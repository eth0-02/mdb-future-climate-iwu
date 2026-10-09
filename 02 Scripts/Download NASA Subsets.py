"""Download and inspect MDB subsets using NASA's NetCDF subset service.

Default: September of the first source year from every available model, SSP and variable.
--all-years downloads the configured source-year range from the audited inventory.
Existing validated files are reused. This does not mix data into the GEE pilot.
"""
import argparse
import calendar
import csv
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
import xarray as xr
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

NETCDF_LOCK = threading.Lock()


def write_json(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def cached_result(row, destination, prior_checks, resume_cutoff):
    """Reuse verified files so a restart does not reread the whole archive."""
    if not destination.is_file() or destination.stat().st_size < 4096:
        return None
    with destination.open('rb') as stream:
        signature = stream.read(8)
    if not (signature.startswith(b'CDF') or signature.startswith(bytes([137, 72, 68, 70]))):
        return None

    previous = prior_checks.get(str(destination))
    if previous and previous.get('SHA256'):
        digest = sha256_file(destination)
        if digest != previous['SHA256']:
            return None
        result = dict(previous)
        result['Status'] = 'Reused; prior NetCDF read and SHA256 verified'
        return result

    if previous and previous.get('Status', '').startswith('Reused; successful prior full read'):
        result = dict(previous)
        result['Status'] = 'Reused; prior full NetCDF read and header verified'
        return result

    modified_utc = datetime.fromtimestamp(destination.stat().st_mtime, timezone.utc)
    if resume_cutoff and modified_utc < resume_cutoff:
        # The prior complete pass read these files successfully. Their old check
        # manifest was replaced on restart; preserve that validation with a header check.
        return {
            'GCM': row['GCM'], 'SSP': row['SSP'], 'Variable': row['Variable'],
            'Calendar Year': int(row['Calendar Year']), 'Version': row['Version'],
            'Status': 'Reused; successful prior full read, NetCDF header present',
            'Days': '', 'First Time': '', 'Last Time': '', 'Calendar': '',
            'Units': '', 'Minimum': '', 'Maximum': '', 'Finite Fraction': '',
            'File': str(destination), 'Source URL': row['URL'],
            'Subset URL': '', 'SHA256': '',
        }
    return None


def fetch(row, root, bounds, all_years, prior_checks, resume_cutoff):
    year = int(row['Calendar Year'])
    variable = row['Variable']
    folder = root / '01 Input Data' / 'NASA Direct Subsets' / row['GCM'] / row['SSP']
    folder.mkdir(parents=True, exist_ok=True)
    period = str(year) if all_years else f'{year} September'
    destination = folder / f'{variable} {period} v{row["Version"]}.nc'
    reusable = cached_result(row, destination, prior_checks, resume_cutoff)
    if reusable:
        return reusable
    source_key = row['URL'].split('amazonaws.com/', 1)[1]
    path = source_key.replace('NEX-GDDP-CMIP6/', 'AMES/NEX/GDDP-CMIP6/', 1)
    url = 'https://ds.nccs.nasa.gov/thredds/ncss/grid/' + path
    west, south, east, north = bounds
    params = dict(var=variable, north=north, south=south, east=east, west=west,
                  horizStride=1, accept='netcdf3', addLatLon='true',
                  time_start=f'{year}-01-01T00:00:00Z' if all_years else f'{year}-09-01T00:00:00Z',
                  time_end=f'{year}-12-31T23:59:59Z' if all_years else f'{year}-09-30T23:59:59Z')
    if all_years:
        # The annual source already defines its calendar; Dec 31 is invalid in 360-day years.
        params.pop('time_start')
        params.pop('time_end')
        params['time'] = 'all'
    candidate = destination
    if destination.exists():
        with destination.open('rb') as stream:
            signature = stream.read(8)
        prior = prior_checks.get(str(destination), {})
        bad_header = destination.stat().st_size < 4096 or not (
            signature.startswith(b'CDF') or signature.startswith(bytes([137, 72, 68, 70])))
        bad_checksum = bool(prior.get('SHA256')) and sha256_file(destination) != prior['SHA256']
        if bad_header or bad_checksum:
            candidate = destination.with_suffix('.partial')
    else:
        candidate = destination.with_suffix('.partial')
    # A partial file is never proof that a previous transfer finished.
    if candidate != destination:
        # Read timeouts and dropped connections are common on long runs; retry the whole file.
        for attempt in range(1, 5):
            try:
                with requests.Session() as client:
                    client.mount('https://', HTTPAdapter(max_retries=Retry(
                        total=1, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
                    with client.get(url, params=params, stream=True, timeout=(20, 90)) as response:
                        response.raise_for_status()
                        with candidate.open('wb') as stream:
                            for chunk in response.iter_content(1024 * 1024):
                                stream.write(chunk)
                break
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(10 * attempt)
    # netCDF4's C library is not thread-safe; keep file reads serial.
    with NETCDF_LOCK:
        with xr.open_dataset(candidate, engine='netcdf4') as raw:
            # NCSS may include the nearest timestamp just outside the requested range.
            keep = raw.time.dt.year == year
            if not all_years:
                keep = keep & (raw.time.dt.month == 9)
            ds = raw.isel(time=np.flatnonzero(keep.values))
            data = ds[variable].load()
            units = data.attrs.get('units', '')
            expected_units = ['K'] if variable != 'pr' else ['kg m-2 s-1', 'kg/m2/s', 'kg m**-2 s**-1']
            if units not in expected_units:
                raise ValueError(f'Unexpected units {units!r}')
            times = ds.time.values
            count = len(times)
            native_calendar = raw.time.encoding.get('calendar', 'standard')
            if all_years:
                days = (360 if native_calendar == '360_day' else
                        365 if native_calendar in ('365_day', 'noleap') else
                        366 if native_calendar in ('366_day', 'all_leap') else
                        365 + calendar.isleap(year))
                if count != days:
                    raise ValueError(f'{year}: {count} records, expected {days} ({native_calendar})')
                for month in range(1, 13):
                    expected = (30 if native_calendar == '360_day' else
                                28 if month == 2 and native_calendar in ('365_day', 'noleap') else
                                29 if month == 2 and native_calendar in ('366_day', 'all_leap') else
                                calendar.monthrange(year, month)[1])
                    actual = ds.time.dt.day.values[ds.time.dt.month.values == month]
                    if sorted(actual.tolist()) != list(range(1, expected + 1)):
                        raise ValueError(f'Incomplete daily coverage: {year}-{month:02d}')
            if len(set(map(str, times))) != count:
                raise ValueError('Duplicate time coordinates')
            finite = np.isfinite(data.values)
            if not finite.any():
                raise ValueError('Subset has no finite data')
            if not all_years and count != 30:
                raise ValueError(f'September has {count} records, expected 30')
            result = {'GCM': row['GCM'], 'SSP': row['SSP'], 'Variable': variable,
                      'Calendar Year': year, 'Version': row['Version'], 'Status': 'Downloaded and read',
                      'Days': count, 'First Time': str(times[0]), 'Last Time': str(times[-1]),
                      'Calendar': raw.time.encoding.get('calendar', 'standard'),
                      'Units': units, 'Minimum': float(np.nanmin(data.values)),
                      'Maximum': float(np.nanmax(data.values)),
                      'Finite Fraction': float(finite.mean()), 'File': str(destination),
                      'Source URL': row['URL'], 'Subset URL': requests.Request('GET', url, params=params).prepare().url,
                      'SHA256': ''}
            if len(raw.time) != count:
                trimmed = destination.with_suffix('.trimmed')
                ds.to_netcdf(trimmed, engine='netcdf4')
            else:
                trimmed = None
        if trimmed is not None:
            trimmed.replace(candidate)
        result['SHA256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if candidate != destination:
        candidate.replace(destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--all-years', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--model')
    parser.add_argument('--year', type=int)
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    config = json.loads((args.root / 'config.json').read_text())
    first_year = int(config['analysis_period']['season_start_year']) - 1
    last_year = int(config['analysis_period']['season_end_year'])
    boundary = gpd.read_file(args.root / config['boundary_file']).to_crs(4326)
    bounds = boundary.total_bounds.tolist()
    audit = args.root / '03 Outputs' / 'Archive Access Audit'
    period_tag = f'{first_year}-{last_year}'
    with (audit / f'NASA File Inventory {period_tag}.csv').open() as stream:
        rows = [r for r in csv.DictReader(stream) if r['URL'] and
                (args.all_years or int(r['Calendar Year']) == first_year)]
    rows = [r for r in rows if (not args.model or r['GCM'] == args.model)
            and (not args.year or int(r['Calendar Year']) == args.year)]
    rows.sort(key=lambda r: (r['GCM'], r['SSP'], int(r['Calendar Year']), r['Variable']))
    if args.limit:
        rows = rows[:args.limit]
    name = f'Full Period Download {period_tag}' if args.all_years else f'MDB Sample Download {period_tag}'
    if args.limit or args.model or args.year:
        name += ' Preflight'
    manifest = audit / f'{name} Checks.csv'
    previous_rows = []
    if manifest.exists():
        with manifest.open(newline='', encoding='utf-8') as stream:
            previous_rows = list(csv.DictReader(stream))
    if args.all_years:
        previous_manifest = audit / 'Full Period Download Checks.csv'
        if previous_manifest.exists():
            with previous_manifest.open(newline='', encoding='utf-8') as stream:
                earlier_rows = list(csv.DictReader(stream))
            current_files = {row.get('File') for row in previous_rows}
            previous_rows.extend(row for row in earlier_rows if row.get('File') not in current_files)
    prior_checks = {r.get('File', ''): r for r in previous_rows if r.get('File')}
    old_summary_path = audit / f'{name} Summary.json'
    resume_cutoff = None
    if args.all_years and old_summary_path.exists():
        try:
            old_summary = json.loads(old_summary_path.read_text(encoding='utf-8'))
            if old_summary.get('started_utc'):
                resume_cutoff = datetime.fromisoformat(old_summary['started_utc'].replace('Z', '+00:00'))
        except (ValueError, OSError):
            pass

    results, errors, pending = [], [], []
    for row in rows:
        period = str(row['Calendar Year']) if args.all_years else f"{row['Calendar Year']} September"
        destination = args.root / '01 Input Data' / 'NASA Direct Subsets' / row['GCM'] / row['SSP'] / f"{row['Variable']} {period} v{row['Version']}.nc"
        reusable = cached_result(row, destination, prior_checks, resume_cutoff)
        if reusable:
            results.append(reusable)
        else:
            pending.append(row)

    started = datetime.now(timezone.utc).isoformat()
    print(f'Reusing {len(results)} verified local files; {len(pending)} require download or full validation.', flush=True)
    def write_manifest():
        if results:
            temporary = manifest.with_suffix('.pending')
            with temporary.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(results[0]))
                writer.writeheader()
                writer.writerows(results)
            temporary.replace(manifest)

    write_manifest()
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        jobs = {pool.submit(fetch, row, args.root, bounds, args.all_years, prior_checks, resume_cutoff): row
                for row in pending}
        for index, future in enumerate(as_completed(jobs), 1):
            row = jobs[future]
            try:
                result = future.result()
                results.append(result)
                print(index, '/', len(jobs), row['GCM'], row['SSP'], row['Variable'], 'OK', flush=True)
            except Exception as exc:
                errors.append(dict(GCM=row['GCM'], SSP=row['SSP'], Variable=row['Variable'],
                                   Year=row['Calendar Year'], Error=str(exc)))
                print(index, '/', len(jobs), 'FAILED', row['GCM'], row['SSP'], row['Variable'], str(exc), flush=True)
            write_manifest()
            write_json(audit / f'{name} Errors.json', errors)
            write_json(audit / f'{name} Summary.json', {
                'requested': len(rows),
                'verified_or_reused': len(results),
                'reused_existing': sum(r['Status'].startswith('Reused;') for r in results),
                'downloaded_and_read_this_pass': sum(r['Status'] == 'Downloaded and read' for r in results),
                'errors': len(errors), 'remaining': len(rows) - len(results) - len(errors),
                'started_utc': started, 'updated_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'Running',
                'scope': f'{first_year}-{last_year} full calendar years' if args.all_years else f'{first_year} September samples'})
    summary = {'requested': len(rows), 'verified_or_reused': len(results),
               'reused_existing': sum(r['Status'].startswith('Reused;') for r in results),
               'downloaded_and_read_this_pass': sum(r['Status'] == 'Downloaded and read' for r in results),
               'errors': len(errors),
               'started_utc': started,
               'updated_utc': datetime.now(timezone.utc).isoformat(),
               'scope': f'{first_year}-{last_year} full calendar years' if args.all_years else f'September {first_year} access samples'}
    summary['status'] = 'Finished with errors' if errors else 'Finished'
    write_json(audit / f'{name} Summary.json', summary)
    print(summary, flush=True)
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
