"""Focused regression tests for direct-source calendar handling."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
import xarray as xr

spec = importlib.util.spec_from_file_location('ensemble', Path(__file__).resolve().parents[1] / '02 Scripts' / 'Run Ensemble.py')
ensemble = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ensemble)


class CalendarTests(unittest.TestCase):
    def test_gregorian_unchanged(self):
        values = np.arange(29.).reshape(29, 1, 1)
        np.testing.assert_array_equal(ensemble.calendar_month(values, 29), values)

    def test_rain_conservation(self):
        rng = np.random.default_rng(44)
        for source in (28, 29, 30, 31):
            rain = rng.random((source, 3, 4)) * 50
            for target in (28, 29, 30, 31):
                result = ensemble.calendar_month(rain, target, True)
                self.assertEqual(len(result), target)
                np.testing.assert_allclose(result.sum(axis=0), rain.sum(axis=0))
                self.assertTrue((result >= 0).all())

    def test_constant_temperature_preserved(self):
        np.testing.assert_allclose(ensemble.calendar_month(np.full((30, 2, 3), 300.), 29), 300.)

    def test_no_inverted_temperature_created(self):
        low = np.arange(30.).reshape(30, 1, 1)
        high = low + 10
        self.assertTrue((ensemble.calendar_month(high, 31) >= ensemble.calendar_month(low, 31)).all())

    def test_missing_not_zero(self):
        rain = np.ones((30, 1, 1))
        rain[10] = np.nan
        self.assertTrue(np.isnan(ensemble.calendar_month(rain, 31, True)).any())

    def test_native_leap_and_360_day_coverage(self):
        for native, days in [('proleptic_gregorian', 366), ('noleap', 365), ('360_day', 360)]:
            time = xr.date_range('2032-01-01', periods=days, freq='D', calendar=native, use_cftime=True)
            ds = xr.Dataset(coords={'time': time})
            ds.time.encoding['calendar'] = native
            self.assertEqual(ensemble.verify_dates(ds, 2032), native)
            with self.assertRaises(ValueError):
                ensemble.verify_dates(ds.isel(time=slice(1, None)), 2032)


if __name__ == '__main__':
    unittest.main(verbosity=2)
