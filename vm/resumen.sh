#!/usr/bin/env bash
# Resumen de las corridas del monitor DIAN.
#
#   /opt/dian/resumen.sh [cuantas]     por defecto, las ultimas 15
#
# No necesita sudo: monitor.log es legible por todos.
# Con NO_COLOR=1 sale sin colores (por si la consola no los entiende).
set -uo pipefail

LOG=/opt/dian/monitor.log
N=${1:-15}
TZ_LOCAL=America/Bogota

# Colores solo si al otro lado hay una consola de verdad (ssh -t). Si la salida
# se esta canalizando a un archivo, salen los codigos crudos y estorban.
if [[ -n ${NO_COLOR:-} || ( ! -t 1 && -z ${FORCE_COLOR:-} ) ]]; then
  AZUL=""; GRIS=""; VERDE=""; ROJO=""; AMBAR=""; FIN=""
else
  AZUL=$'\033[1;36m'; GRIS=$'\033[0;90m'; VERDE=$'\033[1;32m'
  ROJO=$'\033[1;31m'; AMBAR=$'\033[1;33m'; FIN=$'\033[0m'
fi

echo
echo "${AZUL}══ MONITOR DE CITAS DIAN ═══════════════════════════════════════${FIN}"
echo "   Son las $(TZ=$TZ_LOCAL date '+%H:%M:%S') del $(TZ=$TZ_LOCAL date '+%d/%m/%Y') (hora de Colombia)"

# ── Estado del temporizador ───────────────────────────────────────────
activo=$(systemctl is-active dian-monitor.timer 2>/dev/null || echo desconocido)
if [[ $activo == active ]]; then
  echo "   Temporizador: ${VERDE}encendido${FIN} — dispara en punto y y media"
else
  echo "   Temporizador: ${ROJO}${activo}${FIN}"
fi

# systemd devuelve esto como fecha legible ("Fri 2026-08-07 19:00:00 UTC"),
# no como numero: hay que pasarselo a date tal cual para verlo en hora local.
sig=$(systemctl show dian-monitor.timer -p NextElapseUSecRealtime --value 2>/dev/null) || sig=""
if [[ -n $sig && $sig != "n/a" && $sig != "infinity" ]]; then
  prox=$(TZ=$TZ_LOCAL date -d "$sig" '+%H:%M:%S' 2>/dev/null) || prox=""
  [[ -n $prox ]] && echo "   Proxima revision: $prox"
fi

# ── Como termino la ultima ────────────────────────────────────────────
salida=$(systemctl show dian-monitor.service -p ExecMainStatus --value 2>/dev/null || echo "")
case "$salida" in
  0)  fin="hubo cupos (se envio correo)" ;;
  1)  fin="sin cupos" ;;
  2)  fin="error de la pagina" ;;
  3)  fin="fuera de horario, no reviso" ;;
  "") fin="todavia no ha corrido" ;;
  *)  fin="codigo $salida" ;;
esac
echo "   La ultima corrida termino en: $fin"

# ── Cuentas ───────────────────────────────────────────────────────────
if [[ -r $LOG ]]; then
  hoy=$(TZ=$TZ_LOCAL date '+%Y-%m-%d')
  tot=$(grep -ac 'resultado: ' "$LOG") || tot=0
  hoy_n=$(grep -a "^\[$hoy" "$LOG" | grep -ac 'resultado: ') || hoy_n=0
  con=$(grep -ac 'resultado: hay_cupos' "$LOG") || con=0
  sin=$(grep -ac 'resultado: sin_cupos' "$LOG") || sin=0
  err=$(grep -ac 'resultado: error' "$LOG") || err=0
  echo "   Revisiones: $tot en total, $hoy_n hoy  |  con cupos $con · sin cupos $sin · con error $err"
fi
echo

# ── Las ultimas N corridas ────────────────────────────────────────────
echo "${AZUL}── Ultimas $N revisiones ────────────────────────────────────────${FIN}"
if [[ ! -r $LOG ]]; then
  echo "   (todavia no hay registro en $LOG)"
else
  grep -a 'resultado: ' "$LOG" | tail -n "$N" | \
    awk -v verde="$VERDE" -v gris="$GRIS" -v rojo="$ROJO" -v fin="$FIN" -F'resultado: ' '
    {
      ts = substr($0, 2, 19)
      resto = $2
      sp = index(resto, " ")
      estado  = (sp ? substr(resto, 1, sp - 1) : resto)
      detalle = (sp ? substr(resto, sp + 1) : "")
      sub(/^[^A-Za-z0-9[]+/, "", detalle)          # quita la raya inicial
      if (length(detalle) > 62) detalle = substr(detalle, 1, 59) "..."

      if (estado == "hay_cupos")      { color = verde; texto = "HAY CUPOS" }
      else if (estado == "sin_cupos") { color = gris;  texto = "sin cupos" }
      else                            { color = rojo;  texto = "ERROR    " }

      printf "   %s  %s%s%s  %s\n", ts, color, texto, fin, detalle
    }'

  # Corridas que ni llegaron a dar resultado (Chromium no arranco, etc.)
  abiertas=$(grep -ac '^\[.*\] Revision — ' "$LOG") || abiertas=0
  cerradas=$(grep -ac 'resultado: ' "$LOG") || cerradas=0
  if (( abiertas > cerradas )); then
    echo
    echo "   ${AMBAR}$((abiertas - cerradas)) corrida(s) no llegaron a terminar${FIN} — mira: journalctl -u dian-monitor -n 40"
  fi
fi
echo

# ── Correos y salud ───────────────────────────────────────────────────
if [[ -r $LOG ]]; then
  ult=$(grep -a 'correo enviado' "$LOG" | tail -1) || ult=""
  if [[ -n $ult ]]; then
    echo "${GRIS}   Ultimo correo enviado: ${ult:1:19}${FIN}"
  else
    echo "${GRIS}   Todavia no se ha enviado ningun aviso.${FIN}"
  fi
fi

peso=$(du -sh /opt/dian 2>/dev/null | cut -f1)
libre=$(df -h / | tail -1 | awk '{print $4}')
logk=$(du -h "$LOG" 2>/dev/null | cut -f1)
echo "${GRIS}   Ocupa $peso (registro $logk) · quedan $libre libres en la VM${FIN}"
echo
