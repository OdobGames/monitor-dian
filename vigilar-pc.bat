@echo off
rem Monitor DIAN en este PC, modo asistido: cuando haya cupos la pagina aparece
rem en pantalla ya parada en la lista de citas, con alarma sonora.
rem La configuracion va en dian.env, en esta misma carpeta (ver LEEME.md).
cd /d "%~dp0"
python check_dian_v5.py --vigilar --abrir-navegador
pause
