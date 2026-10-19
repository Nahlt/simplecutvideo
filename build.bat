@echo off
rem Crea la versione portabile di SimpleCutVideo (dist\SimpleCutVideo e dist\SimpleCutVideo-<versione>-win64.zip).
rem Requisiti: Windows 10/11 con Python 3.12 o 3.13 e connessione Internet (scarica dipendenze e FFmpeg).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build.ps1" %*
if errorlevel 1 (
    echo.
    echo Build NON riuscita: controlla i messaggi qui sopra.
    pause
    exit /b 1
)
echo.
echo Build completata: trovi il pacchetto nella cartella dist.
pause
