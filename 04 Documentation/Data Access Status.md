# Data Access Status

## Local inputs

The project contains the Murray-Darling Basin boundary and 15 annual CSIRO MDB irrigated-fraction rasters for WY2010-WY2024. The fraction rasters are on a 1 km EPSG:9473 equal-area grid, have values from 0 to 1, and have a historical mean area of approximately 8,813.947 km2. These annual fractions are averaged cell by cell for the future-climate analysis.

The source CSIRO MDB class maps are from collection 73785: <https://data.csiro.au/collection/73785>. The upstream classification counts only classes 1-5 as irrigated. Class 6 is non-irrigated. The future-climate workflow reads the prepared fraction rasters and does not reclassify the native CSIRO classes again.

## NASA climate data

The full ensemble uses public NEX-GDDP-CMIP6 annual NetCDF subsets obtained through the NASA NCCS THREDDS NetCDF subset service. The 2024-2100 archive audit lists 7,700 of 8,316 possible GCM/SSP/variable/year subsets. All four SSPs and all 77 years are available for eight complete GCMs. The 616 unavailable files are CESM2 tasmax and tasmin; CESM2 precipitation is available, but without both temperature variables the specified Hargreaves-Samani ETo and IWU cannot be calculated.

The earlier 2029-2100 download completed on 3 October 2026 with 7,200 available subsets validated and no final download errors. The extension uses a separate 2024-2100 audit and download manifest; check those period-tagged files for current counts before treating the extension as complete.

The NASA Earth Engine collection is used only for the separate pilot workflow. The full ensemble obtains all four requested SSPs through the direct NASA access route and does not require Earth Engine authentication.

## Calendar and season coverage

Annual source subsets are organized by calendar year, covering 2024-2100. They are combined to build irrigation seasons labelled by ending year, from 2025 through 2100. The first season includes September-December 2024 and January-April 2025; the final season includes September-December 2099 and January-April 2100. The native Gregorian, no-leap or 360-day calendar is retained in source records and harmonized month by month to Gregorian day counts. Temperature values are interpolated within each month; rainfall is redistributed by day-interval overlap so each source monthly rainfall total is conserved. This is an explicit modelling assumption described in `Full Ensemble Method.md`.

## Completed analysis

The preserved earlier run is in `03 Outputs/Ensemble 20261003 140238` and covers seasons ending 2030-2100. The extended run should have its own dated folder; use its `Run Summary.json` and `GCM Coverage.csv` to verify completion. Climate outputs retain the NEX-GDDP-CMIP6 0.25-degree EPSG:4326 grid; the 1 km CSIRO irrigated-area fractions are aggregated to that coarser climate grid, not the reverse.
