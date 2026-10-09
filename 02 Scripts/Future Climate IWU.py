"""Future climate irrigation water-use pilot for the Murray-Darling Basin.

The first run is deliberately limited to one GCM, one SSP and one irrigation
season. The same checked functions can later be iterated across the supported
ensemble without changing the scientific logic.
"""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Geod, Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.warp import reproject

try:
    import ee
except ImportError:  # A clearer error is raised before any data work starts.
    ee = None


NODATA = -9999.0
SSP_TO_EE = {
    "SSP1-2.6": None,
    "SSP2-4.5": "ssp245",
    "SSP3-7.0": None,
    "SSP5-8.5": "ssp585",
}
REQUIRED_BANDS = ("tasmax", "tasmin", "pr")
MONTH_NAMES = {month: calendar.month_name[month] for month in range(1, 13)}


@dataclass(frozen=True)
class RasterGrid:
    crs: object
    transform: object
    width: int
    height: int
    inside: np.ndarray
    cell_area_km2: np.ndarray
    latitude_radians: np.ndarray


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    config["_root"] = path.resolve().parent
    return config


def resolve_path(config: dict, value: str) -> Path:
    return (Path(config["_root"]) / value).resolve()


def prepare_output_folders(root: Path) -> dict[str, Path]:
    folders = {
        "daily": root / "03 Outputs" / "Daily",
        "monthly": root / "03 Outputs" / "Monthly",
        "seasonal": root / "03 Outputs" / "Seasonal",
        "quality": root / "03 Outputs" / "Quality Control",
        "logs": root / "03 Outputs" / "Logs",
        "downloads": root / "03 Outputs" / "Downloads",
    }
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


def configure_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("future_iwu")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def season_dates(ending_year: int) -> tuple[date, date]:
    """Return the inclusive September-April season labelled by ending year."""
    return date(ending_year - 1, 9, 1), date(ending_year, 4, 30)


def iter_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def season_months(ending_year: int) -> list[tuple[int, int]]:
    return [
        (ending_year - 1, 9),
        (ending_year - 1, 10),
        (ending_year - 1, 11),
        (ending_year - 1, 12),
        (ending_year, 1),
        (ending_year, 2),
        (ending_year, 3),
        (ending_year, 4),
    ]


def extraterrestrial_radiation(
    latitude_radians: np.ndarray, day_of_year: int
) -> tuple[np.ndarray, np.ndarray]:
    """Calculate FAO extraterrestrial radiation in MJ m-2 day-1 and mm day-1."""
    inverse_distance = 1.0 + 0.033 * np.cos(2.0 * np.pi * day_of_year / 365.0)
    declination = 0.409 * np.sin(2.0 * np.pi * day_of_year / 365.0 - 1.39)
    acos_argument = -np.tan(latitude_radians) * np.tan(declination)
    sunset_angle = np.arccos(np.clip(acos_argument, -1.0, 1.0))
    radiation_mj = (
        (24.0 * 60.0 / np.pi)
        * 0.0820
        * inverse_distance
        * (
            sunset_angle * np.sin(latitude_radians) * np.sin(declination)
            + np.cos(latitude_radians) * np.cos(declination) * np.sin(sunset_angle)
        )
    )
    radiation_mj = np.maximum(radiation_mj, 0.0)
    return radiation_mj, radiation_mj * 0.408


def hargreaves_samani(
    tmax_c: np.ndarray,
    tmin_c: np.ndarray,
    latitude_radians: np.ndarray,
    day_of_year: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return daily ETo and both forms of extraterrestrial radiation."""
    radiation_mj, radiation_mm = extraterrestrial_radiation(latitude_radians, day_of_year)
    mean_temperature = (tmax_c + tmin_c) / 2.0
    temperature_range = np.maximum(tmax_c - tmin_c, 0.0)
    eto = 0.0023 * radiation_mm * (mean_temperature + 17.8) * np.sqrt(temperature_range)
    return np.maximum(eto, 0.0), radiation_mj, radiation_mm


def precipitation_to_mm_day(pr_kg_m2_s: np.ndarray) -> np.ndarray:
    """Convert kg m-2 s-1 to daily water depth in millimetres."""
    return np.maximum(pr_kg_m2_s * 86400.0, 0.0)


def cropwat_effective_precipitation(monthly_rain_mm: np.ndarray) -> np.ndarray:
    """Apply the monthly FAO CROPWAT effective-precipitation equation."""
    rainfall = np.maximum(monthly_rain_mm, 0.0)
    return np.where(
        rainfall <= 250.0,
        rainfall * (125.0 - 0.2 * rainfall) / 125.0,
        125.0 + 0.1 * rainfall,
    )


def initialise_earth_engine(project: str) -> None:
    if ee is None:
        raise RuntimeError("earthengine-api is not installed. Run Run Pilot.bat first.")
    try:
        ee.Initialize(project=project)
    except Exception as exc:
        raise RuntimeError(
            "Google Earth Engine is not authenticated. Run the authentication step in "
            "Run Pilot.bat, then rerun the pilot."
        ) from exc


def earth_engine_collection(
    dataset_id: str,
    model: str,
    scenario: str,
    start: date,
    end_exclusive: date,
):
    ee_scenario = SSP_TO_EE.get(scenario)
    if ee_scenario is None:
        raise ValueError(
            f"{scenario} is not exposed by the Earth Engine NASA/GDDP-CMIP6 catalog. "
            "Use another official NEX-GDDP-CMIP6 access route for this SSP."
        )
    return (
        ee.ImageCollection(dataset_id)
        .filter(ee.Filter.eq("model", model))
        .filter(ee.Filter.eq("scenario", ee_scenario))
        .filterDate(start.isoformat(), end_exclusive.isoformat())
        .sort("system:time_start")
    )


def check_selected_data(
    dataset_id: str,
    model: str,
    scenario: str,
    start: date,
    end: date,
) -> dict:
    collection = earth_engine_collection(
        dataset_id, model, scenario, start, end + timedelta(days=1)
    )
    count = int(collection.size().getInfo())
    if count == 0:
        return {
            "status": "Unavailable",
            "images": 0,
            "bands": "",
            "comment": "No matching images",
        }
    bands = collection.first().bandNames().getInfo()
    missing = [band for band in REQUIRED_BANDS if band not in bands]
    return {
        "status": "Available" if not missing else "Unavailable",
        "images": count,
        "bands": ", ".join(bands),
        "comment": "All required bands present"
        if not missing
        else f"Missing bands: {', '.join(missing)}",
    }


def requested_source_inventory(config: dict, selected_check: dict | None = None) -> pd.DataFrame:
    rows = []
    selected = config["pilot"]
    for model in config["requested_gcms"]:
        for ssp in config["requested_ssps"]:
            ee_scenario = SSP_TO_EE.get(ssp)
            if ee_scenario is None:
                status = "External source required"
                comment = "This SSP is not exposed in the Earth Engine NASA/GDDP-CMIP6 collection."
            elif model == "CESM2":
                status = "Unavailable for Hargreaves in GEE"
                comment = "The Earth Engine catalog lacks the required CESM2 temperature bands."
            else:
                status = "Candidate in Earth Engine"
                comment = "Run a dated preflight before processing."
            if model == selected["gcm"] and ssp == selected["ssp"] and selected_check:
                status = selected_check["status"]
                comment = selected_check["comment"]
            rows.append(
                {
                    "GCM": model,
                    "SSP": ssp,
                    "Earth Engine Scenario": ee_scenario or "Not available",
                    "Status": status,
                    "Comment": comment,
                }
            )
    return pd.DataFrame(rows)


def load_boundary(boundary_file: Path) -> gpd.GeoDataFrame:
    required = [
        boundary_file.with_suffix(extension)
        for extension in (".shp", ".shx", ".dbf", ".prj")
    ]
    missing = [path.name for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"MDB boundary is incomplete. Missing: {', '.join(missing)}")
    boundary = gpd.read_file(boundary_file)
    if boundary.empty or boundary.crs is None:
        raise ValueError("MDB boundary is empty or has no coordinate reference system.")
    return boundary


def load_historical_irrigated_area(
    folder: Path,
) -> tuple[np.ndarray, dict, pd.DataFrame, float]:
    files = sorted(folder.glob("MDB CSIRO WY* Irrigated Fraction.tif"))
    if not files:
        raise FileNotFoundError(f"No CSIRO irrigated-fraction GeoTIFFs were found in {folder}")
    arrays = []
    records = []
    reference = None
    for raster_file in files:
        with rasterio.open(raster_file) as source:
            array = source.read(1, masked=True).astype("float64")
            if reference is None:
                reference = {
                    "crs": source.crs,
                    "transform": source.transform,
                    "width": source.width,
                    "height": source.height,
                }
            elif (
                source.crs != reference["crs"]
                or source.transform != reference["transform"]
                or source.width != reference["width"]
                or source.height != reference["height"]
            ):
                raise ValueError(f"CSIRO fraction raster grid differs: {raster_file.name}")
            clean = np.ma.filled(array, np.nan)
            if np.nanmin(clean) < -1e-6 or np.nanmax(clean) > 1.0 + 1e-6:
                raise ValueError(f"Irrigated fractions are outside 0-1 in {raster_file.name}")
            pixel_area_km2 = abs(source.transform.a * source.transform.e) / 1_000_000.0
            area = float(np.nansum(clean * pixel_area_km2))
            match = re.search(r"WY(\d{4})", raster_file.name)
            records.append(
                {
                    "Water Year": f"WY{match.group(1)}" if match else raster_file.stem,
                    "Area km2": area,
                }
            )
            arrays.append(clean)
    stack = np.stack(arrays)
    valid_count = np.sum(np.isfinite(stack), axis=0)
    mean_fraction = np.divide(
        np.nansum(stack, axis=0),
        valid_count,
        out=np.full(stack.shape[1:], np.nan, dtype="float64"),
        where=valid_count > 0,
    )
    annual_table = pd.DataFrame(records)
    historical_mean_area = float(annual_table["Area km2"].mean())
    return mean_fraction, reference, annual_table, historical_mean_area


def download_month_stack(
    collection,
    year: int,
    month: int,
    bounds_wgs84: tuple[float, float, float, float],
    scale_metres: int,
    destination: Path,
    logger: logging.Logger,
) -> tuple[Path, list[str]]:
    start = date(year, month, 1)
    end = date(year + int(month == 12), 1 if month == 12 else month + 1, 1)
    month_collection = collection.filterDate(start.isoformat(), end.isoformat())

    def rename_bands(image):
        stamp = ee.Date(image.get("system:time_start")).format("YYYYMMdd")
        names = [ee.String(band).cat("_").cat(stamp) for band in REQUIRED_BANDS]
        return image.select(list(REQUIRED_BANDS)).rename(names).toFloat()

    stack = month_collection.map(rename_bands).toBands()
    band_names = stack.bandNames().getInfo()
    destination.parent.mkdir(parents=True, exist_ok=True)
    sidecar = destination.with_suffix(".bands.json")
    if destination.exists() and sidecar.exists():
        with sidecar.open("r", encoding="utf-8") as stream:
            return destination, json.load(stream)

    west, south, east, north = bounds_wgs84
    region = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    url = stack.getDownloadURL(
        {
            "name": destination.stem,
            "region": region,
            "scale": scale_metres,
            "crs": "EPSG:4326",
            "format": "GEO_TIFF",
            "filePerBand": False,
        }
    )
    logger.info("Downloading %s %s (%s bands)", MONTH_NAMES[month], year, len(band_names))
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    temporary = destination.with_suffix(".download")
    temporary.write_bytes(response.content)
    if zipfile.is_zipfile(temporary):
        extract_folder = destination.parent / f"{destination.stem} extracted"
        extract_folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(temporary) as archive:
            archive.extractall(extract_folder)
        candidates = list(extract_folder.rglob("*.tif")) + list(
            extract_folder.rglob("*.tiff")
        )
        if len(candidates) != 1:
            raise RuntimeError(
                f"Expected one multiband GeoTIFF, received {len(candidates)} files"
            )
        shutil.move(str(candidates[0]), destination)
        shutil.rmtree(extract_folder)
        temporary.unlink(missing_ok=True)
    else:
        temporary.replace(destination)
    with sidecar.open("w", encoding="utf-8") as stream:
        json.dump(band_names, stream, indent=2)
    return destination, band_names


def parse_band_name(name: str) -> tuple[str, date]:
    variable_match = re.search(r"(tasmax|tasmin|pr)", name)
    date_matches = re.findall(r"(20\d{6})", name)
    if not variable_match or not date_matches:
        raise ValueError(f"Could not parse Earth Engine band name: {name}")
    stamp = date_matches[-1]
    return variable_match.group(1), date(
        int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8])
    )


def build_grid(source: rasterio.DatasetReader, boundary: gpd.GeoDataFrame) -> RasterGrid:
    boundary_on_grid = boundary.to_crs(source.crs)
    inside = geometry_mask(
        boundary_on_grid.geometry,
        out_shape=(source.height, source.width),
        transform=source.transform,
        invert=True,
        all_touched=False,
    )
    rows, columns = np.indices((source.height, source.width))
    xs, ys = rasterio.transform.xy(source.transform, rows, columns, offset="center")
    shape = (source.height, source.width)
    x_array = np.asarray(xs, dtype="float64").reshape(shape)
    y_array = np.asarray(ys, dtype="float64").reshape(shape)
    transformer = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
    _, latitudes = transformer.transform(x_array, y_array)
    latitude_radians = np.deg2rad(latitudes)

    if source.crs.is_projected:
        cell_area_km2 = np.full(
            (source.height, source.width),
            abs(source.transform.a * source.transform.e) / 1e6,
        )
    else:
        geod = Geod(ellps="WGS84")
        cell_area_km2 = np.zeros((source.height, source.width), dtype="float64")
        for row in range(source.height):
            for column in range(source.width):
                transform = source.transform @ rasterio.Affine.translation(column, row)
                left, bottom, right, top = rasterio.transform.array_bounds(1, 1, transform)
                area_m2, _ = geod.polygon_area_perimeter(
                    [left, right, right, left], [bottom, bottom, top, top]
                )
                cell_area_km2[row, column] = abs(area_m2) / 1e6
    return RasterGrid(
        source.crs,
        source.transform,
        source.width,
        source.height,
        inside,
        cell_area_km2,
        latitude_radians,
    )


def align_irrigated_area(
    mean_fraction: np.ndarray,
    source_reference: dict,
    target_grid: RasterGrid,
    historical_mean_area_km2: float,
) -> tuple[np.ndarray, np.ndarray]:
    aligned = np.full((target_grid.height, target_grid.width), np.nan, dtype="float64")
    reproject(
        source=mean_fraction,
        destination=aligned,
        src_transform=source_reference["transform"],
        src_crs=source_reference["crs"],
        src_nodata=np.nan,
        dst_transform=target_grid.transform,
        dst_crs=target_grid.crs,
        dst_nodata=np.nan,
        resampling=Resampling.average,
    )
    aligned = np.where(target_grid.inside, np.clip(aligned, 0.0, 1.0), np.nan)
    raw_area = aligned * target_grid.cell_area_km2
    raw_total = float(np.nansum(raw_area))
    if raw_total <= 0:
        raise ValueError("The CSIRO irrigated area does not overlap the climate grid.")
    area_weights = raw_area * (historical_mean_area_km2 / raw_total)
    return aligned, area_weights


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    valid = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not valid.any():
        return float("nan")
    return float(np.sum(values[valid] * weights[valid]) / np.sum(weights[valid]))


def catchment_mean(values: np.ndarray, grid: RasterGrid) -> float:
    weights = np.where(grid.inside, grid.cell_area_km2, np.nan)
    return weighted_mean(values, weights)


def write_multiband_geotiff(
    path: Path,
    arrays: list[np.ndarray],
    band_names: list[str],
    grid: RasterGrid,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": len(arrays),
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": NODATA,
        "compress": "deflate",
        "predictor": 3,
        "tiled": True,
    }
    with rasterio.open(path, "w", **profile) as destination:
        for index, (array, name) in enumerate(zip(arrays, band_names), start=1):
            destination.write(
                np.where(np.isfinite(array), array, NODATA).astype("float32"), index
            )
            destination.set_band_description(index, name)
        destination.update_tags(
            SOURCE="NASA/GDDP-CMIP6 and CSIRO irrigated area",
            IRRIGATION_SEASON="September-April",
            METHODS=(
                "Daily Hargreaves-Samani ETo; monthly FAO CROPWAT "
                "effective precipitation"
            ),
        )


def run_pilot(
    config: dict,
    folders: dict[str, Path],
    logger: logging.Logger,
    check_only: bool = False,
) -> None:
    pilot = config["pilot"]
    model = pilot["gcm"]
    ssp = pilot["ssp"]
    ending_year = int(pilot["season_ending_year"])
    first_season = int(config.get("analysis_period", {}).get("season_start_year", 2025))
    last_season = int(config.get("analysis_period", {}).get("season_end_year", 2100))
    if not first_season <= ending_year <= last_season:
        raise ValueError(
            f"Pilot season ending year must be between {first_season} and {last_season}."
        )
    start, end = season_dates(ending_year)
    expected_dates = list(iter_dates(start, end))

    # Availability is checked before large downloads so unsupported combinations fail cleanly.
    earth_engine_project = (
        os.environ.get("EARTH_ENGINE_PROJECT")
        or config.get("earth_engine_project", "")
    )
    if not earth_engine_project:
        raise ValueError(
            "Set EARTH_ENGINE_PROJECT or add your own Earth Engine project ID "
            "to config.json before running the pilot."
        )
    initialise_earth_engine(earth_engine_project)
    selected_check = check_selected_data(
        config["earth_engine_collection"], model, ssp, start, end
    )
    requested_source_inventory(config, selected_check).to_csv(
        folders["quality"] / "CMIP6 Data Availability.csv", index=False
    )
    logger.info("Selected source check: %s", selected_check)
    if selected_check["status"] != "Available":
        raise RuntimeError(f"Selected climate source is unavailable: {selected_check['comment']}")
    if selected_check["images"] != len(expected_dates):
        raise RuntimeError(
            f"Expected {len(expected_dates)} daily images but Earth Engine returned "
            f"{selected_check['images']}."
        )
    if check_only:
        logger.info("Data access check completed; no downloads were requested.")
        return

    boundary = load_boundary(resolve_path(config, config["boundary_file"]))
    boundary_wgs84 = boundary.to_crs("EPSG:4326")
    bounds = tuple(boundary_wgs84.total_bounds.tolist())
    mean_fraction, source_reference, area_table, historical_mean_area = (
        load_historical_irrigated_area(
            resolve_path(config, config["csiro_fraction_folder"])
        )
    )
    area_table.loc[len(area_table)] = {
        "Water Year": "Historical mean",
        "Area km2": historical_mean_area,
    }
    area_table.to_csv(
        folders["quality"] / "CSIRO Irrigated Area Summary.csv", index=False
    )
    logger.info("Fixed historical mean CSIRO irrigated area: %.3f km2", historical_mean_area)

    collection = earth_engine_collection(
        config["earth_engine_collection"], model, ssp, start, end + timedelta(days=1)
    )
    month_files = []
    for year, month in season_months(ending_year):
        file_name = f"{model} {ssp} {year}-{month:02d} Daily Climate Stack.tif"
        month_files.append(
            download_month_stack(
                collection,
                year,
                month,
                bounds,
                int(config["download_scale_metres"]),
                folders["downloads"] / file_name,
                logger,
            )
        )

    daily_inputs: dict[date, dict[str, np.ndarray]] = {}
    grid = None
    climate_profile = None
    for stack_file, band_names in month_files:
        with rasterio.open(stack_file) as source:
            if grid is None:
                grid = build_grid(source, boundary)
                climate_profile = (
                    source.crs,
                    source.transform,
                    source.width,
                    source.height,
                )
            elif climate_profile != (
                source.crs,
                source.transform,
                source.width,
                source.height,
            ):
                raise ValueError(
                    f"Earth Engine grid changed between downloads: {stack_file.name}"
                )
            if source.count != len(band_names):
                raise ValueError(f"Band sidecar does not match {stack_file.name}")
            for index, band_name in enumerate(band_names, start=1):
                variable, observation_date = parse_band_name(band_name)
                band = source.read(index, masked=True).astype("float64")
                daily_inputs.setdefault(observation_date, {})[variable] = np.ma.filled(
                    band, np.nan
                )

    assert grid is not None
    aligned_fraction, baseline_area_weights = align_irrigated_area(
        mean_fraction, source_reference, grid, historical_mean_area
    )
    write_multiband_geotiff(
        folders["quality"] / "Fixed Historical Mean Irrigated Fraction.tif",
        [aligned_fraction, baseline_area_weights],
        ["Irrigated fraction", "Irrigated area km2"],
        grid,
    )

    coverage_rows = []
    for expected in expected_dates:
        variables = daily_inputs.get(expected, {})
        missing = [band for band in REQUIRED_BANDS if band not in variables]
        coverage_rows.append(
            {
                "Date": expected.isoformat(),
                "GCM": model,
                "SSP": ssp,
                "Required bands present": not missing,
                "Missing bands": ", ".join(missing),
            }
        )
    coverage = pd.DataFrame(coverage_rows)
    coverage.to_csv(
        folders["quality"] / "Daily Climate Coverage.csv", index=False
    )
    if not coverage["Required bands present"].all():
        raise RuntimeError(
            "Daily climate coverage is incomplete. See Daily Climate Coverage.csv."
        )

    daily_rows = []
    monthly_arrays: dict[tuple[int, int], dict[str, list[np.ndarray]]] = {}
    for observation_date in expected_dates:
        variables = daily_inputs[observation_date]
        tmax_c = variables["tasmax"] - 273.15
        tmin_c = variables["tasmin"] - 273.15
        rain_mm = precipitation_to_mm_day(variables["pr"])
        eto_mm, radiation_mj, radiation_mm = hargreaves_samani(
            tmax_c,
            tmin_c,
            grid.latitude_radians,
            observation_date.timetuple().tm_yday,
        )
        valid = (
            grid.inside
            & np.isfinite(tmax_c)
            & np.isfinite(tmin_c)
            & np.isfinite(rain_mm)
        )
        tmax_c = np.where(valid, tmax_c, np.nan)
        tmin_c = np.where(valid, tmin_c, np.nan)
        rain_mm = np.where(valid, rain_mm, np.nan)
        eto_mm = np.where(valid, eto_mm, np.nan)
        radiation_mj = np.where(valid, radiation_mj, np.nan)
        radiation_mm = np.where(valid, radiation_mm, np.nan)
        key = (observation_date.year, observation_date.month)
        bucket = monthly_arrays.setdefault(key, {"eto": [], "rain": []})
        bucket["eto"].append(eto_mm)
        bucket["rain"].append(rain_mm)
        daily_rows.append(
            {
                "Date": observation_date.isoformat(),
                "GCM": model,
                "SSP": ssp,
                "Season Ending Year": ending_year,
                "Calendar Year": observation_date.year,
                "Month": MONTH_NAMES[observation_date.month],
                "Day of Year": observation_date.timetuple().tm_yday,
                "Mean Tmax C": catchment_mean(tmax_c, grid),
                "Mean Tmin C": catchment_mean(tmin_c, grid),
                "Mean Extraterrestrial Radiation MJ m-2 day-1": catchment_mean(
                    radiation_mj, grid
                ),
                "Mean Extraterrestrial Radiation mm day-1": catchment_mean(
                    radiation_mm, grid
                ),
                "Mean ETo mm day-1": catchment_mean(eto_mm, grid),
                "Mean Rainfall mm day-1": catchment_mean(rain_mm, grid),
            }
        )
        if config.get("write_daily_geotiffs", True):
            daily_name = (
                f"{model} {ssp} {observation_date.isoformat()} Daily Intermediate.tif"
            )
            write_multiband_geotiff(
                folders["daily"] / daily_name,
                [tmax_c, tmin_c, radiation_mj, eto_mm, rain_mm],
                [
                    "Tmax C",
                    "Tmin C",
                    "Ra MJ m-2 day-1",
                    "ETo mm day-1",
                    "Rainfall mm day-1",
                ],
                grid,
            )
    pd.DataFrame(daily_rows).to_csv(
        folders["daily"] / "Daily Intermediate Results.csv", index=False
    )

    monthly_rows = []
    seasonal_sums = {
        name: np.where(
            grid.inside,
            np.zeros((grid.height, grid.width), dtype="float64"),
            np.nan,
        )
        for name in ("eto", "rain", "eta", "peff", "iwr")
    }
    area_scenarios = config["irrigated_area_multipliers"]
    for year, month in season_months(ending_year):
        key = (year, month)
        expected_days = calendar.monthrange(year, month)[1]
        source_days = len(monthly_arrays[key]["eto"])
        if config.get("strict_complete_months", True) and source_days != expected_days:
            raise RuntimeError(
                f"{MONTH_NAMES[month]} {year} has {source_days} days; "
                f"expected {expected_days}."
            )
        eto_month = np.where(
            grid.inside,
            np.nansum(np.stack(monthly_arrays[key]["eto"]), axis=0),
            np.nan,
        )
        rain_month = np.where(
            grid.inside,
            np.nansum(np.stack(monthly_arrays[key]["rain"]), axis=0),
            np.nan,
        )
        eta_month = eto_month * float(config["etof"][str(month)])
        peff_month = cropwat_effective_precipitation(rain_month)
        iwr_month = np.maximum(eta_month - peff_month, 0.0)
        for name, array in (
            ("eto", eto_month),
            ("rain", rain_month),
            ("eta", eta_month),
            ("peff", peff_month),
            ("iwr", iwr_month),
        ):
            seasonal_sums[name] += np.nan_to_num(array, nan=0.0)
        volume_arrays = []
        for scenario_name, multiplier in area_scenarios.items():
            scenario_weights = baseline_area_weights * float(multiplier)
            volume_gl_cells = iwr_month * scenario_weights * 0.001
            volume_arrays.append(volume_gl_cells)
            monthly_rows.append(
                {
                    "GCM": model,
                    "SSP": ssp,
                    "Season Ending Year": ending_year,
                    "Calendar Year": year,
                    "Month Number": month,
                    "Month": MONTH_NAMES[month],
                    "Expected Days": expected_days,
                    "Source Days": source_days,
                    "EToF": float(config["etof"][str(month)]),
                    "Irrigated Area Scenario": scenario_name,
                    "Irrigated Area km2": historical_mean_area * float(multiplier),
                    "Mean ETo mm": weighted_mean(eto_month, scenario_weights),
                    "Mean ETa mm": weighted_mean(eta_month, scenario_weights),
                    "Mean Rainfall mm": weighted_mean(rain_month, scenario_weights),
                    "Mean Effective Precipitation mm": weighted_mean(
                        peff_month, scenario_weights
                    ),
                    "Mean IWR mm": weighted_mean(iwr_month, scenario_weights),
                    "IWU GL": float(np.nansum(volume_gl_cells)),
                }
            )
        if config.get("write_monthly_geotiffs", True):
            monthly_name = f"{model} {ssp} {year}-{month:02d} Monthly Results.tif"
            write_multiband_geotiff(
                folders["monthly"] / monthly_name,
                [eto_month, rain_month, eta_month, peff_month, iwr_month]
                + volume_arrays,
                [
                    "ETo mm",
                    "Rainfall mm",
                    "ETa mm",
                    "Effective precipitation mm",
                    "IWR mm",
                ]
                + [f"IWU GL {name}" for name in area_scenarios],
                grid,
            )
    pd.DataFrame(monthly_rows).to_csv(
        folders["monthly"] / "Monthly Results.csv", index=False
    )

    seasonal_rows = []
    seasonal_volume_arrays = []
    for scenario_name, multiplier in area_scenarios.items():
        scenario_weights = baseline_area_weights * float(multiplier)
        volume_gl_cells = seasonal_sums["iwr"] * scenario_weights * 0.001
        seasonal_volume_arrays.append(volume_gl_cells)
        seasonal_rows.append(
            {
                "GCM": model,
                "SSP": ssp,
                "Season Ending Year": ending_year,
                "Season Start": start.isoformat(),
                "Season End": end.isoformat(),
                "Irrigated Area Scenario": scenario_name,
                "Irrigated Area km2": historical_mean_area * float(multiplier),
                "Seasonal ETo mm": weighted_mean(
                    seasonal_sums["eto"], scenario_weights
                ),
                "Seasonal ETa mm": weighted_mean(
                    seasonal_sums["eta"], scenario_weights
                ),
                "Seasonal Rainfall mm": weighted_mean(
                    seasonal_sums["rain"], scenario_weights
                ),
                "Seasonal Effective Precipitation mm": weighted_mean(
                    seasonal_sums["peff"], scenario_weights
                ),
                "Seasonal IWR mm": weighted_mean(
                    seasonal_sums["iwr"], scenario_weights
                ),
                "Seasonal IWU GL": float(np.nansum(volume_gl_cells)),
            }
        )
    pd.DataFrame(seasonal_rows).to_csv(
        folders["seasonal"] / "Seasonal Results.csv", index=False
    )
    if config.get("write_seasonal_geotiffs", True):
        seasonal_name = f"{model} {ssp} Season {ending_year} Results.tif"
        write_multiband_geotiff(
            folders["seasonal"] / seasonal_name,
            [seasonal_sums[name] for name in ("eto", "rain", "eta", "peff", "iwr")]
            + seasonal_volume_arrays,
            [
                "ETo mm",
                "Rainfall mm",
                "ETa mm",
                "Effective precipitation mm",
                "IWR mm",
            ]
            + [f"IWU GL {name}" for name in area_scenarios],
            grid,
        )
    logger.info(
        "Pilot completed for %s, %s, season ending %s", model, ssp, ending_year
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(project_root() / "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check Earth Engine availability without downloading",
    )
    arguments = parser.parse_args()
    config = load_config(Path(arguments.config))
    folders = prepare_output_folders(Path(config["_root"]))
    logger = configure_logging(folders["logs"] / "Processing Log.txt")
    missing_data_log = folders["logs"] / "Missing Data Log.csv"
    try:
        run_pilot(config, folders, logger, check_only=arguments.check_only)
        missing_data_log.unlink(missing_ok=True)
        return 0
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        pd.DataFrame([{"Error": str(exc)}]).to_csv(
            missing_data_log, index=False
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
