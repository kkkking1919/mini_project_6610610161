@echo off
title Restart FastAPI Container
chcp 65001 >nul
echo Restarting FastAPI container to apply latest iframe security headers...
docker restart cucumber_miniproject_fastapi
echo.
echo [DONE] FastAPI container restarted successfully!
pause
