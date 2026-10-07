@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo MDB Future Climate Irrigation Water Use Pilot
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment...
  py -3.12 -m venv .venv 2>nul
  if errorlevel 1 py -3.11 -m venv .venv 2>nul
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 goto :python_error
)

echo Installing or updating required packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :package_error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :package_error

echo.
echo Checking Google Earth Engine access...
set /p "EARTH_ENGINE_PROJECT=Enter your Google Earth Engine project ID: "
if not defined EARTH_ENGINE_PROJECT goto :earth_engine_project_error
".venv\Scripts\python.exe" -c "import ee, os; ee.Initialize(project=os.environ['EARTH_ENGINE_PROJECT']); print('Earth Engine access confirmed.')"
if errorlevel 1 (
  echo.
  echo A browser will open for Earth Engine sign-in.
  echo Sign in, approve access, then return to this window.
  ".venv\Scripts\earthengine.exe" authenticate
  if errorlevel 1 goto :earth_engine_error
)

echo.
echo Running scientific tests...
".venv\Scripts\python.exe" "05 Tests\Test Future Climate IWU.py"
if errorlevel 1 goto :test_error

echo.
echo Running the one-GCM, one-SSP, one-season pilot...
".venv\Scripts\python.exe" "02 Scripts\Future Climate IWU.py" --config config.json
if errorlevel 1 goto :run_error

echo.
echo Pilot completed successfully.
echo Results are in: 03 Outputs
pause
exit /b 0

:python_error
echo ERROR: Python 3.11 or 3.12 is required. Install it from python.org and select Add Python to PATH.
goto :failed

:package_error
echo ERROR: Package installation failed. Check the internet connection and rerun this file.
goto :failed

:earth_engine_error
echo ERROR: Earth Engine authentication failed. Confirm that the Google account has Earth Engine access.
goto :failed

:earth_engine_project_error
echo ERROR: A Google Earth Engine project ID is required for the pilot.
goto :failed

:test_error
echo ERROR: Scientific tests failed. The pilot was not run.
goto :failed

:run_error
echo ERROR: The pilot did not complete. Read 03 Outputs\Logs\Processing Log.txt.

:failed
pause
exit /b 1
