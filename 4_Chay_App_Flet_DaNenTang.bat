@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
title SMART HSE VISION - MULTI-PLATFORM AI (WINDOWS / MACOS / ANDROID / IOS)
cls
echo ==============================================================================
echo        HE THONG DIEM DANH VA GIAM SAT AN TOAN LAO DONG (SMART HSE VISION)
echo       KHOI DONG HE THONG DA NEN TANG - DONG BO SQL SERVER VA TAILSCALE
echo ==============================================================================
echo.
echo [1/3] Dang giai phong port 8501 va cac tien trinh cu...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8501" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul 2>&1

echo.
echo [2/3] May chu dang khoi dong tai: http://localhost:8501
echo       (Ket noi tu xa qua Tailscale Mesh VPN hoac Mang noi bo)
echo.
echo [3/3] Dang tu dong mo trinh duyet...
echo.
echo ==============================================================================
echo   CHU Y QUAN TRONG:
echo   - CUA SO NAY LA MAY CHU AI DANG HOAT DONG!
echo   - XIN DUNG DONG CUA SO NAY. THU NHO CUA SO DE HE THONG TIEP TUC CHAY.
echo   - TAI KHOAN DANG NHAP SAN CO:
echo       + Chu Xuong (Owner):  khiem_owner  /  123456
echo       + Chu Server (Admin): admin        /  admin123
echo ==============================================================================
echo.

cd /d "%~dp0"
"C:\Users\phamv\AppData\Local\Programs\Python\Python311\python.exe" app_core.py
pause
