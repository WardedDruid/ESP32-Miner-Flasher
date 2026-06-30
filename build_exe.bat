@echo off
echo =========================================
echo  Building ESP32 CYD Flasher .exe
echo =========================================

pip install pyinstaller customtkinter tkinterdnd2 pyserial esptool

python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "ESP32_CYD_Flasher" ^
    --icon "icon.ico" ^
    --add-data "icon.ico;." ^
    --add-data "core;core" ^
    --add-data "gui;gui" ^
    --hidden-import customtkinter ^
    --hidden-import tkinterdnd2 ^
    --hidden-import serial ^
    --hidden-import serial.tools ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import esptool ^
    --collect-all customtkinter ^
    --collect-all tkinterdnd2 ^
    --collect-all esptool ^
    main.py

echo.
echo Done! Executable is in: dist\ESP32_CYD_Flasher.exe
pause
