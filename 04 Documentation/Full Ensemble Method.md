# Full MDB Future Climate Study

## Scope and source

This run implements the future-climate study in Instructions.docx, not the older
historical Namoi analysis. It covers irrigation seasons ending in 2025-2100,
following the historical analysis through 2024. Each season extends from 1
September of the previous year to 30 April of the labelled year. The first
projected season (2025) therefore uses September-December 2024 and January-April
2025; source NetCDF subsets are needed for calendar years 2024-2100. For example,
season 2031 uses September-December 2030 and January-April 2031.
May-August and other months outside the irrigation season are not used in IWU
calculations.

NASA NEX-GDDP-CMIP6 is accessed directly through the NASA THREDDS NetCDF subset
service. Only the MDB bounding rectangle is transferred, not global rasters.
All four SSPs were requested for the nine specified models. The audited archive
contains 7,200 available subsets out of 7,776 possible model/SSP/variable/year
subsets for 2029-2100. The extended 2024-2100 audit is recorded separately and
must be checked before the new run is described as complete. CESM2 maximum and
minimum temperatures are absent for every SSP and year in the previous audit.
Its available rainfall is retained but cannot by itself produce
Hargreaves ETo or IWU. No other GCM is silently substituted for CESM2. The
agreed analysis therefore uses the eight complete GCMs; this is a complete
available eight-model ensemble, not a complete nine-model ensemble.

An archive listing is not a download. The download manifest records source URLs,
subset URLs, actual daily coverage, units, finite-data fractions and SHA256
checksums only after files are downloaded and read successfully.

## Calendar treatment: explicit additional assumption

Raw NetCDF files retain their original Gregorian, no-leap or 360-day calendar.
The document does not specify how to reconcile non-Gregorian models with normal
calendar reporting. The ensemble implementation therefore makes this explicit
additional assumption: within each month, daily maximum/minimum temperatures
are linearly interpolated at fractional-month day centres to the Gregorian day
count, with endpoint values held at the edges. Rainfall depths are redistributed
using day-interval overlap weights that preserve each source monthly total.

This is a numerical calendar harmonisation assumption, not additional observed
weather. It can affect daily temperature variability and nonlinear ETo and
should be disclosed in the manuscript and considered in sensitivity analysis.
Months already having the correct number of days are unchanged. Gregorian leap
years use 29 February; 2100 is not a leap year. Outputs retain both source and
analysis day counts and the source calendar. Missing native days are an error,
not a reason to invent an interpolation. Tests verify daily coverage and
rainfall conservation across calendar conversion.

## Daily and monthly calculation

Temperatures in Kelvin are converted to degrees Celsius. Mean temperature is
(Tmax + Tmin) / 2. Extraterrestrial radiation is calculated from latitude and
Gregorian day of year using the astronomical equations, then multiplied by
0.408 to convert MJ per square metre per day to equivalent mm per day.

ETo = 0.0023 * Ra(mm/day) * (Tmean + 17.8) * sqrt(Tmax - Tmin)

ETo is calculated daily before summation to months. This is not an ETo estimate
from monthly mean temperature. Precipitation in kg m-2 s-1 is multiplied by
86,400 to obtain daily mm, then accumulated monthly. Monthly ETa is monthly ETo
multiplied by the supplied month-specific EToF (Sep .546, Oct .531, Nov .522,
Dec .590, Jan .702, Feb .720, Mar .621, Apr .4725).

Monthly Pe = P * (125 - 0.2 * P) / 125 for P <= 250 mm;
monthly Pe = 125 + 0.1 * P for P > 250 mm.

Monthly IWR = max(0, ETa - Pe).

These equations are implemented directly. The future-climate study does not
use pyCropWat soil-storage parameters, SILO or satellite ET as future climate
inputs. Those belong to the earlier historical study, not this specification.

## Spatial support and area

Climate calculations retain the native 0.25-degree NASA grid. They do not create
new 1-km climate detail. The fixed historical mean CSIRO irrigated-fraction
rasters use the documented EPSG:9473 equal-area 1-km grid. Positive fine-cell
irrigated areas within the supplied boundary are assigned to their containing
climate cell by fine-cell centroid and summed. The total is checked against the
historical mean; it is not adjusted by normalising coarse boundary-cell averages.
Centroid assignment is a finite-resolution boundary approximation, not exact
polygon intersection. Existing supplied fractional rasters are inputs; the
original CSIRO classification is not reclassified by this future pipeline.

The fraction rasters were derived from the native CSIRO MDB seasonal class maps in collection 73785 (https://data.csiro.au/collection/73785). The upstream classification rule counts only classes 1, 2, 3, 4 and 5 as irrigated; class 6 is non-irrigated and is not included in the irrigated numerator. Classification precedes aggregation to irrigated fractions on the 1 km EPSG:9473 grid. The future-climate workflow uses the supplied 15 annual fraction rasters and does not reclassify the raw CSIRO maps a second time. These are byte-identical copies of the previously prepared CSIRO fractions (verified 2 October 2026).

The upstream MDB pipeline test checks this rule using a synthetic 4 x 4 raster with five class 1-5 pixels and eleven class 6 pixels; the resulting irrigated fraction is 5/16.

The 15 available historical annual area rasters (WY2010-WY2024) give a fixed mean
of approximately 8,813.9475 km2. The scenarios use 90%, 100% and 110% of this
area, keeping the spatial distribution fixed. All reported climate depths are
weighted by irrigated area, not unweighted averages of basin pixels.

Cell IWU (GL) = monthly IWR (mm) * irrigated area (km2) * 0.001.

Missing climate values over irrigated land cause that model/year to fail and be
logged. They are never counted as zero rain, zero ET or zero water use. Negative
rainfall beyond a small floating-point tolerance and Tmax below Tmin are errors.
Monthly cell volumes must reconcile with area-weighted depth times total area.
Seasonal totals are created only for eight complete months, by summing positive
monthly deficits, not by truncating a seasonal net balance.

## Outputs and completion

Each analysis writes a new dated Ensemble directory under 03 Outputs. The
existing GEE pilot is not overwritten. Outputs include monthly and seasonal
CSV tables, daily CSV and compressed NetCDF intermediates, monthly and seasonal
multiband GeoTIFFs, historical area totals, source-file checksums, run configuration,
missing-data records and a run summary. No multi-year averaging is applied.

The requested ensemble contains nine GCMs, but the previous NASA archive audit
shows that CESM2 lacks tasmax and tasmin. CESM2 therefore has no IWU estimates
and is explicitly excluded. For 2025-2100, a complete eight-model run is
expected to contain 58,368 monthly records, 7,296 seasonal records across four
SSPs and three irrigated-area scenarios, and 2,432 seasonal GeoTIFFs. These are
expected counts, not evidence that a run has completed. Each results table
carries the coverage statement and a false Complete Nine GCM Ensemble flag.
GCM Coverage.csv and Run Summary.json must be checked before describing a run
as complete. A finished download alone is not proof of successful calculation.

Results estimate net climatic irrigation requirement, not pumping or measured
withdrawal. Fixed EToF, Hargreaves assumptions, the effective-rainfall equation,
static irrigated area and calendar conversion remain limitations. Computational
tests establish consistency, not independent validation of future water use.

## Running and monitoring

Run `python "02 Scripts/Run Full Study.py"` from the project folder, or double
click `Run Full Study.bat`. It downloads all available annual subsets, retries
failed transfers in up to five passes, then processes complete inputs. Keep
the machine powered on and connected. Valid downloaded files are reused on a
restart; partial downloads are downloaded again. A process lock prevents duplicate
full-study runners. No Earth Engine authentication is required for this route.

Read `03 Outputs/Logs/Full Study Status.json` for the current stage and
`03 Outputs/Archive Access Audit/Full Period Download Summary.json` for actual
counts. Full Study Console.txt and Full Study Errors.txt contain background-run
output. The latest dated Ensemble directory contains the analysis Run Summary.json.
Status files may remain after a shutdown; verify the recorded process is running.

## Sources

- NASA: https://www.nccs.nasa.gov/data-collections/nex-gddp-cmip6/
- NASA archive: https://nex-gddp-cmip6.s3.us-west-2.amazonaws.com/
- NCSS service: https://docs.unidata.ucar.edu/tds/5.6/adminguide/netcdf_subset_service_ref.html
- Calendar caveats: https://docs.xarray.dev/en/stable/generated/xarray.Dataset.convert_calendar.html
- Hargreaves: https://www.hec.usace.army.mil/confluence/hmsdocs/hmstrm/evaporation-and-transpiration/hargreaves-method
- CSIRO historical input: https://data.csiro.au/collection/73785
