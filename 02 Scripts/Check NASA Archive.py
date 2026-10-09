"""Inventory public NASA files and verify byte access without global downloads."""
import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = 'https://nex-gddp-cmip6.s3.us-west-2.amazonaws.com/'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}


def session():
    client = requests.Session()
    client.mount('https://', HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
    return client


def listing(client, prefix, delimiter=None):
    params = {'list-type': '2', 'prefix': prefix, 'max-keys': 1000}
    if delimiter:
        params['delimiter'] = delimiter
    objects, prefixes = [], []
    while True:
        response = client.get(BASE, params=params, timeout=60)
        response.raise_for_status()
        tree = ET.fromstring(response.content)
        prefixes.extend(n.text for n in tree.findall('s:CommonPrefixes/s:Prefix', NS))
        for item in tree.findall('s:Contents', NS):
            objects.append({
                'key': item.findtext('s:Key', namespaces=NS),
                'bytes': int(item.findtext('s:Size', namespaces=NS)),
                'modified': item.findtext('s:LastModified', namespaces=NS),
                'etag': item.findtext('s:ETag', namespaces=NS),
            })
        token = tree.findtext('s:NextContinuationToken', namespaces=NS)
        if not token:
            return objects, prefixes
        params['continuation-token'] = token


def inspect_combination(model, scenario, first_year, last_year):
    client = session()
    rows, checks = [], []
    _, variants = listing(client, f'NEX-GDDP-CMIP6/{model}/{scenario}/', '/')
    if len(variants) != 1:
        raise ValueError(f'{model} {scenario}: expected one variant, found {variants}')
    for variable in ['tasmax', 'tasmin', 'pr']:
        objects, _ = listing(client, variants[0] + variable + '/')
        selected = {}
        for obj in objects:
            match = re.search(r'_(\d{4})(?:_v(\d+)\.(\d+))?\.nc$', obj['key'])
            if not match:
                continue
            year = int(match[1])
            if not first_year <= year <= last_year:
                continue
            version = (int(match[2] or 1), int(match[3] or 0))
            if year not in selected or version > selected[year][0]:
                selected[year] = (version, obj)
        missing = sorted(set(range(first_year, last_year + 1)) - set(selected))
        samples = []
        for year in [first_year, last_year]:
            if year not in selected:
                continue
            url = BASE + selected[year][1]['key']
            with client.get(url, headers={'Range': 'bytes=0-7'}, stream=True, timeout=60) as response:
                response.raise_for_status()
                signature = response.raw.read(8)
                valid = signature.startswith(b'\x89HDF') or signature.startswith(b'CDF')
                if not valid:
                    raise ValueError(f'Unexpected NetCDF signature: {url}')
                samples.append(f'{year}: HTTP {response.status_code}, NetCDF signature verified')
        for year in range(first_year, last_year + 1):
            entry = selected.get(year)
            rows.append({
                'GCM': model, 'SSP': scenario, 'Variable': variable, 'Calendar Year': year,
                'Status': 'Listed in NASA archive' if entry else 'Missing in NASA archive',
                'Version': '.'.join(map(str, entry[0])) if entry else '',
                'Bytes': entry[1]['bytes'] if entry else '',
                'URL': BASE + entry[1]['key'] if entry else '',
                'ETag': entry[1]['etag'] if entry else '',
                'Downloaded': False,
            })
        checks.append({'GCM': model, 'SSP': scenario, 'Variable': variable,
                       'Available Years': len(selected), 'Expected Years': last_year - first_year + 1,
                       'Missing Years': ', '.join(map(str, missing)),
                       'Access Test': '; '.join(samples) or 'No files to test'})
    client.close()
    return rows, checks


def save_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.root / 'config.json').read_text())
    first_year = int(config['analysis_period']['season_start_year']) - 1
    last_year = int(config['analysis_period']['season_end_year'])
    output = args.root / '03 Outputs' / 'Archive Access Audit'
    output.mkdir(parents=True, exist_ok=True)
    period_tag = f'{first_year}-{last_year}'
    rows, checks, errors = [], [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(inspect_combination, model, scenario, first_year, last_year): (model, scenario)
                for model in config['requested_gcms']
                for scenario in ['ssp126', 'ssp245', 'ssp370', 'ssp585']}
        for job in as_completed(jobs):
            model, scenario = jobs[job]
            try:
                records, results = job.result()
                rows.extend(records)
                checks.extend(results)
                print(model, scenario, [(r['Variable'], r['Available Years']) for r in results], flush=True)
            except Exception as exc:
                errors.append({'GCM': model, 'SSP': scenario, 'Error': str(exc)})
                print('FAILED', model, scenario, str(exc), flush=True)
    if rows:
        save_csv(output / f'NASA File Inventory {period_tag}.csv', sorted(rows, key=lambda r: (r['GCM'], r['SSP'], r['Variable'], r['Calendar Year'])))
    if checks:
        save_csv(output / f'NASA Access Checks {period_tag}.csv', checks)
    (output / f'Access Errors {period_tag}.json').write_text(json.dumps(errors, indent=2))
    summary = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'combinations_requested': len(config['requested_gcms']) * 4,
               'variable_checks_completed': len(checks),
               'expected_files': len(config['requested_gcms']) * 4 * 3 * (last_year - first_year + 1),
               'files_listed': sum(r['Status'] == 'Listed in NASA archive' for r in rows),
               'missing_files': sum(r['Status'] != 'Listed in NASA archive' for r in rows),
               'errors': len(errors), 'full_files_downloaded_by_this_audit': 0}
    (output / f'Audit Summary {period_tag}.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
