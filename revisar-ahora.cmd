@echo off
rem Fuerza una revision ahora mismo, sin esperar al temporizador, y muestra
rem como quedo. Tarda cosa de un minuto (tiene que abrir Chromium y navegar).
setlocal
call "%~dp0vm-config.cmd"
title Revision manual del monitor DIAN

if not exist "%VM_KEY%" (
    echo   No encuentro la llave SSH en "%VM_KEY%" - revisa vm-config.cmd
    pause
    exit /b 1
)

echo.
echo   Lanzando una revision en la VM... (aguanta ~1 minuto)
echo.

rem Ojo: --respetar-horario sigue mandando, asi que fuera de 5:00-21:00 no
rem revisa nada. Para saltarse el horario usa: ver-corridas.cmd systemd
ssh -t -i "%VM_KEY%" %VM_USER%@%VM_IP% "sudo systemctl start dian-monitor.service; /opt/dian/resumen.sh 5"

echo.
pause
endlocal
