@echo off
chcp 65001 >nul
echo Starting Obscura CLI Manager...

set PYTHON_CMD=python

:: Try to find local or AstrBot virtual environment
if exist ".\.venv\Scripts\python.exe" (
    set PYTHON_CMD=".\.venv\Scripts\python.exe"
) else if exist ".\env\Scripts\python.exe" (
    set PYTHON_CMD=".\env\Scripts\python.exe"
) else if exist "..\..\..\env\Scripts\python.exe" (
    set PYTHON_CMD="..\..\..\env\Scripts\python.exe"
) else if exist "..\..\..\venv\Scripts\python.exe" (
    set PYTHON_CMD="..\..\..\venv\Scripts\python.exe"
) else if exist "..\..\..\.venv\Scripts\python.exe" (
    set PYTHON_CMD="..\..\..\.venv\Scripts\python.exe"
)

echo Using Python interpreter: %PYTHON_CMD%
%PYTHON_CMD% -m obscura_manager.cli
pause
