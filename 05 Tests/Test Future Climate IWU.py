from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import geopandas as gpd
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box


SCRIPT = Path(__file__).resolve().parents[1] / "02 Scripts" / "Future Climate IWU.py"
SPEC = importlib.util.spec_from_file_location("future_iwu", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FutureIWUTests(unittest.TestCase):
    def test_season_dates_and_leap_years(self):
        self.assertEqual(
            MODULE.season_dates(2031), (date(2030, 9, 1), date(2031, 4, 30))
        )
        self.assertEqual(
            len(list(MODULE.iter_dates(*MODULE.season_dates(2031)))), 242
        )
        leap_dates = list(MODULE.iter_dates(*MODULE.season_dates(2032)))
        self.assertEqual(len(leap_dates), 243)
        self.assertIn(date(2032, 2, 29), leap_dates)

    def test_precipitation_unit_conversion(self):
        result = MODULE.precipitation_to_mm_day(np.array([1.0, 0.0001]))
        np.testing.assert_allclose(result, [86400.0, 8.64])

    def test_cropwat_effective_precipitation_branches(self):
        result = MODULE.cropwat_effective_precipitation(
            np.array([0.0, 100.0, 250.0, 300.0])
        )
        np.testing.assert_allclose(result, [0.0, 84.0, 150.0, 155.0])

    def test_extraterrestrial_radiation_units_are_consistent(self):
        latitude = np.deg2rad(np.array([[-35.0]]))
        radiation_mj, radiation_mm = MODULE.extraterrestrial_radiation(latitude, 15)
        self.assertGreater(float(radiation_mj[0, 0]), 35.0)
        self.assertLess(float(radiation_mj[0, 0]), 50.0)
        np.testing.assert_allclose(radiation_mm, radiation_mj * 0.408)

    def test_hargreaves_is_daily_and_nonnegative(self):
        latitude = np.deg2rad(np.array([[-35.0]]))
        eto, _, _ = MODULE.hargreaves_samani(
            np.array([[32.0]]), np.array([[18.0]]), latitude, 15
        )
        self.assertGreater(float(eto[0, 0]), 3.0)
        self.assertLess(float(eto[0, 0]), 10.0)

    def test_month_sequence_crosses_calendar_year(self):
        self.assertEqual(
            MODULE.season_months(2031),
            [
                (2030, 9),
                (2030, 10),
                (2030, 11),
                (2030, 12),
                (2031, 1),
                (2031, 2),
                (2031, 3),
                (2031, 4),
            ],
        )

    def test_grid_coordinates_keep_raster_shape(self):
        profile = {
            "driver": "GTiff",
            "height": 2,
            "width": 3,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:4326",
            "transform": from_origin(140.0, -30.0, 0.25, 0.25),
        }
        boundary = gpd.GeoDataFrame(
            geometry=[box(140.0, -30.5, 140.75, -30.0)], crs="EPSG:4326"
        )
        with MemoryFile() as memory_file:
            with memory_file.open(**profile) as dataset:
                dataset.write(np.ones((2, 3), dtype="float32"), 1)
                grid = MODULE.build_grid(dataset, boundary)
        self.assertEqual(grid.latitude_radians.shape, (2, 3))
        self.assertEqual(grid.cell_area_km2.shape, (2, 3))


if __name__ == "__main__":
    unittest.main(verbosity=2)
