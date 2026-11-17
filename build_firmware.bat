@echo off
title PlatformIO - Build Firmware
chcp 65001 >nul
echo ===================================================================
echo   Cucumber RS (ESP32-S2) - Building Firmware...
echo ===================================================================

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

"%PIO_BIN%" run
echo.
pause
