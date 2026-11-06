@echo off
chcp 65001 > nul
title SMART HSE VISION - WEB DASHBOARD
echo ==============================================================================
echo        HE THONG DIEM DANH VA GIAM SAT AN TOAN LAO DONG (SMART HSE VISION)
echo                         KHOI DONG WEB DASHBOARD STREAMLIT
echo ==============================================================================
echo.
echo Dang khoi dong may chu Web Streamlit...
echo Vui long cho 3 - 5 giay de trinh duyet tu dong mo...
echo Dia chi Web: http://localhost:8501
echo.

:: Cho 3 giay de server Streamlit khoi dong xong roi moi bat trinh duyet
start /b cmd /c "timeout /t 3 /nobreak > nul & start http://localhost:8501"

py -3.11 -m streamlit run app.py --server.port 8501 --server.headless false
pause
