@echo off
setlocal
cd /d "%~dp0"
set PYTHON=python
if exist ".venv\Scripts\python.exe" set PYTHON=".venv\Scripts\python.exe"
%PYTHON% "05 Tests\Test Future Climate IWU.py"
if errorlevel 1 goto :failed
%PYTHON% "05 Tests\Test Ensemble.py"
if errorlevel 1 goto :failed
echo All tests passed.
pause
exit /b 0

:failed
echo ERROR: A test failed.
pause
exit /b 1
