# Monitor de citas DIAN

Vigila la pagina de agendamiento de la DIAN (Persona Natural → Videoatención →
Devoluciones) y avisa por correo en cuanto deja de aparecer el mensaje de "sin
cupos". Usa Playwright con Chromium en modo invisible.

Se puede correr de tres formas: en GitHub Actions (gratis, sin maquina), en una
VM propia (puntual al segundo), o en este mismo PC. Los pasos de cada una estan
en [MIGRAR.md](MIGRAR.md).

> La clave de aplicacion de Google ya **no** va dentro de `check_dian_v5.py`.
> Se lee de la variable de entorno `DIAN_CLAVE_APP` (en la VM, de
> `/opt/dian/dian.env` con permisos 600; en GitHub, de un secreto del
> repositorio). Sin ella el monitor revisa igual, pero no manda correo.

## En este PC (Windows)

```powershell
pip install playwright
playwright install chromium

$env:DIAN_CLAVE_APP = "la clave de aplicacion de Google"

python check_dian_v5.py --test-correo        # comprobar que el correo sale
python check_dian_v5.py --una-vez --debug    # una revision con ventana visible
python check_dian_v5.py --vigilar            # bucle continuo, hay que dejarlo abierto
```

## En GitHub Actions (gratis, no depende de ninguna maquina)

El workflow esta en `.github/workflows/dian.yml`: revisa cada 30 minutos dentro
del horario, sin VM que mantener. La contrapartida es que el cron de GitHub no
es puntual — una corrida puede arrancar entre 5 y 20 minutos tarde. El montaje
completo esta en [MIGRAR.md](MIGRAR.md).

## En una VM propia (puntual al segundo)

Sirve cualquier Ubuntu 24.04 con systemd, x86-64 o ARM. Desde esta carpeta, en
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\subir-a-vm.ps1
```

Copia el codigo a la VM y ejecuta `vm/install.sh`, que:

1. Si la maquina tiene menos de 2 GB de RAM, le crea 2 GB de intercambio.
   Chromium pide unos 600 MB en el pico; sin intercambio, en una maquina de 1 GB
   el kernel empieza a matar procesos al azar.
2. Instala `python3-venv` (nada mas; **no** hace `apt upgrade`, para no tocar lo
   que ya corre en la maquina).
3. Crea el usuario de sistema `dian` y la carpeta `/opt/dian`, aparte de todo lo
   demas.
4. Monta un entorno virtual de Python propio y baja Chromium dentro de
   `/opt/dian/browsers` (no se mezcla con nada del servidor).
5. Instala un servicio y un temporizador de systemd que lanzan **una** revision
   cada 30 minutos y terminan.

Las revisiones caen en **hora cerrada**: 2:00, 2:30, 3:00, 3:30… El temporizador
va sin desfase aleatorio y con precision de un segundo.

## Atajos de esta carpeta (doble clic)

| Archivo | Para que |
|---|---|
| `ver-corridas.cmd` | resumen: proxima revision, como fue cada una, cuantas van |
| `ver-corridas.cmd seguir` | el registro en vivo |
| `revisar-ahora.cmd` | fuerza una revision ya, sin esperar al temporizador |
| `conectar-vm.cmd` | abre una consola dentro de la VM |
| `vm-config.cmd` | la IP, el usuario y la llave — se edita aqui y ya |

La lista completa de comandos esta en [COMANDOS.md](COMANDOS.md).

### Que garantiza que no se lleve la maquina por delante

Todo esta en `vm/dian-monitor.service`. Los topes estan calculados para una
maquina chica (1 vCPU, 1 GB de RAM), que es lo minimo donde esto corre bien:

| Tope | Valor | Para que |
|---|---|---|
| `Nice=10`, `IOSchedulingClass=idle` | prioridad baja | si algo mas corre en la maquina, va primero |
| `MemoryHigh=600M` | freno suave | el kernel lo presiona antes de llegar al tope duro |
| `MemoryMax=850M` | tope duro | si Chromium se desboca, lo mata a el y nada mas |
| `RuntimeMaxSec=600` | 10 min | una revision colgada se mata sola, con hijos y todo |
| `PrivateTmp=yes` | `/tmp` propio | la basura temporal desaparece al terminar |
| `ProtectSystem=strict` | solo escribe en `/opt/dian` | no puede tocar nada mas del sistema |

Ademas es un proceso que arranca, trabaja ~1 minuto y muere. Entre revisiones no
queda nada corriendo.

### Limpieza automatica

Al principio de **cada** ejecucion, `check_dian_v5.py` hace:

- Recorta `monitor.log` si pasa de 1 MB (deja las ultimas 3000 lineas).
- Borra las capturas de `salida/` con mas de 3 dias, y si aun asi la carpeta
  pasa de 50 MB, va borrando de la mas vieja.
- Elimina los perfiles y volcados que Chromium deja en la carpeta temporal
  cuando una corrida muere a medias (solo carpetas con nombre inequivoco de
  Playwright/Chromium y con mas de una hora).
- En Linux, mata navegadores huerfanos de corridas anteriores — solo procesos
  del propio usuario `dian` y cuyo ejecutable esta dentro de
  `/opt/dian/browsers`. Nunca toca otro proceso de la maquina.

Los limites se pueden cambiar en `/opt/dian/dian.env`
(`DIAN_MAX_LOG_BYTES`, `DIAN_DIAS_CAPTURAS`, `DIAN_MAX_SALIDA_MB`).

### Comandos utiles en la VM

```bash
systemctl list-timers dian-monitor.timer     # cuando toca la proxima
sudo systemctl start dian-monitor.service    # forzar una revision ahora
sudo tail -f /opt/dian/monitor.log           # el registro del monitor
journalctl -u dian-monitor -n 50 --no-pager  # lo que vio systemd
sudo systemctl disable --now dian-monitor.timer   # apagarlo

# probar el correo (o cualquier otra opcion) con el mismo entorno del servicio:
sudo systemd-run --wait --collect --pipe -p User=dian \
  -p EnvironmentFile=/opt/dian/dian.env \
  -p Environment=PLAYWRIGHT_BROWSERS_PATH=/opt/dian/browsers \
  -p WorkingDirectory=/opt/dian \
  /opt/dian/venv/bin/python /opt/dian/check_dian_v5.py --test-correo
```

`/opt/dian/dian.env` tiene permisos 600 y dueño `dian`: leerlo o pasarlo con
`grep`/`env` desde otro usuario falla. Por eso el rodeo con `systemd-run`.

## Configuracion

En la VM se edita `/opt/dian/dian.env` (permisos 600 porque lleva la clave) y
luego `sudo systemctl restart dian-monitor.timer`. En GitHub Actions son
secretos del repositorio. En Windows, variables de entorno de la sesion.

| Variable | Por defecto | Que hace |
|---|---|---|
| `DIAN_CORREO_DESTINO` | — (obligatoria) | a donde llega el aviso |
| `DIAN_CORREO_ORIGEN` | — (obligatoria) | desde donde sale |
| `DIAN_CLAVE_APP` | — | clave de aplicacion de Google, no la del correo. Obligatoria para que salga el aviso |
| `DIAN_MODALIDAD` | videoatencion | `videoatencion` o `presencial` |
| `DIAN_HORA_INICIO` / `DIAN_HORA_FIN` | 5 / 21 | franja de vigilancia |

## Codigos de salida

`0` hay cupos · `1` sin cupos · `2` error de la pagina · `3` fuera de horario.
El servicio de systemd trata los cuatro como normales.
