# Data Access Status

## Local inputs

The project contains the Murray-Darling Basin boundary and 15 annual CSIRO MDB irrigated-fraction rasters for WY2010-WY2024. The fraction rasters are on a 1 km EPSG:9473 equal-area grid, have values from 0 to 1, and have a historical mean area of approximately 8,813.947 km2. These annual fractions are averaged cell by cell for the future-climate analysis.

The source CSIRO MDB class maps are from collection 73785: <https://data.csiro.au/collection/73785>. The upstream classification counts only classes 1-5 as irrigated. Class 6 is non-irrigated. The future-climate workflow reads the prepared fraction rasters and does not reclassify the native CSIRO classes again.

## NASA climate data

The full ensemble uses public NEX-GDDP-CMIP6 annual NetCDF subsets obtained through the NASA NCCS THREDDS NetCDF subset service. All four SSPs are available through this route for eight complete GCMs. The archive inventory listed 7,200 of 7,776 possible GCM/SSP/variable/year subsets; the 576 unavailable files are CESM2 tasmax and tasmin. CESM2 precipitation is available, but without both temperature variables the specified Hargreaves-Samani ETo and IWU cannot be calculated.

The full period download completed on 3 October 2026. All 7,200 available subsets are present and validated, with zero final download errors. The authoritative counters are in `03 Outputs/Archive Access Audit/Full Period Download Summary.json`; the corresponding error list is `Full Period Download Errors.json`.

The NASA Earth Engine collection is used only for the separate pilot workflow. The full ensemble obtains all four requested SSPs through the direct NASA access route and does not require Earth Engine authentication.

## Calendar and season coverage

Annual source subsets are organized by calendar year, covering 2029-2100. They are combined to build irrigation seasons labelled by their ending year, from 2030 through 2100. For example, season 2031 includes September-December 2030 and January-April 2031. The native Gregorian, no-leap or 360-day calendar is retained in the source records and harmonized month by month to Gregorian day counts. Temperature values are interpolated within each month; rainfall is redistributed by day-interval overlap so each source monthly rainfall total is conserved. This is an explicit modelling assumption described in `Full Ensemble Method.md`.

## Completed analysis

The completed run is in `03 Outputs/Ensemble 20261003 140238`. It is a complete available eight-GCM ensemble, not a nine-GCM ensemble. Its `Run Summary.json` reports 54,528 monthly records and 6,816 seasonal records; `GCM Coverage.csv` shows eight complete GCMs and CESM2 excluded. The run created 2,272 seasonal GeoTIFFs. Climate outputs retain the NEX-GDDP-CMIP6 0.25-degree EPSG:4326 grid; the 1 km CSIRO irrigated-area fractions are aggregated to that coarser climate grid, not the reverse.
