@echo off
cd /d "%~dp0"
python "02 Scripts\Run Full Study.py"
if errorlevel 1 echo The run needs attention. Check the logs and completeness reports.
pause
