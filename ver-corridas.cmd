@echo off
rem Muestra como le fue al monitor DIAN en cada revision.
rem
rem   ver-corridas.cmd            -> resumen con las ultimas 15 revisiones
rem   ver-corridas.cmd 50         -> las ultimas 50
rem   ver-corridas.cmd seguir     -> registro en vivo (Ctrl+C para salir)
rem   ver-corridas.cmd systemd    -> lo que vio systemd (para depurar fallos)
setlocal
call "%~dp0vm-config.cmd"
title Corridas del monitor DIAN

if not exist "%VM_KEY%" (
    echo   No encuentro la llave SSH en "%VM_KEY%" - revisa vm-config.cmd
    pause
    exit /b 1
)

set "MODO=%~1"

if /I "%MODO%"=="seguir"  goto seguir
if /I "%MODO%"=="vivo"    goto seguir
if /I "%MODO%"=="systemd" goto systemd
if "%MODO%"==""           set "MODO=15"

ssh -t -i "%VM_KEY%" %VM_USER%@%VM_IP% "/opt/dian/resumen.sh %MODO%"
goto fin

:seguir
echo.
echo   Registro en vivo. Ctrl+C para salir.
echo.
ssh -t -i "%VM_KEY%" %VM_USER%@%VM_IP% "tail -n 40 -f /opt/dian/monitor.log"
goto fin

:systemd
ssh -t -i "%VM_KEY%" %VM_USER%@%VM_IP% "journalctl -u dian-monitor -n 60 --no-pager"

:fin
echo.
pause
endlocal
