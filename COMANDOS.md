# Chuleta de comandos — Monitor de citas DIAN

## Lo de todos los días (doble clic, desde esta carpeta)

| Archivo | Para qué |
|---|---|
| `ver-corridas.cmd` | resumen: cuándo toca la próxima, cómo fue cada revisión, cuántas van |
| `ver-corridas.cmd seguir` | el registro en vivo, línea a línea (Ctrl+C para salir) |
| `ver-corridas.cmd 50` | las últimas 50 revisiones en vez de 15 |
| `ver-corridas.cmd systemd` | lo que vio systemd — para cuando algo falló feo |
| `revisar-ahora.cmd` | fuerza una revisión ya, sin esperar al temporizador (~1 min) |
| `conectar-vm.cmd` | abre una consola dentro de la VM |
| `subir-a-vm.ps1` | vuelve a subir e instalar el monitor tras editar el `.py` |

La IP, el usuario y la llave SSH están en un solo sitio: **`vm-config.cmd`**. Si
cambia la IP de la VM, se edita ahí y todos los `.cmd` quedan al día.

Para pasarle un comando suelto a la VM sin abrir sesión:

```bat
conectar-vm.cmd "systemctl list-timers dian-monitor.timer"
```

## Cuándo revisa

Cada 30 minutos **en hora cerrada**: 2:00, 2:30, 3:00, 3:30… sin desfase
aleatorio. Fuera de la franja de vigilancia (5:00–21:00, hora de Colombia) el
proceso arranca, ve que no toca, limpia y se muere en menos de un segundo.

## Dentro de la VM (por SSH)

```bash
# ── Ver qué pasa ──────────────────────────────────────────────────────
/opt/dian/resumen.sh                  # el mismo resumen de ver-corridas.cmd
/opt/dian/resumen.sh 50               # las últimas 50
tail -f /opt/dian/monitor.log         # registro en vivo
journalctl -u dian-monitor -n 60 --no-pager       # lo que vio systemd
systemctl list-timers dian-monitor.timer          # cuándo toca la próxima
systemctl status dian-monitor.service             # cómo terminó la última

# ── Mandar ────────────────────────────────────────────────────────────
sudo systemctl start dian-monitor.service         # una revisión ahora
sudo systemctl disable --now dian-monitor.timer   # apagar el monitor
sudo systemctl enable  --now dian-monitor.timer   # volver a encenderlo
sudo systemctl restart dian-monitor.timer         # aplicar cambios del .timer

# ── Configuración (correo, horario, modalidad) ────────────────────────
sudo nano /opt/dian/dian.env
sudo systemctl restart dian-monitor.timer

# ── Probar el correo con el entorno real del servicio ─────────────────
sudo systemd-run --wait --collect --pipe -p User=dian \
  -p EnvironmentFile=/opt/dian/dian.env \
  -p Environment=PLAYWRIGHT_BROWSERS_PATH=/opt/dian/browsers \
  -p Environment=TZ=America/Bogota \
  -p WorkingDirectory=/opt/dian \
  /opt/dian/venv/bin/python /opt/dian/check_dian_v5.py --test-correo

# ── Limpieza a mano (normalmente se hace sola en cada corrida) ─────────
sudo systemd-run --wait --collect --pipe -p User=dian \
  -p Environment=TZ=America/Bogota -p WorkingDirectory=/opt/dian \
  /opt/dian/venv/bin/python /opt/dian/check_dian_v5.py --limpiar

# ── Cuánto ocupa ──────────────────────────────────────────────────────
du -sh /opt/dian && df -h /
```

`/opt/dian/dian.env` tiene permisos 600 y dueño `dian` porque lleva la clave del
correo: leerlo desde otro usuario da *Permission denied*. Por eso el rodeo con
`systemd-run` en vez de `sudo -u dian env $(grep ...)`.

## En este PC (Windows), sin VM

```powershell
python check_dian_v5.py --test-correo         # comprobar que el correo sale
python check_dian_v5.py --una-vez --debug     # una revisión con ventana visible
python check_dian_v5.py --vigilar             # bucle continuo, hay que dejarlo abierto
python check_dian_v5.py --limpiar             # solo limpiar y salir
```

## Códigos de salida

`0` hay cupos · `1` sin cupos · `2` error de la página · `3` fuera de horario.
systemd trata los cuatro como normales, así que el servicio nunca sale en rojo
por no haber cupos.

## Si algo va mal

| Síntoma | Qué mirar |
|---|---|
| No llegan correos y debería | `ver-corridas.cmd` → columna de resultados; luego la prueba de correo de arriba |
| Todas las corridas en ERROR | la DIAN cambió la página: `ver-corridas.cmd systemd` y luego una corrida con `--debug` en el PC |
| Corridas que "no llegaron a terminar" | Chromium no arrancó: `journalctl -u dian-monitor -n 40` |
| La VM va lenta | `systemctl status dian-monitor` — tiene tope de 60% de un núcleo y 1,5 GB, no debería ser esto |
