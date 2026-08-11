#!/usr/bin/env bash
# Instala el monitor de citas DIAN en una VM Ubuntu con systemd.
#
#   sudo bash install.sh
#
# Probado en Ubuntu 24.04, x86-64 y ARM. Sirve igual en la e2-micro gratuita de
# Google Cloud, en una Hetzner o en cualquier otra: no depende del proveedor.
#
# Es idempotente: volver a ejecutarlo actualiza el codigo y reinicia el
# temporizador. Todo vive en /opt/dian bajo un usuario propio ("dian"), con
# topes de memoria y de tiempo, para que un Chromium desbocado no se lleve por
# delante el resto de la maquina.
set -euo pipefail

DEST=/opt/dian
USUARIO=dian
ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecutalo con sudo"
[[ -f "$ORIGEN/check_dian_v5.py" ]] || die "no encuentro check_dian_v5.py junto a este script"

log "1/9 intercambio (swap)"
# Chromium pide ~600 MB en el pico. En una maquina de 1 GB (e2-micro de Google)
# eso queda al filo: sin intercambio, el kernel mata procesos al azar. Con 2 GB
# de archivo de intercambio el pico cabe y la maquina aguanta.
#
# Solo se crea si hay menos de 2 GB de RAM y todavia no hay intercambio; en una
# maquina grande este paso no hace nada.
RAM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
if [[ $RAM_MB -lt 2048 && $SWAP_MB -lt 512 ]]; then
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Preferir la RAM: el intercambio es red de seguridad, no almacen diario.
  sysctl -q -w vm.swappiness=10
  grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
  echo "    2 GB de intercambio activos (RAM detectada: ${RAM_MB} MB)"
else
  echo "    no hace falta (RAM ${RAM_MB} MB, intercambio ${SWAP_MB} MB)"
fi

log "2/9 paquetes de Python"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# Solo esto: nada de upgrade, para no tocar lo que ya corre en la maquina.
apt-get install -y -qq python3-venv python3-pip ca-certificates >/dev/null

log "3/9 usuario y carpetas"
id -u "$USUARIO" >/dev/null 2>&1 || \
  useradd --system --home "$DEST" --shell /usr/sbin/nologin "$USUARIO"
mkdir -p "$DEST" "$DEST/salida" "$DEST/browsers"

log "4/9 codigo"
install -m 644 "$ORIGEN/check_dian_v5.py" "$DEST/check_dian_v5.py"
install -m 644 "$ORIGEN/requirements.txt" "$DEST/requirements.txt"
install -m 755 "$ORIGEN/vm/resumen.sh" "$DEST/resumen.sh"
[[ -f "$ORIGEN/LEEME.md" ]]    && install -m 644 "$ORIGEN/LEEME.md" "$DEST/LEEME.md"
[[ -f "$ORIGEN/COMANDOS.md" ]] && install -m 644 "$ORIGEN/COMANDOS.md" "$DEST/COMANDOS.md"

log "5/9 configuracion (/opt/dian/dian.env)"
if [[ ! -f "$DEST/dian.env" ]]; then
  if [[ -f "$ORIGEN/vm/dian.env" ]]; then
    install -m 600 "$ORIGEN/vm/dian.env" "$DEST/dian.env"
  else
    install -m 600 "$ORIGEN/vm/dian.env.example" "$DEST/dian.env"
    echo "    (copiado del ejemplo — revisa la clave de aplicacion dentro)"
  fi
else
  echo "    ya existe, no se toca"
fi
chmod 600 "$DEST/dian.env"

log "6/9 entorno virtual de Python"
[[ -x "$DEST/venv/bin/python" ]] || python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --upgrade pip
"$DEST/venv/bin/pip" install --quiet -r "$DEST/requirements.txt"
"$DEST/venv/bin/python" -c 'import playwright, sys; print("    playwright", playwright.__version__ if hasattr(playwright,"__version__") else "ok", "en", sys.version.split()[0])' || true

log "7/9 Chromium de Playwright (+ librerias del sistema)"
# --with-deps instala las librerias compartidas que Chromium necesita. Solo
# anade paquetes; no reinicia ni reconfigura ningun servicio existente.
#
# --only-shell baja unicamente el "headless shell" (~314 MB), que es lo que
# usa launch(headless=True). Sin este flag se baja ademas el Chromium completo
# (~578 MB) que aqui no se usa nunca: en la VM no hay escritorio, asi que el
# modo --debug (ventana visible) no tiene sentido.
PLAYWRIGHT_BROWSERS_PATH="$DEST/browsers" \
  "$DEST/venv/bin/playwright" install --with-deps --only-shell chromium

# Restos de instalaciones anteriores (versiones viejas de Chromium, o el
# Chromium completo si se instalo sin --only-shell).
PLAYWRIGHT_BROWSERS_PATH="$DEST/browsers" \
  "$DEST/venv/bin/playwright" uninstall --unused 2>/dev/null || true
rm -rf "$DEST/browsers"/chromium-[0-9]* "$DEST/browsers"/ffmpeg-[0-9]*

log "8/9 permisos"
chown -R "$USUARIO:$USUARIO" "$DEST"
chmod 600 "$DEST/dian.env"

log "9/9 servicio y temporizador"
install -m 644 "$ORIGEN/vm/dian-monitor.service" /etc/systemd/system/dian-monitor.service
install -m 644 "$ORIGEN/vm/dian-monitor.timer"   /etc/systemd/system/dian-monitor.timer
systemctl daemon-reload
systemctl enable --now dian-monitor.timer >/dev/null
systemctl restart dian-monitor.timer

cat <<FIN

  Listo. El monitor revisa cada 30 minutos, entre las 5:00 y las 21:00 (hora de
  Colombia), y avisa por correo si aparecen cupos.

  Comprobar        : systemctl list-timers dian-monitor.timer
  Forzar una ahora : sudo systemctl start dian-monitor.service
  Ver el registro  : sudo tail -f /opt/dian/monitor.log
  Ver systemd      : journalctl -u dian-monitor -n 50 --no-pager
  Probar el correo : sudo systemd-run --wait --collect --pipe -p User=dian \\
                       -p EnvironmentFile=/opt/dian/dian.env \\
                       -p Environment=PLAYWRIGHT_BROWSERS_PATH=/opt/dian/browsers \\
                       -p Environment=TZ=America/Bogota -p WorkingDirectory=/opt/dian \\
                       /opt/dian/venv/bin/python /opt/dian/check_dian_v5.py --test-correo
  Apagarlo         : sudo systemctl disable --now dian-monitor.timer

FIN
