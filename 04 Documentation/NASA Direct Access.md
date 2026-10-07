# NASA Direct Data Access

## Archive coverage

The full MDB future-climate ensemble uses the public NASA NEX-GDDP-CMIP6 archive and the NASA NCCS THREDDS NetCDF subset service. It requests four SSPs (ssp126, ssp245, ssp370 and ssp585), daily tasmax, tasmin and pr, and calendar years 2029-2100. The late 2029 source data are needed to construct the irrigation season ending in 2030.

The inventory contains 7,200 of 7,776 possible model, SSP, variable and year combinations. All 576 unavailable subsets are CESM2 tasmax and tasmin; CESM2 rainfall alone cannot support the required Hargreaves-Samani calculation. The agreed analysis therefore proceeds with eight complete GCMs and does not substitute another model for CESM2.

## Full-period download result

The complete full-period download finished on 3 October 2026. All 7,200 available files were downloaded or reused from successful prior validation and passed the pipeline's NetCDF and coverage checks. The final summary records 7,200 verified or reused files and zero errors. See:

- `03 Outputs/Archive Access Audit/Full Period Download Summary.json`
- `03 Outputs/Archive Access Audit/Full Period Download Checks.csv`
- `03 Outputs/Archive Access Audit/Full Period Download Errors.json`

The input files are clipped rectangular NASA subsets covering the MDB bounding rectangle, not catchment-masked IWU rasters. The ensemble outputs and their coverage are documented in `03 Outputs/Ensemble 20261003 140238`.

## Calendar treatment

The source NetCDF files are organized by calendar year, 2029-2100. Irrigation seasons are labelled by ending year and cover September through April; season 2031 combines September-December 2030 with January-April 2031. Source calendars (Gregorian, no-leap or 360-day) and native daily coverage are checked before processing.

For calendar harmonization, daily maximum and minimum temperatures are interpolated at fractional-month day centres to the corresponding Gregorian month length. Rainfall is redistributed by day-interval overlap weights, preserving each source monthly total. Gregorian leap days are retained. This conversion is an explicit analytical assumption, not observed data, and is described in detail in `Full Ensemble Method.md`.

## Reproduce the download

From the project folder, after installing `requirements.txt`, run:

```powershell
python "02 Scripts/Download NASA Subsets.py" --root . --all-years
```

The downloader reuses successfully checked local files, writes an inventory of checks and failures, and supports interrupted-run recovery. `Run Full Study.bat` performs this download step and then runs the ensemble analysis. The NASA direct-download workflow does not require Google Earth Engine authentication.

## Official sources

- NASA NEX-GDDP-CMIP6: <https://www.nccs.nasa.gov/data-collections/nex-gddp-cmip6/>
- NASA archive: <https://nex-gddp-cmip6.s3.us-west-2.amazonaws.com/>
- CSIRO MDB irrigated-area collection: <https://data.csiro.au/collection/73785>
