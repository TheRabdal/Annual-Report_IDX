@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Download Laporan Tahunan IDX
echo ============================================
echo  Download Laporan Tahunan IDX (252 emiten)
echo  Aman diulang: file yang sudah ada di-SKIP
echo ============================================
echo.
python download_idx.py
echo.
echo Selesai. Tekan tombol apa saja untuk menutup.
pause >nul
