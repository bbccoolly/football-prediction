@echo off
title Football Predict Server
cd /d "%~dp0"
echo ================================
echo   Football Predict Server
echo   http://127.0.0.1:5000/
echo   Press Ctrl+C to stop
echo ================================
echo.
C:\Python314\python.exe run.py
pause