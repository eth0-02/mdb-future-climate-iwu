# MDB Future Climate Irrigation Water Use

This project estimates future irrigation water requirement in the Murray-Darling Basin. It calculates daily Hargreaves-Samani reference evapotranspiration from NASA NEX-GDDP-CMIP6 temperature, aggregates daily values to months, estimates monthly actual evapotranspiration with the supplied historical ETo fractions, applies the FAO CROPWAT effective-rainfall equation, and converts the remaining crop-water deficit to irrigation water use using a fixed historical-mean CSIRO irrigated area.

The extended projection covers irrigation seasons ending in 2025-2100, following the historical analysis through 2024. The September-April season ending in 2025 runs from 1 September 2024 to 30 April 2025. It therefore uses September-December 2024 as required carry-in months; climate source years span 2024-2100. The historical land-area reference remains based on CSIRO annual maps through WY2024.

The project includes an earlier, single-season Earth Engine pilot and a separate full-ensemble workflow. The requested study has nine GCMs. Because the audited NASA archive lacks CESM2 maximum and minimum temperatures, no CESM2 IWU can be calculated. Results must be described as an eight-GCM ensemble (8 of 9 requested GCMs), and only if the run summary confirms complete records for all eight. The output tables and GCM Coverage.csv report this limitation explicitly.

## Quick start on Windows

1. Install 64-bit Python 3.11 or later (tested on 3.14) from <https://www.python.org/downloads/windows/>. During installation, select **Add Python to PATH**.
2. Open a command window in this project folder and run `python -m pip install -r requirements.txt`.
3. To run the full analysis, double-click `Run Full Study.bat` or run `python "02 Scripts\Run Full Study.py"`.
4. Keep the computer powered and online. The runner downloads and verifies the available annual subsets, then creates a new dated ensemble output folder. It reuses valid downloaded data if restarted.
5. Monitor `03 Outputs\Logs\Full Study Status.json`, the period-specific download summary under `03 Outputs\Archive Access Audit`, and the latest ensemble `Run Summary.json`. Use completion and record counts to confirm a finished run.

The Earth Engine authentication is only needed for the older `Run Pilot.bat` workflow. The full ensemble downloader uses the public NASA archive and does not require a Google credential.

No personal credentials or Google API keys are stored in this project. Earth Engine keeps authentication in the current Windows user's secure configuration.

## Required local inputs

The project uses relative paths, so users do not edit code or enter another person's drive letters.

- `01 Input Data/Boundary/mdb_boundary.shp` and its `.dbf`, `.shx`, and `.prj` components.
- Annual `MDB CSIRO WY.... Irrigated Fraction.tif` rasters in `01 Input Data/CSIRO Irrigated Area`.

The fraction rasters were derived from the native CSIRO MDB seasonal class maps in collection 73785 (<https://data.csiro.au/collection/73785>). Only classes 1-5 are counted as irrigated; class 6 is non-irrigated and is excluded from the irrigated numerator. Classification is performed before aggregation to irrigated fractions on the 1 km EPSG:9473 grid. The full-ensemble workflow uses the supplied annual fraction rasters and does not reclassify the source maps a second time. The 15 files are verified copies of the previously prepared CSIRO fractions.

The annual CSIRO fractions are averaged cell by cell to obtain one fixed historical-mean irrigated area. For the full ensemble, fine-grid irrigated area is assigned to the native climate grid by fine-pixel centroid; area totals are checked against the historical mean. The full ensemble calendar and spatial assumptions are described in `04 Documentation\Full Ensemble Method.md`.

### Final ensemble decisions and completed run

The agreed analysis uses eight complete GCMs. CESM2 is excluded because the audited NASA archive does not provide its daily maximum and minimum temperatures; its rainfall data alone are insufficient for the specified Hargreaves-Samani calculation. The earlier run ending in 2030-2100 contains 54,528 monthly records, 6,816 seasonal records and 2,272 seasonal GeoTIFFs and remains preserved. A complete extended 2025-2100 run with eight available GCMs is expected to contain 58,368 monthly records, 7,296 seasonal records and 2,432 seasonal GeoTIFFs; verify actual completeness in its run summary.

NASA subsets are stored by calendar year, but irrigation seasons are labelled by their ending year. The first projected season, 2025, covers 1 September 2024 through 30 April 2025; the final season, 2100, covers 1 September 2099 through 30 April 2100. Native Gregorian, no-leap and 360-day calendars are harmonized month by month to Gregorian day counts as described in the methods note.

Source references:

- NASA NEX-GDDP-CMIP6 Earth Engine catalog: <https://developers.google.com/earth-engine/datasets/catalog/NASA_GDDP-CMIP6>
- HEC-HMS Hargreaves method: <https://www.hec.usace.army.mil/confluence/hmsdocs/hmstrm/evaporation-and-transpiration/hargreaves-method>
- CSIRO MDB irrigated-area collection: <https://data.csiro.au/collection/73785>

## Scientific workflow

Use `.venv\Scripts\python.exe` in the commands below if `Run Pilot.bat` has created a private environment; otherwise use `python`.

### Irrigation season

Only September through April are analysed. Seasons are labelled by their ending year. For example, season 2031 includes September-December 2030 and January-April 2031. Source subsets use calendar years; the adjacent source years are combined to form each ending-year irrigation season. Python's calendar rules are used directly, so February has 29 days whenever the Gregorian ending year is a leap year.

### Reference evapotranspiration

Daily maximum and minimum air temperature are read from `NASA/GDDP-CMIP6`. Kelvin is converted to degrees Celsius. Extraterrestrial radiation is calculated from cell latitude and day of year using the standard astronomical equations. Radiation in MJ m-2 day-1 is converted to equivalent evaporation depth using 0.408 mm per MJ m-2 before the Hargreaves-Samani calculation:

`ETo = 0.0023 x Ra(mm/day) x (Tmean + 17.8) x sqrt(Tmax - Tmin)`

Daily ETo is calculated before monthly summation. The code does not use monthly mean temperature to estimate ETo.

### Actual evapotranspiration

Monthly ETa is `ETa = EToF x ETo`. The EToF values are supplied in `config.json` and are specific to September-April.

### Rainfall and effective precipitation

NEX-GDDP-CMIP6 precipitation is stored as kg m-2 s-1. The code multiplies it by 86,400 to obtain daily millimetres, then sums daily rainfall to monthly rainfall. Monthly effective precipitation is calculated with FAO CROPWAT:

- If `P <= 250 mm`, `Peff = P x (125 - 0.2 x P) / 125`.
- If `P > 250 mm`, `Peff = 125 + 0.1 x P`.

### Irrigation requirement and water use

Monthly net irrigation requirement is `max(0, ETa - Peff)`. Irrigation water use is reported for three fixed-area scenarios:

- historical mean minus 10 percent;
- historical mean;
- historical mean plus 10 percent.

For each grid cell, `IWU GL = IWR mm x irrigated area km2 x 0.001`. Monthly values are summed to the September-April seasonal total.

## Outputs

- `03 Outputs/Daily`: daily summary CSV and daily GeoTIFFs.
- `03 Outputs/Monthly`: monthly CSV and multiband GeoTIFFs containing ETo, rainfall, ETa, effective precipitation, IWR, and cell IWU.
- `03 Outputs/Seasonal`: seasonal CSV and multiband GeoTIFF.
- `03 Outputs/Quality Control`: data-source availability, daily coverage, and irrigated-area summaries.
- `03 Outputs/Logs`: processing and missing-data logs.
- `03 Outputs/Downloads`: cached monthly Earth Engine stacks so an interrupted run can resume.
- `03 Outputs/Ensemble <date time>`: full-ensemble run. `Monthly Results.csv` and `Seasonal Results.csv` cover every GCM x SSP x season x area scenario; each `<GCM>/<ssp code>` folder holds daily CSV and NetCDF intermediates and monthly and seasonal GeoTIFFs. Tables label scenarios as `SSP1-2.6`, `SSP2-4.5`, `SSP3-7.0` and `SSP5-8.5`; folders keep NASA's codes (`ssp126` etc.). Check `Run Summary.json` and `GCM Coverage.csv` before using results.

The folders above this line are the Earth Engine pilot outputs.

GeoTIFF values retain the native NEX-GDDP-CMIP6 analysis grid. The climate source is about 0.25 degrees (roughly 25-28 km); the code does not create artificial 1 km climate detail.

## Data availability that must not be hidden

The earlier 2029-2100 archive inventory and download records are retained. The extended 2024-2100 audit and download write period-tagged manifests under `03 Outputs/Archive Access Audit`; consult those manifests and the latest Ensemble run summary for actual availability, checksums, errors, and completeness. These downloads are separate from the Earth Engine pilot. A restart reuses previously validated files and re-downloads partial ones.

The Google Earth Engine `NASA/GDDP-CMIP6` collection exposes `historical`, `ssp245`, and `ssp585`. SSP1-2.6 and SSP3-7.0 are therefore taken from the direct NASA route above, which the full ensemble uses for all four SSPs. Earth Engine is used only by the single-season pilot.

CESM2 does not provide the temperature bands required for this Hargreaves-Samani workflow in the Earth Engine collection or in the NASA archive itself (checked again on 1 October 2026: the CESM2 folders hold pr, tas, hurs, huss, rlds and rsds only). The code marks it unavailable instead of substituting another model or method.

## Changing the pilot

Edit only the three values under `pilot` in `config.json`:

```json
"pilot": {
  "gcm": "ACCESS-CM2",
  "ssp": "SSP2-4.5",
  "season_ending_year": 2025
}
```

Supported Earth Engine SSP labels in this release are `SSP2-4.5` and `SSP5-8.5`. The ending year must be between 2025 and 2100. Keep the pilot to one combination until its daily, monthly, and seasonal checks have been accepted.

## Command-line use

From the project folder:

```powershell
.venv\Scripts\python.exe "02 Scripts\Future Climate IWU.py" --config config.json
```

Useful checks:

```powershell
.venv\Scripts\python.exe "02 Scripts\Future Climate IWU.py" --config config.json --check-only
.venv\Scripts\python.exe "05 Tests\Test Future Climate IWU.py"
.venv\Scripts\python.exe "05 Tests\Test Ensemble.py"
```

## Method limitations

- ETa is estimated by applying fixed historical monthly ETo fractions to future ETo. It is not a direct future ETa simulation.
- Hargreaves-Samani uses temperature and extraterrestrial radiation only; it does not explicitly use humidity, wind, or net radiation.
- The fixed historical-mean irrigated footprint assumes no future spatial change beyond the -10 and +10 percent area scenarios.
- NEX-GDDP-CMIP6 is substantially coarser than the historical 1 km irrigated-area rasters. The area fractions are aggregated to the climate grid; coarse climate values are not downscaled to imply finer information.
- Results are net climatic irrigation requirements, not gross diversions, pumping, conveyance losses, or on-farm application volumes.
