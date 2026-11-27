@echo off
rem One-click start on Windows: install / update deps -> build web pages -> start server at http://127.0.0.1:8000
rem The web pages are rebuilt on every start, so pulled code changes always take effect.
rem Options (set before running):  set PORT=8080      change the port
rem                                set SKIP_BUILD=1   skip rebuilding the web pages (faster start)
setlocal
cd /d "%~dp0.."
if "%PORT%"=="" set "PORT=8000"
set "VENV=backend\.venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" goto :venv_ok
echo [1/4] Creating Python virtual environment in %VENV% ...
where py >nul 2>nul
if errorlevel 1 (
  python -m venv "%VENV%"
) else (
  py -3 -m venv "%VENV%"
)
if not exist "%PY%" goto :nopython
"%PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required. Delete %VENV% and install a newer Python from https://www.python.org/downloads/
  goto :fail
)
"%PY%" -m pip install --upgrade pip
:venv_ok

rem Install Python packages when requirements.txt changed since the last install
fc /b "backend\requirements.txt" "%VENV%\requirements.installed" >nul 2>nul
if not errorlevel 1 goto :deps_ok
echo [2/4] Installing Python packages, this takes a few minutes the first time ...
"%PY%" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :fail
copy /y "backend\requirements.txt" "%VENV%\requirements.installed" >nul
:deps_ok

if exist "data\timeseries\BA-101.parquet" goto :data_ok
echo [3/4] Generating demo data ...
"%PY%" scripts\gen_demo_data.py
if errorlevel 1 goto :fail
:data_ok

if "%SKIP_BUILD%"=="1" if exist "frontend\dist\index.html" goto :run
where npm >nul 2>nul
if errorlevel 1 (
  if exist "frontend\dist\index.html" (
    echo Node.js not found, using the existing web pages build. Install Node.js 18+ to rebuild: https://nodejs.org/
    goto :run
  )
  echo Node.js 18 or newer is required to build the web pages: https://nodejs.org/
  goto :fail
)
echo [4/4] Building the web pages ...
pushd frontend
fc /b "package-lock.json" "node_modules\package-lock.installed" >nul 2>nul
if not errorlevel 1 goto :npm_ok
call npm install --no-audit --no-fund
if errorlevel 1 (popd & goto :fail)
copy /y "package-lock.json" "node_modules\package-lock.installed" >nul
:npm_ok
call npm run build
if errorlevel 1 (popd & goto :fail)
popd

:run
echo.
echo Starting server: http://127.0.0.1:%PORT%   (press Ctrl+C to stop)
start "" /min cmd /c "timeout /t 5 >nul & start http://127.0.0.1:%PORT%"
cd backend
"..\%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --no-use-colors
goto :eof

:nopython
echo Python was not found. Install Python 3.11+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
:fail
echo.
echo Start failed, see the messages above.
pause
exit /b 1
