@echo off
title PlatformIO - Serial Monitor (115200)
chcp 65001 >nul
echo ===================================================================
echo   Cucumber RS - Opening Serial Monitor (115200 Baud)...
echo   (กด Ctrl + C เพื่อออกจาก Monitor)
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

"%PIO_BIN%" device monitor --baud 115200 --rts 0 --dtr 0
pause
