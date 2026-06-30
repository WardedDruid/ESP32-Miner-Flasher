@echo off
echo =========================================
echo  ESP32 CYD Flasher - Setup ^& Run
echo =========================================

echo.
echo [1/2] Installing dependencies...
pip install customtkinter tkinterdnd2 pyserial esptool
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is on your PATH.
    pause
    exit /b 1
)

echo.
echo [2/2] Launching ESP32 CYD Flasher...
python main.py

pause
