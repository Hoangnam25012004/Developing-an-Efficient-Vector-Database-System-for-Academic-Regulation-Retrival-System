@echo off
REM ============================================================
REM  Khoi dong FastAPI tren http://127.0.0.1:8000
REM  Dung 127.0.0.1 thay vi localhost de tranh van de IPv6 ::1
REM  tren Windows (uvicorn binding 0.0.0.0 chi nghe IPv4).
REM ============================================================

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Khong tim thay venv. Tao truoc:
    echo     python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)

REM Kill any lingering uvicorn on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr LISTENING') do (
    echo [BE] Killing previous server PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
)

echo [BE] Khoi dong FastAPI o cua so "ARRS-Server" ...
start "ARRS-Server" cmd /k "cd /d %~dp0 && call venv\Scripts\activate.bat && echo --- Starting uvicorn --- && python -m uvicorn api.main:app --host 0.0.0.0 --port 8000"

echo [BE] Cho server san sang (poll 127.0.0.1:8000) ...
powershell -NoProfile -Command "$ok=$false; for($i=0;$i -lt 120;$i++){ try{ if((Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop).StatusCode -eq 200){ $ok=$true; break } } catch{} Start-Sleep -Milliseconds 300 }; if($ok){ exit 0 } else { exit 1 }"

if %errorlevel% NEQ 0 (
    echo.
    echo [ERROR] Server khong san sang sau ~36s.
    echo [ERROR] Hay xem cua so "ARRS-Server" de doc traceback.
    echo.
    pause
    exit /b 1
)

echo [BE] Server OK. Mo trinh duyet ...
start "" "http://127.0.0.1:8000"

echo.
echo ==========================================
echo   UI       -^> http://127.0.0.1:8000
echo   API Docs -^> http://127.0.0.1:8000/docs
echo   Server chay trong cua so "ARRS-Server"
echo   Dong cua so do de dung server.
echo.
echo   Luu y: dung 127.0.0.1 thay vi localhost.
echo ==========================================
endlocal
