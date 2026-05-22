@echo off
title KTB Crawler - Background Worker
cd /d "%~dp0"
echo ==============================================
echo Đang chay Crawler de tai them URL moi...
echo ==============================================
python crawler.py
pause
