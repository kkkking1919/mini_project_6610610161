@echo off
title Run AIoT Full System Test
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test_system.ps1"
pause
