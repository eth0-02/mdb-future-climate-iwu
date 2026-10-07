"""Process verified NASA annual MDB subsets without replacing the GEE pilot.

Temperature calendars are interpolated within each month. Rainfall is remapped
conservatively so changing the number of days cannot change monthly rainfall.
These are explicit additional assumptions, not observations on invented dates.
"""
from __future__ import annotations

import argparse
import calendar
import importlib.util
import json
import sys
import hashlib
import shutil
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.transform import from_origin


# Folder names keep NASA's scenario codes; every table uses the study's SSP labels.
SSP_LABELS = {'ssp126': 'SSP1-2.6', 'ssp245': 'SSP2-4.5', 'ssp370': 'SSP3-7.0', 'ssp585': 'SSP5-8.5'}
SSP_CODES = {label: code for code, label in SSP_LABELS.items()}


def load_core():
    spec = importlib.util.spec_from_file_location('future_core', Path(__file__).with_name('Future Climate IWU.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def calendar_month(values, target_days, accumulated=False):
    """Map native daily values to calendar days; conserve accumulated amounts."""
    values = np.asarray(values, dtype=np.float64)
    source_days = len(values)
    if source_days == target_days:
        return values.copy()
    if accumulated:
        # Overlap of equal-width day intervals conserves the source monthly total.
        source_edges = np.linspace(0, 1, source_days + 1)
        target_edges = np.linspace(0, 1, target_days + 1)
        overlap = np.maximum(0, np.minimum(target_edges[1:, None], source_edges[None, 1:])
                             - np.maximum(target_edges[:-1, None], source_edges[None, :-1]))
        matrix = overlap * source_days
    else:
        source_centres = (np.arange(source_days) + .5) / source_days
        target_centres = (np.arange(target_days) + .5) / target_days
        matrix = np.stack([np.interp(target_centres, source_centres, column)
                           for column in np.eye(source_days).T], axis=1)
    # Zero weights must not propagate neighbouring NaNs across the entire month.
    result = np.einsum('ts,sij->tij', matrix, np.nan_to_num(values, nan=0))
    missing = np.einsum('ts,sij->tij', (matrix > 0).astype(float), (~np.isfinite(values)).astype(float))
    return np.where(missing > 0, np.nan, result)


def verify_dates(ds, year):
    native = ds.time.encoding.get('calendar', 'standard')
    for month in range(1, 13):
        expected = (30 if native == '360_day' else
                    28 if month == 2 and native in ('noleap', '365_day') else
                    29 if month == 2 and native in ('all_leap', '366_day') else
                    calendar.monthrange(year, month)[1])
        days = ds.time.dt.day.values[ds.time.dt.month.values == month]
        if sorted(days.tolist()) != list(range(1, expected + 1)):
            raise ValueError(f'Incomplete {native} source calendar for {year}-{month:02d}')
    if not np.all(ds.time.dt.year.values == year):
        raise ValueError('Source contains dates outside its declared year')
    return native


def build_area_grid(core, lat, lon, boundary, fraction, reference, expected_area):
    dx, dy = float(np.diff(lon)[0]), float(-np.diff(lat)[0])
    if not np.allclose(np.diff(lon), dx) or not np.allclose(np.diff(lat), -dy):
        raise ValueError('Climate grid is not regular')
    transform = from_origin(lon[0] - dx / 2, lat[0] + dy / 2, dx, dy)
    with rasterio.io.MemoryFile() as memory:
        with memory.open(driver='GTiff', width=len(lon), height=len(lat), count=1,
                         dtype='float32', crs='EPSG:4326', transform=transform) as source:
            grid = core.build_grid(source, boundary)
    if not reference['crs'].is_projected or reference['crs'].to_epsg() != 9473:
        raise ValueError('Expected the documented equal-area EPSG:9473 CSIRO fraction grid')
    boundary_mask = geometry_mask(boundary.to_crs(reference['crs']).geometry,
                                 out_shape=fraction.shape, transform=reference['transform'], invert=True)
    row, col = np.where(boundary_mask & np.isfinite(fraction) & (fraction > 0))
    x, y = rasterio.transform.xy(reference['transform'], row, col)
    longitude, latitude = Transformer.from_crs(reference['crs'], 4326, always_xy=True).transform(x, y)
    climate_col = np.floor((np.asarray(longitude) - transform.c) / dx).astype(int)
    climate_row = np.floor((transform.f - np.asarray(latitude)) / dy).astype(int)
    if not ((climate_col >= 0) & (climate_col < len(lon)) & (climate_row >= 0) & (climate_row < len(lat))).all():
        raise ValueError('Climate subset does not cover the complete irrigated area')
    pixel_area = abs(reference['transform'].determinant) / 1e6
    weights = np.zeros((len(lat), len(lon)))
    np.add.at(weights, (climate_row, climate_col), fraction[row, col] * pixel_area)
    if not np.isclose(weights.sum(), expected_area, rtol=1e-5, atol=.01):
        raise ValueError(f'Boundary-clipped area {weights.sum()} differs from source mean {expected_area}')
    # Fine-pixel centroid assignment conserves area, including coarse boundary cells.
    inside = grid.inside | (weights > 0)
    grid = core.RasterGrid(grid.crs, grid.transform, grid.width, grid.height,
                           inside, grid.cell_area_km2, grid.latitude_radians)
    return grid, weights


def process_year(core, files, model, ssp, year, config, boundary, area_data, output):
    inputs, calendars = {}, []
    lat = lon = times = None
    for variable in ('tasmax', 'tasmin', 'pr'):
        with xr.open_dataset(files[variable]) as ds:
            calendars.append(verify_dates(ds, year))
            ds = ds.sortby('lat', ascending=False).sortby('lon')
            unit = ds[variable].attrs.get('units')
            if unit not in (['K'] if variable != 'pr' else ['kg m-2 s-1', 'kg/m2/s', 'kg m**-2 s**-1']):
                raise ValueError(f'Unexpected {variable} units: {unit}')
            if lat is not None and (not np.array_equal(lat, ds.lat.values) or
                                    not np.array_equal(lon, ds.lon.values) or
                                    not np.array_equal(times, ds.time.values)):
                raise ValueError('Temperature and rainfall grids or timestamps do not match')
            lat, lon, times = ds.lat.values, ds.lon.values, ds.time.values
            months = ds.time.dt.month.values
            inputs[variable] = ds[variable].transpose('time', 'lat', 'lon').values.astype(float)
    if len(set(calendars)) != 1:
        raise ValueError('Variables have different source calendars')
    fraction, reference, _, area = area_data
    grid, weights = build_area_grid(core, lat, lon, boundary, fraction, reference, area)
    irrigated = weights > 0
    path = output / model / ssp
    path.mkdir(parents=True, exist_ok=True)
    ssp = SSP_LABELS.get(ssp, ssp)
    core.write_multiband_geotiff(path / 'Fixed Irrigated Area.tif', [weights], ['Irrigated area km2'], grid)
    rows, daily_rows, daily_arrays, daily_dates = [], [], [], []
    for month in sorted(config['irrigation_months']):
        season = year + (month >= 9)
        if not 2030 <= season <= 2100:
            continue
        native_days = int(np.sum(months == month))
        days = calendar.monthrange(year, month)[1]
        maximum = calendar_month(inputs['tasmax'][months == month], days) - 273.15
        minimum = calendar_month(inputs['tasmin'][months == month], days) - 273.15
        precipitation = inputs['pr'][months == month] * 86400
        rain = calendar_month(precipitation, days, accumulated=True)
        if not all(np.isfinite(v[:, irrigated]).all() for v in (maximum, minimum, rain)):
            raise ValueError(f'Missing climate values over irrigated land in {year}-{month:02d}')
        if np.any(maximum[:, irrigated] < minimum[:, irrigated]):
            raise ValueError('Maximum temperature is below minimum temperature')
        if np.any(rain[:, irrigated] < -1e-6):
            raise ValueError('Negative precipitation exceeds numerical tolerance')
        rain = np.maximum(rain, 0)
        eto_days = []
        for day in range(1, days + 1):
            stamp = date(year, month, day)
            eto, _, _ = core.hargreaves_samani(maximum[day-1], minimum[day-1], grid.latitude_radians,
                                               stamp.timetuple().tm_yday)
            eto = np.where(grid.inside, eto, np.nan)
            eto_days.append(eto)
            daily_dates.append(np.datetime64(stamp))
            daily_arrays.append(np.stack([maximum[day-1], minimum[day-1], eto, rain[day-1]]))
            daily_rows.append({'Date': stamp.isoformat(), 'GCM': model, 'SSP': ssp,
                               'Season Ending Year': season, 'Source Calendar': calendars[0],
                               'Mean ETo mm': core.weighted_mean(eto, weights),
                               'Mean Rainfall mm': core.weighted_mean(rain[day-1], weights)})
        # Ordinary sums deliberately retain missing data rather than treating them as zeros.
        eto = np.sum(eto_days, axis=0)
        rainfall = np.sum(rain, axis=0)
        if not np.allclose(rainfall[irrigated], np.sum(precipitation, axis=0)[irrigated], rtol=1e-10, atol=1e-8):
            raise ValueError('Calendar conversion did not conserve rainfall')
        eta = eto * config['etof'][str(month)]
        pe = core.cropwat_effective_precipitation(rainfall)
        iwr = np.maximum(0, eta - pe)
        arrays = [eto, rainfall, eta, pe, iwr]
        labels = ['ETo mm', 'Rainfall mm', 'ETa mm', 'Effective precipitation mm', 'IWR mm']
        for scenario, multiplier in config['irrigated_area_multipliers'].items():
            volume = np.where(irrigated, iwr * weights * multiplier * .001, 0)
            arrays.append(volume)
            labels.append(f'IWU GL {scenario}')
            record = {'GCM': model, 'SSP': ssp, 'Calendar Year': year, 'Month Number': month,
                      'Month': calendar.month_name[month], 'Season Ending Year': season,
                      'Days': days, 'Source Days': native_days, 'Source Calendar': calendars[0],
                      'Calendar Adjusted': native_days != days, 'EToF': config['etof'][str(month)],
                      'Irrigated Area Scenario': scenario, 'Irrigated Area km2': area * multiplier,
                      'IWU GL': float(volume.sum())}
            for label, values in zip(labels[:5], arrays[:5]):
                record[f'Mean {label}'] = core.weighted_mean(values, weights)
            if not np.isclose(record['IWU GL'], record['Mean IWR mm'] * record['Irrigated Area km2'] * .001):
                raise ValueError('Depth-volume reconciliation failed')
            rows.append(record)
        core.write_multiband_geotiff(path / f'{year}-{month:02d} Monthly Results.tif',
                                    [np.where(grid.inside, a, np.nan) for a in arrays], labels, grid)
    pd.DataFrame(daily_rows).to_csv(path / f'{year} Daily Results.csv', index=False)
    pd.DataFrame(rows).to_csv(path / f'{year} Monthly Results.csv', index=False)
    provenance = {name: {'file': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
                  for name, file in files.items()}
    (path / f'{year} Source Files.json').write_text(json.dumps(provenance, indent=2))
    values = np.stack(daily_arrays)
    values = np.where(grid.inside[None, None], values, np.nan).astype('float32')
    intermediate = xr.Dataset({name: (('time', 'lat', 'lon'), values[:, index])
                               for index, name in enumerate(('tasmax', 'tasmin', 'eto', 'rainfall'))},
                              coords={'time': daily_dates, 'lat': lat, 'lon': lon},
                              attrs={'source_calendar': calendars[0], 'calendar_method': 'Monthly linear temperature interpolation and rainfall-conserving day-interval redistribution',
                                     'gcm': model, 'ssp': ssp})
    for name in ('tasmax', 'tasmin'):
        intermediate[name].attrs['units'] = 'degrees_Celsius'
    for name in ('eto', 'rainfall'):
        intermediate[name].attrs['units'] = 'mm/day'
    intermediate.to_netcdf(path / f'{year} Daily Intermediate.nc',
                           encoding={name: {'zlib': True, 'complevel': 2} for name in intermediate.data_vars})
    return rows, grid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--model')
    parser.add_argument('--year', type=int)
    args = parser.parse_args()
    core = load_core()
    config = core.load_config(args.root / 'config.json')
    boundary = core.load_boundary(args.root / config['boundary_file'])
    area_data = core.load_historical_irrigated_area(args.root / config['csiro_fraction_folder'])
    output = args.root / '03 Outputs' / ('Ensemble ' + datetime.now().strftime('%Y%m%d %H%M%S'))
    output.mkdir(parents=True)
    shutil.copy2(args.root / 'config.json', output / 'Run Configuration.json')
    method_note = args.root / '04 Documentation' / 'Full Ensemble Method.md'
    if method_note.exists():
        shutil.copy2(method_note, output / method_note.name)
    area_data[2].to_csv(output / 'Historical Area.csv', index=False)
    inventory = pd.read_csv(args.root / '03 Outputs' / 'Archive Access Audit' / 'NASA File Inventory.csv').fillna('')
    rows, gaps, grid = [], [], None
    for (model, ssp, year), group in inventory.groupby(['GCM', 'SSP', 'Calendar Year'], sort=True):
        year = int(year)
        if (args.model and model != args.model) or (args.year and year != args.year):
            continue
        files = {}
        for _, record in group.iterrows():
            filename = f'{record["Variable"]} {year} v{record["Version"]}.nc'
            files[record['Variable']] = args.root / '01 Input Data' / 'NASA Direct Subsets' / model / ssp / filename
        missing = [v for v in ('tasmax', 'tasmin', 'pr') if v not in files or not files[v].exists()]
        if missing:
            gaps.append({'GCM': model, 'SSP': SSP_LABELS.get(ssp, ssp), 'Year': year,
                         'Reason': 'Missing ' + ', '.join(missing)})
            continue
        try:
            records, grid = process_year(core, files, model, ssp, year, config, boundary, area_data, output)
            rows.extend(records)
            print(f'Processed {model} {ssp} {year}', flush=True)
        except Exception as exc:
            gaps.append({'GCM': model, 'SSP': SSP_LABELS.get(ssp, ssp), 'Year': year, 'Reason': str(exc)})
            print(f'FAILED {model} {ssp} {year}: {exc}', flush=True)
    pd.DataFrame(gaps, columns=['GCM', 'SSP', 'Year', 'Reason']).to_csv(output / 'Missing Data Log.csv', index=False)
    monthly = pd.DataFrame(rows)
    requested_gcms = list(config['requested_gcms'])
    complete_monthly_per_gcm = 4 * 71 * 8 * 3
    complete_seasonal_per_gcm = 4 * 71 * 3
    coverage_note = ('Eight of nine requested GCMs are candidates for complete IWU estimates; '
                     'CESM2 is excluded because the audited archive has no required tasmax/tasmin.')
    monthly['Ensemble GCM Coverage'] = coverage_note
    monthly['Complete Nine GCM Ensemble'] = False
    monthly.to_csv(output / 'Monthly Results.csv', index=False)
    seasonal = []
    if len(monthly):
        keys = ['GCM', 'SSP', 'Season Ending Year', 'Irrigated Area Scenario']
        for identity, group in monthly.groupby(keys):
            if len(group) != 8 or set(group['Month Number']) != set(config['irrigation_months']):
                continue
            model, ssp, ending, scenario = identity
            record = dict(zip(keys, identity))
            record.update({'Season Start': f'{ending-1}-09-01', 'Season End': f'{ending}-04-30',
                           'Irrigated Area km2': group['Irrigated Area km2'].iloc[0],
                           'Days': int(group['Days'].sum())})
            for column in ['IWU GL'] + [c for c in monthly if c.startswith('Mean ')]:
                record['Seasonal ' + column.removeprefix('Mean ')] = group[column].sum()
            seasonal.append(record)
        # Each raster contains all three area scenarios, so write one per model/SSP/season.
        for identity in sorted({(r['GCM'], r['SSP'], r['Season Ending Year']) for r in seasonal}):
            model, ssp, ending = identity
            folder = output / model / SSP_CODES.get(ssp, ssp)
            arrays = []
            for year, month in core.season_months(ending):
                with rasterio.open(folder / f'{year}-{month:02d} Monthly Results.tif') as source:
                    arrays.append(source.read(masked=True).filled(np.nan))
                    labels = list(source.descriptions)
                    season_grid = core.build_grid(source, boundary)
            core.write_multiband_geotiff(folder / f'Season {ending} Results.tif',
                                        list(np.sum(arrays, axis=0)), labels, season_grid)
    season_columns = ['GCM', 'SSP', 'Season Ending Year', 'Irrigated Area Scenario',
                      'Season Start', 'Season End', 'Irrigated Area km2', 'Days',
                      'Seasonal IWU GL', 'Seasonal ETo mm', 'Seasonal Rainfall mm',
                      'Seasonal ETa mm', 'Seasonal Effective precipitation mm', 'Seasonal IWR mm']
    seasonal_table = pd.DataFrame(seasonal, columns=season_columns)
    seasonal_table['Ensemble GCM Coverage'] = coverage_note
    seasonal_table['Complete Nine GCM Ensemble'] = False
    seasonal_table.to_csv(output / 'Seasonal Results.csv', index=False)
    complete_gcms = []
    coverage_rows = []
    for model in requested_gcms:
        model_months = int((monthly['GCM'] == model).sum()) if len(monthly) else 0
        model_seasons = int((seasonal_table['GCM'] == model).sum())
        if model == 'CESM2':
            status = 'Excluded from IWU: tasmax and tasmin unavailable'
        elif model_months == complete_monthly_per_gcm and model_seasons == complete_seasonal_per_gcm:
            status = 'Complete'
            complete_gcms.append(model)
        elif model_months or model_seasons:
            status = 'Incomplete: check missing-data log and row counts'
        else:
            status = 'Not processed: check download and missing-data logs'
        coverage_rows.append({'GCM': model, 'Status': status,
                              'Monthly Records': model_months,
                              'Expected Monthly Records': complete_monthly_per_gcm,
                              'Seasonal Records': model_seasons,
                              'Expected Seasonal Records': complete_seasonal_per_gcm})
    pd.DataFrame(coverage_rows).to_csv(output / 'GCM Coverage.csv', index=False)
    expected_monthly = complete_monthly_per_gcm * 8
    expected_seasonal = complete_seasonal_per_gcm * 8
    available_models_complete = (len(complete_gcms) == 8 and len(rows) == expected_monthly
                                 and len(seasonal) == expected_seasonal)
    full_run = not args.model and not args.year
    status = ('Complete available ensemble (8 of 9 requested GCMs; CESM2 excluded)' if available_models_complete
              else 'Incomplete processing; do not report as a complete available ensemble')
    summary = {'run_status': status, 'monthly_records': len(rows), 'seasonal_records': len(seasonal),
               'requested_gcm_count': len(requested_gcms), 'complete_gcm_count': len(complete_gcms),
               'complete_gcms': complete_gcms,
               'excluded_gcms': {'CESM2': 'tasmax and tasmin are unavailable in the audited NASA archive'},
               'is_complete_available_ensemble': available_models_complete,
               'is_complete_nine_gcm_ensemble': False,
               'expected_full_monthly_records_available_gcms': expected_monthly,
               'expected_full_seasonal_records_available_gcms': expected_seasonal,
               'missing_or_failed_model_years': len(gaps), 'output': str(output),
               'coverage_note': coverage_note}
    (output / 'Run Summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    if full_run and not available_models_complete:
        return 2
    return 0 if rows else 1


if __name__ == '__main__':
    raise SystemExit(main())
