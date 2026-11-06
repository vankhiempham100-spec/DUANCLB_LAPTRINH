@echo off
chcp 65001 > nul
title SMART HSE VISION - WEB HTML5 / CSS3 DASHBOARD
echo ==============================================================================
echo        HỆ THỐNG ĐIỂM DANH & GIÁM SÁT AN TOÀN LAO ĐỘNG (SMART HSE VISION)
echo                   KHỞI ĐỘNG WEB SERVER HTML5 / CSS3 & FASTAPI
echo ==============================================================================
echo.
echo Đang khởi chạy máy chủ Web FastAPI & Giao diện Dashboard...
echo -> Truy cập trên Laptop:     http://localhost:8501
echo -> Truy cập trên Điện thoại: Kết nối chung Wi-Fi và nhập IP của Laptop
echo.

cd /d "%~dp0"

:: Tự động nhận diện đường dẫn Python chính xác trên máy
if exist "C:\Users\phamv\AppData\Local\Programs\Python\Python311\python.exe" (
    "C:\Users\phamv\AppData\Local\Programs\Python\Python311\python.exe" app_core.py
) else (
    py -3.11 app_core.py || py app_core.py || python app_core.py
)

pause
