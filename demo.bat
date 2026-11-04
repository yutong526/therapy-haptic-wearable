@echo off
rem One-click demo: closed loop (config\demo.yaml) with the live window. Ports: change them here.
rem PUMP_PORT: COM18 = Bluetooth "Airbag-ESP32" (pair it in Windows first), COM16 = USB cable.
set EDA_PORT=COM15
set PUMP_PORT=COM18
set PPG_ADDRESS=70:15:11:69:00:1D

cd /d "%~dp0"
call .venv\Scripts\activate.bat
python tools\run_session.py --name demo --protocol config\demo.yaml --eda %EDA_PORT% --ppg-address %PPG_ADDRESS% --esp32 %PUMP_PORT% --pump auto --plot
pause
