@echo off
chcp 65001 >nul
cd /d "%~dp0"
title xiaosu-ai-assistant

where pwsh >nul 2>nul
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1"
) else (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1"
)
