# Chuleta de comandos — Monitor de citas DIAN

El monitor corre en GitHub Actions. No hay maquina que mantener ni nada que
dejar encendido.

## Lo de todos los dias

```bash
gh run list --workflow=dian.yml          # las ultimas corridas
gh run list --workflow=dian.yml -L 50    # las ultimas 50
gh run watch                             # ver la corrida en curso, en vivo
gh run view                              # el detalle de la ultima
gh run view --log                        # su registro completo

gh workflow run "Monitor de citas DIAN"  # forzar una revision ahora
```

Lo mismo con botones: pestana **Actions** del repositorio.

## Ver las capturas

| Donde | Que muestra |
|---|---|
| `https://github.com/OdobGames/monitor-dian/tree/resultados` | La ultima captura, se abre en el navegador |
| Resumen de la corrida (pestana Actions) | Estado, hora de Bogota, modalidad, codigo de salida |
| **Artifacts**, al pie de la corrida | La captura de esa corrida, en un zip. Se borra a los 7 dias |

La rama `resultados` se actualiza en hora cerrada, y siempre que haya cupos.

```bash
gh run download                          # bajar las capturas de la ultima corrida
```

## Cuando revisa

Desde la v6, cada ~3 minutos entre las 5:00 y las 21:00, hora de Colombia. Cada
corrida vigila en bucle casi 6 horas y la siguiente queda en cola, asi que no
depende de la puntualidad del cron. Las corridas "canceladas" que se ven en
Actions son las que sobran en la cola: es normal.

La frecuencia se cambia con `DIAN_INTERVALO_SEG` y la franja con
`DIAN_HORA_INICIO` / `DIAN_HORA_FIN`, en `.github/workflows/dian.yml`.

## Configuracion

Los datos sensibles son secretos del repositorio, no estan en el codigo. Los
de los avisos al celular (ntfy, Telegram, llamada, WhatsApp) estan explicados
en LEEME.md:

```bash
gh secret list                                    # cuales hay (no muestra el valor)
gh secret set DIAN_CLAVE_APP --body "clave nueva" # cambiar uno
```

| Secreto | Que es |
|---|---|
| `DIAN_CORREO_DESTINO` | a donde llega el aviso |
| `DIAN_CORREO_ORIGEN` | desde donde sale |
| `DIAN_CLAVE_APP` | clave de aplicacion de Google, no la del correo |
| `DIAN_NTFY_TOPIC` | tema de ntfy para el push al celular |
| `DIAN_TELEGRAM_TOKEN`, `DIAN_TELEGRAM_CHAT_ID` | bot de Telegram |
| `DIAN_TWILIO_SID`, `DIAN_TWILIO_TOKEN`, `DIAN_TWILIO_FROM`, `DIAN_TWILIO_TO` | llamada telefonica |
| `DIAN_WHATSAPP_PHONE`, `DIAN_WHATSAPP_APIKEY` | WhatsApp por CallMeBot |

La modalidad y el horario van como variables normales dentro del workflow
(`DIAN_MODALIDAD`, `DIAN_HORA_INICIO`, `DIAN_HORA_FIN`).

## Apagarlo y encenderlo

```bash
gh workflow disable "Monitor de citas DIAN"
gh workflow enable  "Monitor de citas DIAN"
```

## En este PC (Windows)

```powershell
$env:DIAN_CORREO_DESTINO = "tu_correo@gmail.com"
$env:DIAN_CORREO_ORIGEN  = "tu_correo@gmail.com"
$env:DIAN_CLAVE_APP      = "la clave de aplicacion"

python check_dian_v5.py --test-avisos         # comprobar que llegan los avisos
python check_dian_v5.py --una-vez --debug     # una revision con ventana visible
python check_dian_v5.py --vigilar --abrir-navegador  # bucle, modo asistido
python check_dian_v5.py --limpiar             # solo limpiar y salir
```

## Codigos de salida

`0` hay cupos · `1` sin cupos · `2` error de la pagina · `3` fuera de horario.
El workflow solo marca la corrida en rojo con el `2`: no haber cupos es el
resultado normal y no debe llenar el correo de fallos.

## Si algo va mal

| Sintoma | Que mirar |
|---|---|
| No llegan correos y deberia | `gh run view --log` de una corrida con cupos; luego `--test-correo` en el PC con la misma clave |
| Todas las corridas en rojo | La DIAN cambio la pagina. Baja la captura con `gh run download` y corre `--una-vez --debug` en el PC |
| Hace dias que no corre sola | GitHub apaga los workflows programados tras 60 dias sin actividad en el repositorio. `gh workflow enable` y comprobar que el commit de latido mensual se este haciendo |
| Las corridas llegan tarde | Es el cron de GitHub. Si cuesta cupos, la alternativa es una VM propia: ver `MIGRAR.md`, Ruta B |

## Si algun dia vuelve a una VM

`MIGRAR.md` (Ruta B) tiene los pasos. El instalador es `subir-a-vm.ps1` mas
`vm/install.sh`, y una vez instalado se maneja por SSH:

```bash
/opt/dian/resumen.sh                              # resumen de las corridas
systemctl list-timers dian-monitor.timer          # cuando toca la proxima
sudo systemctl start dian-monitor.service         # una revision ahora
sudo tail -f /opt/dian/monitor.log                # registro en vivo
sudo nano /opt/dian/dian.env                      # configuracion (permisos 600)
```
