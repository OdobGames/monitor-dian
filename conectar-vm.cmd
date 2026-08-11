@echo off
rem Abre una consola dentro de la VM de Oracle.
rem   conectar-vm.cmd                 -> sesion interactiva
rem   conectar-vm.cmd "comando aqui"  -> ejecuta ese comando y sale
setlocal
call "%~dp0vm-config.cmd"
title Consola de la VM  -  %VM_USER%@%VM_IP%

if not exist "%VM_KEY%" (
    echo.
    echo   No encuentro la llave SSH en "%VM_KEY%"
    echo   Corrige la ruta en vm-config.cmd
    echo.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo.
    echo   Conectando a %VM_USER%@%VM_IP% ...
    echo   Para volver a Windows escribe: exit
    echo.
)

ssh -i "%VM_KEY%" %VM_USER%@%VM_IP% %*
set "CODIGO=%ERRORLEVEL%"

if not "%CODIGO%"=="0" (
    echo.
    echo   La conexion termino con codigo %CODIGO%
    pause
)
endlocal
