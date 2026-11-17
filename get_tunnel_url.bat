@echo off
title Get & Sync Cloudflare Tunnel URL
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_tunnel_url.ps1"
pause
