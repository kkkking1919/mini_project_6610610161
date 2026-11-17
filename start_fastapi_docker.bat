@echo off
title Cucumber RS - AIoT Server & Telegram Sync
chcp 65001 >nul
echo ===================================================================
echo  Cucumber RS - Starting FastAPI Dashboard with Docker...
echo ===================================================================

:: 1. Check if Docker is responding
docker info >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Docker Desktop is not running or still initializing.
    echo [*] Please open Docker Desktop and wait for the engine to start.
    echo [*] Retrying in 10 seconds...
    timeout /t 10 /nobreak >nul
    docker info >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo.
        echo [ERROR] Docker is still unreachable.
        echo Please ensure Docker Desktop is open and the green icon shows 'Engine running'.
        pause
        exit /b 1
    )
)

:: 2. Launch Docker Compose
echo.
echo [*] Launching Docker containers...
docker compose up -d

:: 3. Run auto-sync script for Cloudflare Tunnel & Telegram
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_tunnel_url.ps1"

echo.
pause
