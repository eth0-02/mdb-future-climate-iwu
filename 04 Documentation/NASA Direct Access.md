# NASA Direct Data Access

## Archive coverage

The full MDB future-climate ensemble uses the public NASA NEX-GDDP-CMIP6 archive and the NASA NCCS THREDDS NetCDF subset service. It requests four SSPs (ssp126, ssp245, ssp370 and ssp585), daily tasmax, tasmin and pr, and calendar years 2024-2100. Calendar year 2024 is needed to construct September-December of the first irrigation season ending in 2025.

The 2024-2100 audit lists 7,700 of 8,316 possible model, SSP, variable and year combinations. The 616 unavailable files are CESM2 tasmax and tasmin; CESM2 rainfall alone cannot support the required Hargreaves-Samani calculation. All four SSPs and all 77 years have complete tasmax, tasmin and precipitation coverage for the other eight GCMs. The analysis does not substitute another model for CESM2.

## Full-period download result

The previous full-period download finished on 3 October 2026 for 2029-2100. The extended 2024-2100 download writes period-specific checks, error and summary files, preserving the previous manifests. Check the extended summary and errors before reporting completion. See:

- `03 Outputs/Archive Access Audit/Full Period Download Summary.json`
- `03 Outputs/Archive Access Audit/Full Period Download Checks.csv`
- `03 Outputs/Archive Access Audit/Full Period Download Errors.json`

The input files are clipped rectangular NASA subsets covering the MDB bounding rectangle, not catchment-masked IWU rasters. The ensemble outputs and their coverage are documented in `03 Outputs/Ensemble 20261003 140238`.

## Calendar treatment

The source NetCDF files are organized by calendar year, 2024-2100. Irrigation seasons are labelled by ending year and cover September through April; season 2025 combines September-December 2024 with January-April 2025. Source calendars (Gregorian, no-leap or 360-day) and native daily coverage are checked before processing.

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
