@echo off
cd /d "%~dp0"
py -m pip install -q -r requirements.txt
py torretas.py
pause
