@echo off
setlocal EnableDelayedExpansion

echo.
echo ============================================================
echo   Dashboard CCTV - Production Setup ^& Launch
echo ============================================================
echo.

REM ── Step 1: Virtual environment ──────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo [1/4] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: python not found. Install Python 3.10+ and add to PATH.
        pause & exit /b 1
    )
) else (
    echo [1/4] Virtual environment already exists.
)

call venv\Scripts\activate.bat

REM ── Step 2: Install / update dependencies ────────────────────
echo [2/4] Installing dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo ERROR: pip install failed. Check requirements.txt and internet connection.
    pause & exit /b 1
)

REM ── Step 3: Seed sample data (60 days, idempotent) ───────────
set FLASK_ENV=production
set FLASK_APP=run.py

echo [3/4] Seeding sample data (60 days of historical snapshots)...
flask seed-sample --days 60
if errorlevel 1 (
    echo WARNING: seed-sample returned an error (non-fatal, continuing...)
)

REM ── Step 4: Start Dashboard ──────────────────────────────────
echo [4/4] Starting Dashboard CCTV (production)...
echo.
echo   URL: http://localhost:5000
echo   Press Ctrl+C to stop.
echo.
python run.py

pause
