@echo off
title AI Duplicate Finder Installer
echo ===================================================
echo   AI Product Duplicate Finder Installer for Windows
echo ===================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your Windows PATH.
    echo Please install Python 3.8+ and check "Add Python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: 2. Create Virtual Environment if it doesn't exist
if not exist ".venv" (
    echo [1/4] Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Virtual environment folder (.venv) already exists.
)

:: 3. Upgrade Pip and install requirements
echo [2/4] Installing required dependencies (pandas, openpyxl, torch, rclip)...
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install pandas openpyxl numpy torch rclip
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python dependencies.
    pause
    exit /b 1
)

:: 4. Verify/Ensure report template and script exists
echo [3/4] Checking project scripts...
if not exist "match_image_ai.py" (
    echo [ERROR] match_image_ai.py is missing from this directory!
    pause
    exit /b 1
)

:: 5. Create Desktop/Start Menu shortcut script
echo [4/4] Creating Launcher script...
echo @echo off > run_gui.bat
echo cd /d "%%~dp0" >> run_gui.bat
echo call .venv\Scripts\activate >> run_gui.bat
echo start /b pythonw gui.py >> run_gui.bat

echo.
echo ===================================================
echo   INSTALLATION COMPLETED SUCCESSFULLY!
echo ===================================================
echo.
echo You can now run the app on Windows by double-clicking:
echo -^> "run_gui.bat" (this will open the GUI without showing a command console)
echo.
pause
