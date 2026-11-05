@echo off
rem One-click control test (config\control_test.yaml): same window as the demo. After the baseline,
rem type notes for the tasks: note:math / note:english at the start, note:end at the end. The airbag
rem may start in one task only; which one alternates by itself over the finished control tests.
rem Ports: change them here (as in demo.bat).
rem Saved to sessions\<date>_<name>; analyse with: python tools\analyze_control.py sessions\<folder>
set EDA_PORT=COM15
set PUMP_PORT=COM18
set PPG_ADDRESS=70:15:11:69:00:1D

cd /d "%~dp0"
set NAME=
set /p NAME=Participant name:
if "%NAME%"=="" (
    echo A name is needed.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat
python tools\run_session.py --name "%NAME%" --protocol config\control_test.yaml --order next --eda %EDA_PORT% --ppg-address %PPG_ADDRESS% --esp32 %PUMP_PORT% --pump auto --plot
pause
