@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: build.bat  —  Build ModemDiag for Windows
:: Double-click for a complete release, or use build.bat --dev for an executable only.
:: Output: executable, binary ZIP and source ZIP in local-ignored\dist
:: ─────────────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"
if not defined PIP_CACHE_DIR set "PIP_CACHE_DIR=%CD%\local-ignored\cache\pip"
if not defined PYINSTALLER_CONFIG_DIR set "PYINSTALLER_CONFIG_DIR=%CD%\local-ignored\cache\pyinstaller"
set "PYTHONPYCACHEPREFIX=%CD%\local-ignored\cache\pycache"

echo === ModemDiag Windows Build ===
set MODEMDIAG_RELEASE=1
if "%~1"=="--dev" set MODEMDIAG_RELEASE=0
if not "%~1"=="" if not "%~1"=="--release" if not "%~1"=="--dev" (
    echo Usage: build.bat [--release ^| --dev]
    pause
    exit /b 2
)

:: Resolve all release inputs before installing dependencies or starting PyInstaller.
:: A source archive exports its exact platform inputs; a checkout includes a baseline.
set "RELEASE_PLATFORM=windows-%PROCESSOR_ARCHITECTURE%"
if /I "%PROCESSOR_ARCHITECTURE%"=="AMD64" set "RELEASE_PLATFORM=windows-x64"
set "RELEASE_INPUTS=tools\build\%RELEASE_PLATFORM%"
if exist "windows-release-review.json" set "RELEASE_INPUTS=."
if exist "release\%RELEASE_PLATFORM%\windows-release-review.json" set "RELEASE_INPUTS=release\%RELEASE_PLATFORM%"
set "RELEASE_REQUIREMENTS=%RELEASE_INPUTS%\requirements-release.txt"
if not defined MODEMDIAG_WINDOWS_REVIEW set "MODEMDIAG_WINDOWS_REVIEW=%CD%\%RELEASE_INPUTS%\windows-release-review.json"
if "%MODEMDIAG_RELEASE%"=="1" (
    if not exist "%MODEMDIAG_WINDOWS_REVIEW%" (
        echo ERROR: Missing Windows dependency review: "%MODEMDIAG_WINDOWS_REVIEW%"
        echo Use the complete source archive or checkout, including tools\build\%RELEASE_PLATFORM%.
        goto :FAILED
    )
    if not exist "%RELEASE_REQUIREMENTS%" (
        echo ERROR: Missing pinned build requirements: "%RELEASE_REQUIREMENTS%"
        goto :FAILED
    )
)

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found. Install from https://python.org
    pause & exit /b 1
)

:: 2. Try to create a virtual environment (skip gracefully if venv not available)
if not exist ".venv" (
    echo ^>^>^> Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo WARNING: Could not create venv - continuing with system Python
        goto :NO_VENV
    )
)
call .venv\Scripts\activate.bat
if errorlevel 1 goto :NO_VENV
echo ^>^>^> Using virtual environment
goto :INSTALL

:NO_VENV
echo ^>^>^> Using system/user Python installation

:INSTALL
:: 3. Install dependencies
echo ^>^>^> Installing dependencies...
if exist "%RELEASE_REQUIREMENTS%" (
    python -m pip install --disable-pip-version-check -r "%RELEASE_REQUIREMENTS%"
) else (
    python -m pip install --disable-pip-version-check -r requirements.txt
)
if errorlevel 1 goto :FAILED

:: 4. Clean old artefacts
echo ^>^>^> Cleaning old build...
if exist local-ignored\build\ModemDiag rmdir /s /q local-ignored\build\ModemDiag

:: 5. Build  — use "python -m PyInstaller" so it works regardless of PATH
echo ^>^>^> Running PyInstaller...
python -m PyInstaller tools\build\ModemDiag.spec --distpath local-ignored\dist --workpath local-ignored\build --noconfirm
if errorlevel 1 (
    echo.
    echo [FAIL] PyInstaller failed - check output above
    goto :FAILED
)

:: 6. Package separate binary and source ZIPs
if "%MODEMDIAG_RELEASE%"=="1" (
    python tools\release_bundle.py package
    if errorlevel 1 goto :FAILED
) else (
    python tools\release_bundle.py clean
    if errorlevel 1 goto :FAILED
)

:: 7. Done
echo.
echo [OK] Build complete!
echo     Executable: %cd%\local-ignored\dist\ModemDiag.exe
if "%MODEMDIAG_RELEASE%"=="1" echo     Release: binary ZIP plus separate source ZIP in local-ignored\dist
if "%MODEMDIAG_RELEASE%"=="1" echo     Combine both platforms' metadata for a multi-platform release; see README.md
echo     User data : %APPDATA%\ModemDiag\
echo.
pause
exit /b 0

:FAILED
echo.
echo [FAIL] Build stopped. See the error above; an existing executable may be from an earlier build.
pause
exit /b 1
