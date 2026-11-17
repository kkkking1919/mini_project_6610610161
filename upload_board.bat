@echo off
title PlatformIO - Upload Firmware to Cucumber RS
chcp 65001 >nul
echo ===================================================================
echo   Cucumber RS (ESP32-S2) - Uploading Firmware...
echo ===================================================================
echo.
echo [TIPS สำหรับบอร์ด Cucumber RS / ESP32-S2]:
echo หากโปรแกรมขึ้น Error หา COM Port ไม่พบ หรือ Upload ไม่ผ่าน:
echo 1. กดปุ่ม BOOT (GPIO 0) ค้างไว้
echo 2. กดปุ่ม RESET (EN) 1 ครั้ง แล้วปล่อย
echo 3. ปล่อยปุ่ม BOOT เพื่อเข้าสู่ ROM Download Mode
echo ===================================================================
echo.

set PIO_BIN=pio
where pio >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    if exist "%USERPROFILE%\.platformio\penv\Scripts\pio.exe" (
        set "PIO_BIN=%USERPROFILE%\.platformio\penv\Scripts\pio.exe"
    ) else (
        echo [ERROR] PlatformIO CLI not found in system or ~/.platformio.
        pause
        exit /b 1
    )
)

"%PIO_BIN%" run --target upload
echo.
pause
