#!/usr/bin/env python3
"""
Monitor de citas DIAN, devolucion IVA (certificado UPME)
=========================================================

v6. Que cambio frente a la v5:

  * Revisa cada pocos minutos (180 s por defecto) en vez de cada 30.
  * Mira las dos modalidades, videoatencion y presencial, en la misma pasada.
  * Avisa al celular al instante y por varios canales a la vez, cada uno
    opcional: ntfy (push con alarma), Telegram, llamada telefonica (Twilio),
    WhatsApp (CallMeBot) y correo. El aviso trae un boton que abre la pagina.
  * Modo asistido para el PC (--abrir-navegador): el navegador trabaja fuera de
    la pantalla y, cuando hay cupos, aparece al frente ya parado en la lista de
    citas de Devoluciones, con alarma sonora. Solo falta escoger y confirmar.
  * Cuando hay cupos guarda el HTML de esa pantalla (salida/cupos_*.html). Con
    eso se puede automatizar el resto del agendamiento la proxima vez.
  * Latido diario: un aviso suave al dia confirma que el monitor sigue vivo.

CONFIGURACION
    Todo por variables de entorno. En el PC se pueden escribir en un archivo
    dian.env junto a este script (KEY=valor, una por linea); las variables del
    sistema tienen prioridad. Ese archivo nunca sube al repositorio.

    Ver LEEME.md para la lista completa y como sacar cada clave.

Uso:
    python check_dian_v5.py --test-avisos               # prueba todos los canales
    python check_dian_v5.py --test-avisos --con-llamada # incluye la llamada
    python check_dian_v5.py --una-vez --debug           # una revision, ventana visible
    python check_dian_v5.py --vigilar                   # bucle continuo
    python check_dian_v5.py --vigilar --abrir-navegador # bucle en el PC, modo asistido
    python check_dian_v5.py --vigilar --duracion-min 330 --respetar-horario
                                                        # para GitHub Actions
    python check_dian_v5.py --una-vez --respetar-horario
                                                        # para temporizadores (systemd)
    python check_dian_v5.py --limpiar                   # solo limpia y termina
"""

import argparse
import base64
import json
import os
import re
import shutil
import smtplib
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

VERSION = "6.4"
CARPETA = Path(__file__).resolve().parent


def _cargar_env_local() -> None:
    """Lee dian.env junto al script, sin pisar variables ya definidas."""
    archivo = CARPETA / "dian.env"
    if not archivo.is_file():
        return
    for linea in archivo.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave, valor = clave.strip(), valor.strip().strip('"').strip("'")
        if clave and clave not in os.environ:
            os.environ[clave] = valor


_cargar_env_local()


def _env(nombre: str, defecto: str = "") -> str:
    return os.getenv(nombre, defecto).strip()


# ══════════════════════════════════════════════════════════════════
# CONFIGURACION (todo por variables de entorno)
# ══════════════════════════════════════════════════════════════════
# Correo (opcional). La clave es una contrasena de aplicacion de Google.
CORREO_DESTINO = _env("DIAN_CORREO_DESTINO")
CORREO_ORIGEN = _env("DIAN_CORREO_ORIGEN")
CLAVE_APP = _env("DIAN_CLAVE_APP")

# ntfy (recomendado): app gratis en el celular, push con alarma.
NTFY_TOPIC = _env("DIAN_NTFY_TOPIC")
NTFY_SERVER = _env("DIAN_NTFY_SERVER", "https://ntfy.sh").rstrip("/")

# Telegram: token de @BotFather y tu chat_id.
TELEGRAM_TOKEN = _env("DIAN_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _env("DIAN_TELEGRAM_CHAT_ID")

# Llamada telefonica real (Twilio, de pago, centavos por llamada).
TWILIO_SID = _env("DIAN_TWILIO_SID")
TWILIO_TOKEN = _env("DIAN_TWILIO_TOKEN")
TWILIO_FROM = _env("DIAN_TWILIO_FROM")
TWILIO_TO = _env("DIAN_TWILIO_TO")

# WhatsApp (CallMeBot, gratis para uso personal, no es servicio oficial).
WHATSAPP_PHONE = _env("DIAN_WHATSAPP_PHONE")
WHATSAPP_APIKEY = _env("DIAN_WHATSAPP_APIKEY")

# Ritmo de la vigilancia
_intervalo_min = _env("DIAN_INTERVALO_MIN")
INTERVALO_SEG = int(_env("DIAN_INTERVALO_SEG")
                    or (int(_intervalo_min) * 60 if _intervalo_min else 180))
COOLDOWN_AVISO_MIN = int(_env("DIAN_COOLDOWN_AVISO_MIN", "10"))
COOLDOWN_LLAMADA_MIN = int(_env("DIAN_COOLDOWN_LLAMADA_MIN", "30"))
LATIDO_DIARIO = _env("DIAN_LATIDO", "1") not in ("0", "no", "false", "")
HORA_LATIDO = int(_env("DIAN_HORA_LATIDO", "8"))

# Horario de vigilancia (hora local). Fuera de esto no revisa.
HORA_INICIO = int(_env("DIAN_HORA_INICIO", "5"))
HORA_FIN = int(_env("DIAN_HORA_FIN", "21"))

# Modalidades: "ambas", "videoatencion", "presencial" o lista separada por comas
MODALIDAD = _env("DIAN_MODALIDAD", "ambas")

# Ciudad (seccional) que sirve. Las demas solo generan un aviso suave.
CIUDAD = _env("DIAN_CIUDAD", "Bogota")
RE_CIUDAD = re.compile(re.escape(CIUDAD[:5]).replace("o", "[oó]"), re.I)
AVISAR_OTRAS = _env("DIAN_AVISAR_OTRAS", "1") not in ("0", "no", "false", "")
COOLDOWN_OTRAS_MIN = int(_env("DIAN_COOLDOWN_OTRAS_MIN", "120"))

# Navegador: por defecto el Chromium de Playwright. En Windows se puede usar el
# Edge o el Chrome ya instalados con DIAN_NAVEGADOR_CANAL=msedge o chrome.
NAVEGADOR_CANAL = _env("DIAN_NAVEGADOR_CANAL")
NAVEGADOR_RUTA = _env("DIAN_CHROME_PATH")

# Limpieza (evita que la maquina se llene)
MAX_LOG_BYTES = int(_env("DIAN_MAX_LOG_BYTES", "1000000"))
LINEAS_A_DEJAR = int(_env("DIAN_LINEAS_LOG", "3000"))
DIAS_CAPTURAS = int(_env("DIAN_DIAS_CAPTURAS", "3"))
MAX_SALIDA_MB = int(_env("DIAN_MAX_SALIDA_MB", "50"))
# ══════════════════════════════════════════════════════════════════

URL = _env("DIAN_URL", "https://agendamiento.dian.gov.co/")
TEXTO_SIN_CUPOS = "No se encontraron especialidades"
LLAVE_MODALIDAD = {"presencial": "1", "videoatencion": "2"}
NOMBRE_MODALIDAD = {"presencial": "Presencial", "videoatencion": "Videoatención"}
SEL_AGENDAR = 'div[nombre="btnSolicitarCita"]'
# Cortina negra con la palabra "Cargando" que la pagina pone encima mientras
# consulta. Mientras este visible, lo que se lea en pantalla no vale nada.
SEL_CARGANDO = "#mpcWPdivCargando"

# La pagina de la DIAN se pone lenta a ratos: medido el 2026-08-12, el HTML
# inicial tardo entre 52 y 118 segundos y la interfaz aparecio unos 12 segundos
# despues. Los margenes dejan holgura sobre el peor caso visto.
TIMEOUT_CARGA_MS = int(_env("DIAN_TIMEOUT_CARGA_MS", "180000"))
TIMEOUT_BOTON_MS = int(_env("DIAN_TIMEOUT_BOTON_MS", "120000"))
TIMEOUT_CLIC_MS = int(_env("DIAN_TIMEOUT_CLIC_MS", "45000"))
TIMEOUT_CARGANDO_MS = int(_env("DIAN_TIMEOUT_CARGANDO_MS", "150000"))
INTENTOS_CARGA = int(_env("DIAN_INTENTOS_CARGA", "2"))
# Margen tras irse la cortina: el modal de "sin cupos" tarda un instante en
# pintarse. Si en este plazo no aparece, es que de verdad no lo hay.
ESPERA_MODAL_MS = int(_env("DIAN_ESPERA_MODAL_MS", "15000"))
# Modo asistido: cuanto se deja la pagina en pantalla esperando al usuario.
ESPERA_ASISTIDO_MIN = float(_env("DIAN_ESPERA_ASISTIDO_MIN", "20"))
# Modo rapido: deja la pagina abierta en Videoatencion y vuelve a consultar
# Devoluciones cada RAPIDO_SEG segundos, sin recargar. Cada RECARGA_MIN minutos
# (o ante cualquier rareza) recarga todo, para no quedarse con una sesion vieja.
# 0 = modo clasico (pagina completa cada DIAN_INTERVALO_SEG).
RAPIDO_SEG = int(_env("DIAN_RAPIDO_SEG", "0"))
RECARGA_MIN = float(_env("DIAN_RECARGA_MIN", "5"))
PRESENCIAL_CADA_MIN = float(_env("DIAN_PRESENCIAL_CADA_MIN", "30"))
# Mientras sigan visibles los cupos en la ciudad buscada, repetir el aviso
# urgente cada tantos segundos (hasta REPETIR_MAX veces por racha).
REPETIR_AVISO_SEG = int(_env("DIAN_REPETIR_AVISO_SEG", "60"))
REPETIR_MAX = int(_env("DIAN_REPETIR_MAX", "10"))

OUT_DIR = CARPETA / "salida"
OUT_DIR.mkdir(exist_ok=True)
LOG_FILE = CARPETA / "monitor.log"
ESTADO_FILE = OUT_DIR / "estado.json"
RESUMEN_FILE = OUT_DIR / "resumen.json"

# Chromium dentro de una VM o de GitHub sin escritorio: sin estos argumentos
# revienta al arrancar (sandbox de Ubuntu 24.04 y /dev/shm pequeno).
ARGS_LINUX = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-first-run",
    "--mute-audio",
]
# Modo asistido: la ventana nace fuera de la pantalla y solo se trae al frente
# cuando hay cupos. Asi no estorba cada pocos minutos mientras se usa el PC.
ARGS_OCULTO = ["--window-position=-2600,-2600", "--window-size=1400,1000"]

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def log(msg: str) -> None:
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(linea, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


def modalidades_configuradas(valor: str | None = None) -> list[str]:
    v = (MODALIDAD if valor is None else valor or "").strip().lower()
    if v in ("", "ambas", "todas", "all"):
        return ["presencial", "videoatencion"]
    lista = [m.strip() for m in v.split(",") if m.strip()]
    malas = [m for m in lista if m not in LLAVE_MODALIDAD]
    if malas:
        raise SystemExit(f"DIAN_MODALIDAD no reconocida: {', '.join(malas)} "
                         "(usa ambas, presencial o videoatencion)")
    return lista


# ──────────────────────────────────────────────────────────────────
# ESTADO PERSISTENTE (enfriamiento de avisos y latido diario)
# ──────────────────────────────────────────────────────────────────
def leer_estado() -> dict:
    try:
        return json.loads(ESTADO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_estado(estado: dict) -> None:
    try:
        tmp = ESTADO_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(ESTADO_FILE)
    except OSError as e:
        log(f"!! no se pudo guardar el estado: {e}")


# ──────────────────────────────────────────────────────────────────
# LIMPIEZA: se ejecuta al principio de cada revision
# ──────────────────────────────────────────────────────────────────
def _recortar_log() -> str | None:
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size <= MAX_LOG_BYTES:
        return None
    antes = LOG_FILE.stat().st_size
    try:
        lineas = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    tmp = LOG_FILE.with_suffix(".log.tmp")
    tmp.write_text("\n".join(lineas[-LINEAS_A_DEJAR:]) + "\n", encoding="utf-8")
    tmp.replace(LOG_FILE)
    return f"log recortado ({antes // 1024} KB -> {LOG_FILE.stat().st_size // 1024} KB)"


def _limpiar_capturas() -> str | None:
    """Borra capturas y HTML viejos; si aun pesa demasiado, los mas antiguos."""
    if not OUT_DIR.is_dir():
        return None
    limite = time.time() - DIAS_CAPTURAS * 86400
    borradas = 0
    patrones = ("*.png", "*.html")

    def archivos() -> list[Path]:
        return [p for pat in patrones for p in OUT_DIR.glob(pat) if p.is_file()]

    for f in archivos():
        try:
            if f.stat().st_mtime < limite:
                f.unlink()
                borradas += 1
        except OSError:
            pass
    tope = MAX_SALIDA_MB * 1024 * 1024
    try:
        while sum(p.stat().st_size for p in archivos()) > tope:
            viejos = sorted(archivos(), key=lambda p: p.stat().st_mtime)
            if not viejos:
                break
            viejos[0].unlink()
            borradas += 1
    except OSError:
        pass
    return f"{borradas} archivo(s) viejo(s) de salida borrado(s)" if borradas else None


def _limpiar_temporales() -> str | None:
    """Perfiles y volcados que Chromium deja tirados cuando una corrida muere.

    Solo carpetas con nombre inequivoco de Playwright/Chromium y con mas de una
    hora, para no pisar una ejecucion en curso.
    """
    tmp = Path(tempfile.gettempdir())
    if not tmp.is_dir():
        return None
    patrones = ("playwright*", "playwright_chromiumdev_profile-*",
                ".org.chromium.Chromium.*", "Crashpad*", "chrome_*")
    limite = time.time() - 3600
    borrados = 0
    for patron in patrones:
        for resto in tmp.glob(patron):
            try:
                if resto.stat().st_mtime >= limite:
                    continue
                if resto.is_dir():
                    shutil.rmtree(resto, ignore_errors=True)
                else:
                    resto.unlink()
                borrados += 1
            except OSError:
                pass
    return f"{borrados} resto(s) temporal(es) eliminado(s)" if borrados else None


def _matar_navegadores_huerfanos() -> str | None:
    """Chromium de corridas anteriores que quedo colgado (solo Linux).

    Solo procesos de este mismo usuario cuyo ejecutable vive en la cache de
    navegadores de Playwright: nunca toca un navegador del escritorio.
    """
    if not sys.platform.startswith("linux"):
        return None
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    marca = os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "ms-playwright"
    mio, yo = os.getuid(), os.getpid()
    matados = 0
    for entrada in proc.iterdir():
        if not entrada.name.isdigit() or int(entrada.name) == yo:
            continue
        try:
            if entrada.stat().st_uid != mio:
                continue
            destino = os.readlink(entrada / "exe")
        except OSError:
            continue
        if marca not in destino:
            continue
        try:
            os.kill(int(entrada.name), 9)
            matados += 1
        except OSError:
            pass
    return f"{matados} navegador(es) huerfano(s) cerrado(s)" if matados else None


def limpiar(silencioso: bool = False) -> None:
    for tarea in (_recortar_log, _limpiar_capturas,
                  _limpiar_temporales, _matar_navegadores_huerfanos):
        try:
            detalle = tarea()
        except Exception as e:  # la limpieza nunca debe tumbar el monitor
            detalle = f"fallo en {tarea.__name__}: {type(e).__name__}: {e}"
        if detalle and not silencioso:
            log(f"   limpieza: {detalle}")


# ──────────────────────────────────────────────────────────────────
# AVISOS
# ──────────────────────────────────────────────────────────────────
def _http(url: str, *, datos: bytes | None = None, cabeceras: dict | None = None,
          metodo: str | None = None, usuario: tuple[str, str] | None = None,
          timeout: int = 25) -> tuple[int, str]:
    cab = {"User-Agent": f"monitor-dian/{VERSION}"}
    cab.update(cabeceras or {})
    if usuario:
        token = base64.b64encode(f"{usuario[0]}:{usuario[1]}".encode()).decode()
        cab["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, data=datos, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2000).decode("utf-8", "replace")


def _multipart(campos: dict, archivo: tuple[str, str, bytes] | None) -> tuple[bytes, str]:
    limite = uuid.uuid4().hex
    partes: list[bytes] = []
    for k, v in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"'
                      f"\r\n\r\n{v}\r\n".encode())
    if archivo:
        nombre_campo, nombre, contenido = archivo
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; '
                      f'name="{nombre_campo}"; filename="{nombre}"\r\n'
                      "Content-Type: image/png\r\n\r\n".encode() + contenido + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def _aviso_ntfy(titulo, texto, captura, urgente):
    if not NTFY_TOPIC:
        return None
    cuerpo = {
        "topic": NTFY_TOPIC,
        "title": titulo,
        "message": texto,
        "priority": 5 if urgente else 2,
        "tags": ["rotating_light"] if urgente else ["white_check_mark"],
        "click": URL,
        "actions": [{"action": "view", "label": "Abrir agendamiento DIAN",
                     "url": URL, "clear": True}],
    }
    st, resp = _http(NTFY_SERVER, datos=json.dumps(cuerpo).encode(),
                     cabeceras={"Content-Type": "application/json"}, metodo="POST")
    return st < 300, f"HTTP {st}" + ("" if st < 300 else f" {resp[:150]}")


def _aviso_telegram(titulo, texto, captura, urgente):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return None
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    cuerpo = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{titulo}\n\n{texto}",
        "disable_web_page_preview": True,
        "disable_notification": not urgente,
        "reply_markup": {"inline_keyboard": [[{"text": "Abrir agendamiento DIAN",
                                               "url": URL}]]},
    }
    st, resp = _http(f"{base}/sendMessage", datos=json.dumps(cuerpo).encode(),
                     cabeceras={"Content-Type": "application/json"}, metodo="POST")
    if st >= 300:
        return False, f"HTTP {st} {resp[:150]}"
    if captura and Path(captura).is_file():
        datos, tipo = _multipart({"chat_id": TELEGRAM_CHAT_ID,
                                  "caption": "Lo que vio el monitor"},
                                 ("photo", Path(captura).name, Path(captura).read_bytes()))
        st2, _ = _http(f"{base}/sendPhoto", datos=datos,
                       cabeceras={"Content-Type": tipo}, metodo="POST", timeout=60)
        return True, f"HTTP {st} (foto HTTP {st2})"
    return True, f"HTTP {st}"


def _aviso_whatsapp(titulo, texto, captura, urgente):
    if not (WHATSAPP_PHONE and WHATSAPP_APIKEY):
        return None
    q = urllib.parse.urlencode({"phone": WHATSAPP_PHONE, "apikey": WHATSAPP_APIKEY,
                                "text": f"{titulo}\n{texto}"})
    st, resp = _http(f"https://api.callmebot.com/whatsapp.php?{q}")
    ok = st < 300 and "error" not in resp.lower()
    return ok, f"HTTP {st}" + ("" if ok else f" {resp[:150]}")


def _aviso_llamada(titulo, texto, captura, urgente):
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and TWILIO_TO):
        return None
    frase = ("Atención. Hay citas disponibles en la DIAN para devoluciones. "
             "Entra ya a la página de agendamiento.")
    say = f'<Say voice="Polly.Mia" language="es-MX">{frase}</Say>'
    twiml = f'<Response>{say}<Pause length="1"/>{say}</Response>'
    datos = urllib.parse.urlencode({"To": TWILIO_TO, "From": TWILIO_FROM,
                                    "Twiml": twiml}).encode()
    st, resp = _http(f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json",
                     datos=datos, metodo="POST",
                     cabeceras={"Content-Type": "application/x-www-form-urlencoded"},
                     usuario=(TWILIO_SID, TWILIO_TOKEN))
    return st < 300, f"HTTP {st}" + ("" if st < 300 else f" {resp[:150]}")


def _aviso_correo(titulo, texto, captura, urgente):
    if not (CLAVE_APP and CORREO_DESTINO and CORREO_ORIGEN) or "xxxx" in CLAVE_APP:
        return None
    msg = EmailMessage()
    msg["Subject"] = titulo
    msg["From"] = CORREO_ORIGEN
    msg["To"] = CORREO_DESTINO
    msg.set_content(texto)
    if captura and Path(captura).is_file():
        msg.add_attachment(Path(captura).read_bytes(), maintype="image",
                           subtype="png", filename=Path(captura).name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(CORREO_ORIGEN, CLAVE_APP.replace(" ", ""))
        s.send_message(msg)
    return True, f"-> {CORREO_DESTINO}"


CANALES = {
    "ntfy": _aviso_ntfy,
    "telegram": _aviso_telegram,
    "whatsapp": _aviso_whatsapp,
    "correo": _aviso_correo,
    "llamada": _aviso_llamada,
}


def canales_configurados() -> list[str]:
    return [n for n, ok in (
        ("ntfy", NTFY_TOPIC),
        ("telegram", TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
        ("whatsapp", WHATSAPP_PHONE and WHATSAPP_APIKEY),
        ("correo", CLAVE_APP and CORREO_DESTINO and CORREO_ORIGEN),
        ("llamada", TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and TWILIO_TO),
    ) if ok]


def avisar(titulo: str, texto: str, captura: Path | None = None, *,
           urgente: bool = True, llamar: bool = False,
           solo: tuple[str, ...] | None = None) -> dict:
    """Manda el aviso por todos los canales configurados, en paralelo."""
    nombres = [n for n in CANALES if (n != "llamada" or llamar)]
    if solo:
        nombres = [n for n in nombres if n in solo]

    def uno(nombre):
        try:
            return nombre, CANALES[nombre](titulo, texto, captura, urgente)
        except Exception as e:
            return nombre, (False, f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=len(nombres) or 1) as ex:
        resultados = dict(ex.map(uno, nombres))

    enviados = 0
    for nombre, r in resultados.items():
        if r is None:
            continue
        ok, detalle = r
        enviados += ok
        log(f"   aviso {nombre}: {'ok' if ok else 'FALLO'} {detalle}")
    if not any(r is not None for r in resultados.values()):
        log("!! no hay ningun canal de aviso configurado (ver LEEME.md)")
    return resultados


# Compatibilidad con la v5
def enviar_correo(asunto: str, cuerpo: str, adjunto: Path | None = None) -> bool:
    r = avisar(asunto, cuerpo, adjunto, urgente=False, solo=("correo",))
    return bool(r.get("correo") and r["correo"][0])


# ──────────────────────────────────────────────────────────────────
# ALARMA LOCAL (solo modo asistido)
# ──────────────────────────────────────────────────────────────────
def _alarma(parar: threading.Event, segundos: int = 120) -> None:
    fin = time.monotonic() + segundos
    try:
        import winsound  # solo existe en Windows
    except ImportError:
        winsound = None
    while not parar.is_set() and time.monotonic() < fin:
        try:
            if winsound:
                winsound.Beep(1200, 350)
                winsound.Beep(800, 350)
            else:
                print("\a", end="", flush=True)
                time.sleep(0.7)
        except Exception:
            time.sleep(0.7)
        parar.wait(0.5)


# ──────────────────────────────────────────────────────────────────
# NAVEGACION
# ──────────────────────────────────────────────────────────────────
def esperar_sin_cortina(page) -> None:
    """Espera a que se vaya la cortina de 'Cargando'.

    Con la cortina puesta el aviso de "sin cupos" todavia no esta, y el monitor
    lo tomaria por un cupo libre. Si no se va, revienta con PWTimeout, que se
    cuenta como error, nunca como falsa alarma.
    """
    page.wait_for_selector(SEL_CARGANDO, state="hidden", timeout=TIMEOUT_CARGANDO_MS)


def clic(page, selector: str, etiqueta: str, *, paso="", debug=False, espera=2500):
    log(f"   clic -> {etiqueta}")
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=TIMEOUT_CLIC_MS)
    loc.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    loc.click(force=True)
    page.wait_for_timeout(600)
    esperar_sin_cortina(page)
    page.wait_for_timeout(espera)
    if paso and debug:
        page.screenshot(path=str(OUT_DIR / f"{paso}.png"), full_page=True)


def _abrir_pagina(page) -> None:
    # No se espera a "networkidle": esta pagina no siempre llega a estar quieta.
    for intento in range(1, INTENTOS_CARGA + 1):
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=TIMEOUT_CARGA_MS)
            page.wait_for_selector(SEL_AGENDAR, state="visible", timeout=TIMEOUT_BOTON_MS)
            break
        except PWTimeout:
            if intento == INTENTOS_CARGA:
                raise
            log(f"   pagina lenta, reintento {intento + 1}/{INTENTOS_CARGA}")
            page.wait_for_timeout(5000)
    page.wait_for_timeout(3000)


def _opciones_visibles(page) -> list[str]:
    """Textos de los botones visibles: sirven para automatizar el siguiente paso."""
    try:
        return page.evaluate(
            """() => Array.from(document.querySelectorAll('div[class*="btn"], button, a'))
                 .filter(e => e.offsetParent !== null)
                 .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
                 .filter(t => t && t.length < 120)
                 .slice(0, 60)""")
    except Exception:
        return []


def _recorrer(page, modalidad: str, debug: bool) -> dict:
    """Hace la ruta Agendar > Persona Natural > modalidad > Devoluciones."""
    nombre = NOMBRE_MODALIDAD[modalidad]
    captura = OUT_DIR / f"resultado_{modalidad}.png"
    try:
        _abrir_pagina(page)
        clic(page, SEL_AGENDAR, "Agendar cita", paso="01_agendar", debug=debug, espera=3000)
        clic(page, 'div.btnTipoPersona[llave="1"]', "Persona Natural",
             paso="02_persona", debug=debug)
        clic(page, f'div.btnTipoAtencion[llave="{LLAVE_MODALIDAD[modalidad]}"]', nombre,
             paso=f"03_{modalidad}", debug=debug, espera=3000)
        # Visto el 2026-11-03: en Presencial la lista de categorias no trae
        # Devoluciones (RUT y orientacion TAC, Aduanas, Cobranzas, Recaudo,
        # Defensoria). Eso no es un error ni un cupo: se reporta como "no
        # disponible" sin esperar 45 s. Si algun dia aparece, sigue la ruta.
        page.locator("div.btnCategoria").first.wait_for(state="visible",
                                                        timeout=TIMEOUT_CLIC_MS)
        categorias = page.evaluate(
            """() => Array.from(document.querySelectorAll('div.btnCategoria'))
                 .filter(e => e.offsetParent !== null)
                 .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
                 .filter(Boolean)""")
        if not any("devoluci" in c.lower() for c in categorias):
            page.screenshot(path=str(captura), full_page=True)
            return {"modalidad": modalidad, "estado": "sin_cupos", "no_aplica": True,
                    "detalle": (f"[{nombre}] Devoluciones no aparece en esta modalidad. "
                                f"Categorias: {' | '.join(categorias[:12])}"),
                    "captura": captura}
        sel_dev = 'div.btnCategoria:has(span.btnCatSpan:text-is("Devoluciones."))'
        if page.locator(sel_dev).count() == 0:
            sel_dev = 'div.btnCategoria:has-text("Devoluci")'
        antes = set(_opciones_visibles(page))
        clic(page, sel_dev, "Devoluciones", paso=f"04_dev_{modalidad}", debug=debug,
             espera=500)
        return _evaluar(page, modalidad, antes, captura_siempre=True)

    except Exception as e:
        return _resultado_error(page, modalidad, e)


JS_RESPUESTA = """texto => document.body.innerText.includes(texto) ||
  Array.from(document.querySelectorAll('div[contenido="servicio"] select option'))
       .some(o => o.value && o.value !== '0' && !/seleccion/i.test(o.text))"""


def _evaluar(page, modalidad: str, antes: set, *, captura_siempre: bool) -> dict:
    """Lee el resultado despues del clic en Devoluciones."""
    nombre = NOMBRE_MODALIDAD[modalidad]
    captura = OUT_DIR / f"resultado_{modalidad}.png"
    try:
        # Sale en cuanto aparece el aviso de sin cupos o la lista de servicios:
        # asi un cupo se detecta sin esperar los 15 s de margen.
        try:
            page.wait_for_function(JS_RESPUESTA, arg=TEXTO_SIN_CUPOS,
                                   timeout=ESPERA_MODAL_MS, polling=250)
        except PWTimeout:
            pass

        cuerpo = page.inner_text("body")
        if TEXTO_SIN_CUPOS.lower() in cuerpo.lower():
            if captura_siempre:
                page.screenshot(path=str(captura), full_page=True)
            return {"modalidad": modalidad, "estado": "sin_cupos",
                    "detalle": f"[{nombre}] sin cupos por ahora.", "captura": captura}
        page.screenshot(path=str(captura), full_page=True)

        sello = f"{datetime.now():%Y%m%d_%H%M%S}"
        html = OUT_DIR / f"cupos_{modalidad}_{sello}.html"
        try:
            html.write_text(page.content(), encoding="utf-8")
        except Exception:
            html = None

        # Visto en la pagina real (2026-11-05): cuando hay cupos, en el Paso 1
        # aparece una lista desplegable de SERVICIOS y la ciudad va dentro del
        # nombre de cada uno, por ejemplo:
        #   Bucaramanga "Solicitud de devolucion y/o compensacion persona natural".
        #   "Cali - Solicitud de devolucion y/o compensacion Vehiculos electricos
        #    o Hibridos persona natural"
        # La lista de ciudades del Paso 2 llega vacia en este flujo, asi que la
        # decision se toma con los servicios: si alguno es de Bogota, hay cita.
        # El Paso 3 (datos, captcha y documentos) no se toca.
        servicios = _leer_servicios(page)
        if servicios:
            for sv in servicios:
                sv["ciudad"] = _ciudad_de_servicio(sv["texto"])
                sv["vehiculos"] = bool(RE_SERVICIO.search(sv["texto"]))
            log("   servicios: " + " | ".join(sv["texto"] for sv in servicios)[:500])
            mios = [sv for sv in servicios if RE_CIUDAD.search(sv["texto"])]
            resumen = " | ".join(
                f"{sv['ciudad']} ({'vehiculos electricos' if sv['vehiculos'] else 'devolucion general'})"
                for sv in servicios)
            base = {"modalidad": modalidad, "captura": captura, "html": html,
                    "servicios": servicios, "ciudades": [sv["ciudad"] for sv in servicios],
                    "resumen_servicios": resumen}
            if mios:
                # Preferir el servicio de vehiculos electricos si hay de los dos.
                mios.sort(key=lambda sv: not sv["vehiculos"])
                elegido = mios[0]
                return {**base, "estado": "hay_cupos", "seguro": True, "en_ciudad": True,
                        "servicio": elegido["texto"], "servicio_valor": elegido["valor"],
                        "detalle": (f"[{nombre}] HAY AGENDA EN {CIUDAD.upper()}: "
                                    f"{elegido['texto']}. Todos: {resumen}")}
            return {**base, "estado": "otras_ciudades",
                    "detalle": f"[{nombre}] Hay agenda, pero no en {CIUDAD}: {resumen}"}

        # Confirmacion positiva: tras el clic en Devoluciones deben aparecer
        # opciones nuevas (especialidades, sedes, fechas). Si no aparece nada
        # nuevo y tampoco el aviso de sin cupos, puede ser un error de la
        # pagina: se avisa igual, pero como "posible" y sin llamada.
        opciones = _opciones_visibles(page)
        nuevas = [o for o in opciones if o not in antes]
        seguro = bool(nuevas)
        return {"modalidad": modalidad, "estado": "hay_cupos", "seguro": seguro,
                "detalle": (f"[{nombre}] NO aparecio el aviso de sin cupos. "
                            + (f"Opciones nuevas: {' | '.join(nuevas[:20])}" if seguro
                               else "No aparecieron opciones nuevas; revisar a mano.")),
                "captura": captura, "html": html, "opciones": opciones}

    except Exception as e:
        return _resultado_error(page, modalidad, e)


def _resultado_error(page, modalidad: str, e: Exception) -> dict:
    nombre = NOMBRE_MODALIDAD[modalidad]
    err = OUT_DIR / f"error_{modalidad}.png"
    try:
        page.screenshot(path=str(err), full_page=True)
    except Exception:
        err = None
    tipo = "Timeout" if isinstance(e, PWTimeout) else type(e).__name__
    # Lo que habia en pantalla al fallar: dice en que paso cambio la pagina.
    visibles = _opciones_visibles(page)[:20]
    primera = str(e).splitlines()[0][:200] if str(e) else ""
    return {"modalidad": modalidad, "estado": "error",
            "detalle": (f"[{nombre}] {tipo}: {primera} | En pantalla: "
                        f"{' | '.join(visibles) or '(nada legible)'}"),
            "captura": err}


SEL_SERVICIOS = 'div[contenido="servicio"] select'
SEL_SIGUIENTE_1 = 'div[contenido="botonesPasoUno"] > div[nombreControl="btnSiguiente"]'
SEL_CIUDADES = 'div[contenido="Ciudad"] select'
RE_SERVICIO = re.compile(r"veh[ií]cul|el[eé]ctric|h[ií]brid", re.I)

JS_OPCIONES = """sel => Array.from(document.querySelectorAll(sel))
  .filter(s => s.offsetParent !== null || s.closest('[contenido]')?.offsetParent !== null)
  .flatMap(s => Array.from(s.options))
  .filter(o => o.value && o.value !== '0' && o.text.trim() && !/seleccion/i.test(o.text))
  .map(o => ({valor: o.value, texto: o.text.trim()}))"""


def _opciones_select(page, selector: str) -> list[dict]:
    try:
        return page.evaluate(JS_OPCIONES, selector)
    except Exception:
        return []


def _leer_servicios(page) -> list[dict]:
    """Opciones de la lista de servicios del Paso 1 (vacia si no aparece)."""
    fin = time.monotonic() + 8
    servicios = []
    while time.monotonic() < fin and not servicios:
        servicios = _opciones_select(page, SEL_SERVICIOS)
        if not servicios:
            page.wait_for_timeout(500)
    return servicios


RE_COMILLAS = "\"'“”«»"


def _ciudad_de_servicio(texto: str) -> str:
    """Saca la ciudad del nombre del servicio. Formatos vistos:
    'Bucaramanga "Solicitud de ..."'  y  '"Cali - Solicitud de ..."'."""
    t = texto.strip()
    m = re.match(r"^\s*([^\"“”-]+?)\s*[\"“”]", t)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.match(r"^[\s\"“”]*([^\"“”-]+?)\s*[-–]", t)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return t.strip(RE_COMILLAS + " .")[:40]


def _avanzar_a_paso2(page, servicio_valor: str) -> None:
    """Modo asistido: deja escogido el servicio de Bogota y pasa al Paso 2."""
    try:
        page.locator(SEL_SERVICIOS).first.select_option(value=servicio_valor)
        page.wait_for_timeout(800)
        esperar_sin_cortina(page)
        clic(page, SEL_SIGUIENTE_1, "Siguiente (paso 1)", espera=1500)
    except Exception as e:
        log(f"   no pude avanzar al paso 2 ({type(e).__name__}); escoge el servicio a mano")


def _traer_al_frente(page) -> None:
    try:
        cdp = page.context.new_cdp_session(page)
        win = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": win,
                                             "bounds": {"windowState": "normal"}})
        cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {
            "left": 20, "top": 20, "width": 1400, "height": 1000}})
    except Exception as e:
        log(f"   no se pudo mover la ventana ({type(e).__name__}); buscala en la barra de tareas")
    try:
        page.bring_to_front()
    except Exception:
        pass


def _entregar_al_usuario(page) -> None:
    """Modo asistido: deja la pagina en pantalla para que el usuario agende."""
    log(">>> PAGINA ABIERTA EN TU PANTALLA: escoge la cita y confirma. "
        "Cierra la ventana cuando termines.")
    _traer_al_frente(page)
    parar = threading.Event()
    hilo = threading.Thread(target=_alarma, args=(parar,), daemon=True)
    hilo.start()
    # Se espera a que el usuario cierre la ventana, con tope: si no estaba en el
    # PC, a los DIAN_ESPERA_ASISTIDO_MIN minutos se retoma la vigilancia.
    fin = time.monotonic() + ESPERA_ASISTIDO_MIN * 60
    cerrada = False
    try:
        while time.monotonic() < fin:
            if page.is_closed():
                cerrada = True
                break
            page.wait_for_timeout(1000)
    except Exception:
        cerrada = True  # cerro el navegador entero
    finally:
        parar.set()
    log("   ventana cerrada, sigue la vigilancia" if cerrada
        else f"   nadie cerro la ventana en {ESPERA_ASISTIDO_MIN:g} min, sigue la vigilancia")


def _lanzar(p, *, debug: bool = False, asistido: bool = False):
    args = ARGS_LINUX if sys.platform.startswith("linux") else []
    visible = debug or asistido
    if asistido and not debug:
        args = args + ARGS_OCULTO
    opciones = {"headless": not visible, "args": args}
    if NAVEGADOR_RUTA:
        opciones["executable_path"] = NAVEGADOR_RUTA
    elif NAVEGADOR_CANAL:
        opciones["channel"] = NAVEGADOR_CANAL
    navegador = p.chromium.launch(**opciones)
    ctx = navegador.new_context(viewport={"width": 1400, "height": 1000},
                                locale="es-CO", user_agent=USER_AGENT)
    return navegador, ctx


def _sel_devoluciones(page) -> str:
    sel = 'div.btnCategoria:has(span.btnCatSpan:text-is("Devoluciones."))'
    if page.locator(sel).count() == 0:
        sel = 'div.btnCategoria:has-text("Devoluci")'
    return sel


class Consultas:
    """Cuenta las consultas de cupos que la pagina le hace a la DIAN."""

    def __init__(self, page):
        self.n = 0
        page.on("request", lambda r: self._ver(r))

    def _ver(self, r):
        if r.method == "POST" and "ValidadorValidar" in r.url:
            self.n += 1


_motivos_vistos: set = set()


def _motivo_recarga(motivo: str, page) -> None:
    """Registra por que no se pudo rechequear (cada motivo una sola vez)."""
    if motivo in _motivos_vistos:
        return
    _motivos_vistos.add(motivo)
    visibles = " | ".join(_opciones_visibles(page)[:15])
    log(f"   rechequeo: {motivo}; recargo. En pantalla: {visibles or '(nada legible)'}")
    try:
        (OUT_DIR / "rechequeo.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT_DIR / "rechequeo.png"), full_page=True)
    except Exception:
        pass


def _rechequear(page, modalidad: str, consultas: "Consultas") -> dict | None:
    """Vuelve a consultar Devoluciones sin recargar la pagina.

    Devuelve None si algo no cuadra (el aviso no se cerro, no esta el boton, la
    pagina no hizo una consulta nueva...): quien llama recarga todo.
    """
    try:
        for sel in ('div[pantalla="ModalError"][nombre="btnAceptar"]',
                    'div[pantalla="ModalError"][nombre="btnCerrarModal"]'):
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(force=True)
                break
        page.wait_for_timeout(300)
        esperar_sin_cortina(page)
        try:
            page.wait_for_selector(f"text={TEXTO_SIN_CUPOS}", state="hidden", timeout=5000)
        except PWTimeout:
            _motivo_recarga("el aviso de sin cupos no se cerro", page)
            return None
        sel_dev = _sel_devoluciones(page)
        boton = page.locator(sel_dev).first
        if not boton.count() or not boton.is_visible():
            _motivo_recarga("tras cerrar el aviso no esta el boton Devoluciones", page)
            return None
        antes_n = consultas.n
        antes = set(_opciones_visibles(page))
        boton.click(force=True)
        page.wait_for_timeout(400)
        esperar_sin_cortina(page)
        r = _evaluar(page, modalidad, antes, captura_siempre=False)
        if consultas.n == antes_n:
            # El clic no le pregunto nada nuevo a la DIAN: el resultado no vale.
            log("   la pagina no hizo una consulta nueva; recargo")
            return None
        return r
    except Exception as e:
        log(f"   rechequeo fallo ({type(e).__name__}); recargo")
        return None


def revisar(modalidades: list[str], *, debug: bool = False, asistido: bool = False,
            al_detectar=None, al_otras=None) -> list[dict]:
    """Revisa cada modalidad en el mismo navegador y devuelve los resultados.

    al_detectar(resultado) se llama en cuanto una modalidad muestra cupos, antes
    de mirar las demas: el aviso no espera a nada.
    """
    resultados: list[dict] = []
    with sync_playwright() as p:
        navegador = None
        try:
            # El arranque va dentro del try: si falla, es un error, no "sin cupos".
            navegador, ctx = _lanzar(p, debug=debug, asistido=asistido)
            for modalidad in modalidades:
                page = ctx.new_page()
                r = _recorrer(page, modalidad, debug)
                resultados.append(r)
                log(f"   {r['estado']}: {r['detalle'][:400]}")
                if r["estado"] == "hay_cupos":
                    if al_detectar:
                        al_detectar(r)
                    if asistido:
                        if r.get("en_ciudad") and r.get("servicio_valor"):
                            _avanzar_a_paso2(page, r["servicio_valor"])
                        _entregar_al_usuario(page)
                        break
                elif r["estado"] == "otras_ciudades" and al_otras:
                    al_otras(r)
                try:
                    page.close()
                except Exception:
                    pass
        except Exception as e:
            resultados.append({"modalidad": "navegador", "estado": "error",
                               "detalle": f"{type(e).__name__}: {str(e)[:300]}",
                               "captura": None})
        finally:
            try:
                if navegador:
                    navegador.close()
            except Exception:
                pass
    return resultados


def estado_global(resultados: list[dict]) -> str:
    estados = {r["estado"] for r in resultados}
    if "hay_cupos" in estados:
        return "hay_cupos"
    if "otras_ciudades" in estados:
        return "otras_ciudades"
    if "sin_cupos" in estados:
        return "sin_cupos"
    return "error"


# ──────────────────────────────────────────────────────────────────
# CICLO DE VIGILANCIA
# ──────────────────────────────────────────────────────────────────
def en_horario() -> bool:
    return HORA_INICIO <= datetime.now().hour < HORA_FIN


def _manejar_cupos(r: dict) -> None:
    """Avisa respetando el enfriamiento, para no llamar cada 3 minutos."""
    est = leer_estado()
    ahora = time.time()
    nombre = NOMBRE_MODALIDAD.get(r["modalidad"], r["modalidad"])
    ruta = f"Agendar cita > Persona Natural > {nombre} > Devoluciones"
    seguro = r.get("seguro", True)
    llamar = seguro and ahora - est.get("ultima_llamada", 0) >= COOLDOWN_LLAMADA_MIN * 60
    desde_ultimo = ahora - est.get("ultimo_aviso", 0)
    if r.get("en_ciudad"):
        # Cita en la ciudad buscada: se repite cada REPETIR_AVISO_SEG mientras
        # siga visible, para que no se pierda si no se vio el primer aviso.
        # Una racha nueva empieza si pasaron 10 min sin avisar.
        if desde_ultimo > 600:
            est["racha_avisos"] = 0
        if est.get("racha_avisos", 0) >= REPETIR_MAX:
            log("   cupos siguen visibles; ya se repitio el aviso el maximo de veces")
            return
        if not llamar and desde_ultimo < REPETIR_AVISO_SEG:
            return
        est["racha_avisos"] = est.get("racha_avisos", 0) + 1
    # Un cupo confirmado que todavia no ha generado llamada pasa siempre, aunque
    # hace poco haya salido un aviso de "posible cita".
    elif not llamar and desde_ultimo < COOLDOWN_AVISO_MIN * 60:
        log("   cupos siguen visibles; aviso ya enviado hace poco")
        return
    if r.get("en_ciudad"):
        titulo = f"🚨 CITA DIAN EN {CIUDAD.upper()} DISPONIBLE ({nombre})"
        cuerpo = ("Hay agenda en tu ciudad. Entra YA, se agotan en minutos.\n\n"
                  f"{URL}\n\nRuta: {ruta} > Servicio: {r.get('servicio', '')}\n\n")
    else:
        titulo = (f"🚨 CITA DIAN DISPONIBLE ({nombre})" if seguro
                  else f"🚨 POSIBLE CITA DIAN ({nombre}), revisa ya")
        cuerpo = ("No salio el aviso de 'sin cupos'. Revisa ya si la ciudad es la "
                  f"tuya.\n\n{URL}\n\nRuta: {ruta}\n\n")
    avisar(titulo,
           cuerpo + f"Hora: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
           f"Detalle: {r['detalle'][:600]}",
           r.get("captura"), urgente=True, llamar=llamar)
    est["ultimo_aviso"] = ahora
    if llamar:
        est["ultima_llamada"] = ahora
    guardar_estado(est)


def _manejar_otras(r: dict) -> None:
    """Hay agenda, pero en otras ciudades: aviso suave, sin alarma ni llamada."""
    if not AVISAR_OTRAS:
        return
    est = leer_estado()
    ahora = time.time()
    lista = r.get("resumen_servicios") or " | ".join(r.get("ciudades") or [])
    # Se repite solo si cambio la lista o paso el enfriamiento.
    if (est.get("otras_lista") == lista
            and ahora - est.get("otras_aviso", 0) < COOLDOWN_OTRAS_MIN * 60):
        return
    ciudades = ", ".join(dict.fromkeys(r.get("ciudades") or [])) or "otras ciudades"
    avisar(f"Cupos DIAN en {ciudades} (no {CIUDAD})",
           f"Hay agenda en: {lista or '(no se leyo la lista)'}.\n"
           f"En {CIUDAD} no hay por ahora. No tienes que hacer nada; si aparece "
           f"{CIUDAD} te llega el aviso urgente.",
           urgente=False, solo=("ntfy", "telegram"))
    est["otras_lista"] = lista
    est["otras_aviso"] = ahora
    guardar_estado(est)


def _latido(revisiones: int) -> None:
    # Solo dentro de la hora del latido: asi sale una vez al dia aunque el
    # estado se pierda entre corridas de GitHub.
    if not LATIDO_DIARIO or datetime.now().hour != HORA_LATIDO:
        return
    est = leer_estado()
    hoy = f"{datetime.now():%Y-%m-%d}"
    if est.get("latido") == hoy:
        return
    avisar("Monitor DIAN activo",
           f"Sigue vigilando ({', '.join(modalidades_configuradas())}) cada "
           f"{INTERVALO_SEG // 60} min entre las {HORA_INICIO}:00 y las {HORA_FIN}:00. "
           "Este es el aviso diario de que esta vivo.",
           urgente=False, solo=("ntfy", "telegram"))
    est["latido"] = hoy
    guardar_estado(est)


def una_revision(*, debug: bool = False, asistido: bool = False,
                 modalidades: list[str] | None = None) -> tuple[str, list[dict]]:
    resultados = revisar(modalidades or modalidades_configuradas(), debug=debug,
                         asistido=asistido, al_detectar=_manejar_cupos,
                         al_otras=_manejar_otras)
    estado = estado_global(resultados)
    # La captura "resultado.png" es la que publica el workflow.
    elegida = next((r for r in resultados if r["estado"] == "hay_cupos"), None) \
        or next((r for r in resultados if r.get("captura")), None)
    if elegida and elegida.get("captura") and Path(elegida["captura"]).is_file():
        try:
            shutil.copyfile(elegida["captura"], OUT_DIR / "resultado.png")
        except OSError:
            pass
    return estado, resultados


def vigilar(*, duracion_min: int | None = None, salir_fuera_horario: bool = False,
            asistido: bool = False) -> dict:
    modalidades = modalidades_configuradas()
    log("=" * 62)
    log(f"VIGILANCIA v{VERSION}: cada {INTERVALO_SEG} s, entre las {HORA_INICIO}:00 "
        f"y las {HORA_FIN}:00 | modalidades: {', '.join(modalidades)}")
    log(f"Avisos por: {', '.join(canales_configurados()) or 'NINGUNO configurado'}"
        + (" | modo asistido" if asistido else ""))
    if duracion_min:
        log(f"Termina sola en {duracion_min} min")
    log("Ctrl+C para detener")
    log("=" * 62)

    inicio = time.monotonic()
    resumen = {"revisiones": 0, "sin_cupos": 0, "hay_cupos": 0, "otras_ciudades": 0,
               "error": 0,
               "inicio": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}
    fallos_seguidos = 0

    def queda_seg() -> float:
        return float("inf") if not duracion_min else duracion_min * 60 - (time.monotonic() - inicio)

    while queda_seg() > 0:
        if not en_horario():
            if salir_fuera_horario:
                log(f"Fuera de horario ({datetime.now().hour}:00), fin de esta tanda")
                break
            # Duerme en tramos de 10 min, pero despierta justo a la hora de
            # inicio para que la primera revision del dia no llegue tarde.
            ahora = datetime.now()
            inicio_hoy = ahora.replace(hour=HORA_INICIO, minute=0, second=5, microsecond=0)
            hasta_inicio = (inicio_hoy - ahora).total_seconds()
            if hasta_inicio <= 0:
                hasta_inicio = hasta_inicio + 86400
            log(f"Fuera de horario ({ahora.hour}:00), en espera")
            time.sleep(min(600, max(1, hasta_inicio), max(1, queda_seg())))
            continue
        # Una revision lenta tarda hasta ~10 min: no se empieza si no alcanza.
        if queda_seg() < 12 * 60:
            break

        t0 = time.monotonic()
        resumen["revisiones"] += 1
        log(f"Revision #{resumen['revisiones']}")
        limpiar()
        estado, _ = una_revision(asistido=asistido, modalidades=modalidades)
        resumen[estado] += 1
        if estado == "error":
            fallos_seguidos += 1
            if fallos_seguidos == 3 or (fallos_seguidos and fallos_seguidos % 20 == 0):
                avisar("⚠️ Monitor DIAN con errores",
                       f"Lleva {fallos_seguidos} revisiones seguidas sin poder leer la "
                       "pagina. Puede que este caida o que haya cambiado. Conviene "
                       f"revisar a mano: {URL}", urgente=False)
        else:
            fallos_seguidos = 0
            _latido(resumen["revisiones"])
        guardar_resumen(resumen)

        espera = INTERVALO_SEG - (time.monotonic() - t0)
        if espera > 0:
            time.sleep(min(espera, max(0, queda_seg())))

    log(f"Fin de la vigilancia: {resumen}")
    guardar_resumen(resumen)
    return resumen


def vigilar_rapido(*, duracion_min: int | None = None, asistido: bool = False) -> dict:
    """Como vigilar(), pero con la pagina abierta y una consulta cada RAPIDO_SEG."""
    modalidades = modalidades_configuradas()
    log("=" * 62)
    log(f"VIGILANCIA RAPIDA v{VERSION}: Videoatencion cada {RAPIDO_SEG} s (recarga "
        f"completa cada {RECARGA_MIN:g} min), entre las {HORA_INICIO}:00 y las "
        f"{HORA_FIN}:00" + (f" | Presencial cada {PRESENCIAL_CADA_MIN:g} min"
                             if "presencial" in modalidades else ""))
    log(f"Avisos por: {', '.join(canales_configurados()) or 'NINGUNO configurado'}"
        + (" | modo asistido" if asistido else ""))
    log("=" * 62)

    inicio = time.monotonic()
    resumen = {"revisiones": 0, "sin_cupos": 0, "hay_cupos": 0, "otras_ciudades": 0,
               "error": 0, "recargas": 0, "inicio": f"{datetime.now():%Y-%m-%d %H:%M:%S}"}
    fallos_seguidos = 0
    ultima_presencial = -1e9

    def queda_seg() -> float:
        return float("inf") if not duracion_min else duracion_min * 60 - (time.monotonic() - inicio)

    def procesar(r: dict) -> None:
        nonlocal fallos_seguidos
        resumen["revisiones"] += 1
        resumen[r["estado"]] += 1
        if r["estado"] != "sin_cupos" or resumen["revisiones"] % 20 == 1:
            log(f"   #{resumen['revisiones']} {r['estado']}: {r['detalle'][:400]}")
        if r["estado"] == "hay_cupos":
            _manejar_cupos(r)
        elif r["estado"] == "otras_ciudades":
            _manejar_otras(r)
        if r["estado"] == "error":
            fallos_seguidos += 1
            if fallos_seguidos == 5 or (fallos_seguidos and fallos_seguidos % 60 == 0):
                avisar("⚠️ Monitor DIAN con errores",
                       f"Lleva {fallos_seguidos} consultas seguidas sin poder leer la "
                       f"pagina. Puede que este caida. Conviene revisar a mano: {URL}",
                       urgente=False)
        else:
            fallos_seguidos = 0
            _latido(resumen["revisiones"])
        if r.get("captura") and Path(r["captura"]).is_file():
            try:
                shutil.copyfile(r["captura"], OUT_DIR / "resultado.png")
            except OSError:
                pass

    with sync_playwright() as p:
        navegador = ctx = page = consultas = None
        lista = False          # la pagina esta parada donde se puede rechequear
        t_carga = -1e9
        while queda_seg() > 0:
            if not en_horario():
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
                    page, lista = None, False
                ahora = datetime.now()
                inicio_hoy = ahora.replace(hour=HORA_INICIO, minute=0, second=5, microsecond=0)
                hasta = (inicio_hoy - ahora).total_seconds()
                if hasta <= 0:
                    hasta += 86400
                log(f"Fuera de horario ({ahora.hour}:00), en espera")
                time.sleep(min(600, max(1, hasta), max(1, queda_seg())))
                continue
            if queda_seg() < 3 * 60:
                break
            t0 = time.monotonic()
            try:
                if navegador is None:
                    navegador, ctx = _lanzar(p, asistido=asistido)
                if "presencial" in modalidades and \
                        time.monotonic() - ultima_presencial > PRESENCIAL_CADA_MIN * 60:
                    pg = ctx.new_page()
                    procesar(_recorrer(pg, "presencial", False))
                    pg.close()
                    ultima_presencial = time.monotonic()
                r = None
                if lista and page is not None and not page.is_closed() and \
                        time.monotonic() - t_carga < RECARGA_MIN * 60:
                    r = _rechequear(page, "videoatencion", consultas)
                if r is None:
                    if page is not None:
                        try:
                            page.close()
                        except Exception:
                            pass
                    page = ctx.new_page()
                    consultas = Consultas(page)
                    resumen["recargas"] += 1
                    r = _recorrer(page, "videoatencion", False)
                    t_carga = time.monotonic()
                procesar(r)
                # Solo se puede rechequear desde el aviso de "sin cupos".
                lista = r["estado"] == "sin_cupos"
                if r["estado"] == "hay_cupos" and asistido:
                    if r.get("en_ciudad") and r.get("servicio_valor"):
                        _avanzar_a_paso2(page, r["servicio_valor"])
                    _entregar_al_usuario(page)
                    page, lista = None, False
            except Exception as e:
                log(f"!! fallo del navegador ({type(e).__name__}: {str(e)[:150]}); reinicio")
                resumen["error"] += 1
                try:
                    if navegador:
                        navegador.close()
                except Exception:
                    pass
                navegador = ctx = page = None
                lista = False
            if resumen["revisiones"] % 10 == 0:
                guardar_resumen(resumen)
            espera = RAPIDO_SEG - (time.monotonic() - t0)
            if espera > 0:
                time.sleep(min(espera, max(0, queda_seg())))
        try:
            if navegador:
                navegador.close()
        except Exception:
            pass
    log(f"Fin de la vigilancia: {resumen}")
    guardar_resumen(resumen)
    return resumen


def guardar_resumen(resumen: dict) -> None:
    resumen = dict(resumen, actualizado=f"{datetime.now():%Y-%m-%d %H:%M:%S}")
    try:
        RESUMEN_FILE.write_text(json.dumps(resumen, indent=1), encoding="utf-8")
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=f"Monitor de citas DIAN v{VERSION}")
    ap.add_argument("--vigilar", action="store_true", help="bucle continuo")
    ap.add_argument("--una-vez", action="store_true", help="una sola revision y termina")
    ap.add_argument("--duracion-min", type=int, default=None,
                    help="con --vigilar: termina sola a los N minutos (GitHub Actions)")
    ap.add_argument("--abrir-navegador", action="store_true",
                    help="modo asistido en el PC: al haber cupos deja la pagina en pantalla")
    ap.add_argument("--modalidad", default=None,
                    help="ambas, presencial o videoatencion (pisa DIAN_MODALIDAD)")
    ap.add_argument("--test-avisos", action="store_true",
                    help="manda un aviso de prueba por cada canal configurado")
    ap.add_argument("--con-llamada", action="store_true",
                    help="con --test-avisos: incluye la llamada telefonica")
    ap.add_argument("--test-correo", action="store_true", help="prueba solo el correo")
    ap.add_argument("--debug", action="store_true",
                    help="navegador visible + capturas de cada paso")
    ap.add_argument("--respetar-horario", action="store_true",
                    help="fuera del horario sale sin revisar (systemd, GitHub)")
    ap.add_argument("--limpiar", action="store_true", help="solo limpia y termina")
    ap.add_argument("--sin-limpieza", action="store_true", help="no limpiar antes")
    args = ap.parse_args()

    global MODALIDAD
    if args.modalidad:
        MODALIDAD = args.modalidad
    modalidades_configuradas(MODALIDAD)  # valida antes de arrancar

    if args.limpiar:
        limpiar()
        log("limpieza terminada")
        sys.exit(0)

    if args.test_avisos:
        log(f"Canales configurados: {', '.join(canales_configurados()) or 'ninguno'}")
        r = avisar("Prueba: monitor de citas DIAN",
                   "Si te llego esto, este canal funciona. Cuando haya cupos el "
                   f"aviso trae un boton para abrir la pagina: {URL}",
                   urgente=True, llamar=args.con_llamada)
        ok = [n for n, v in r.items() if v and v[0]]
        sys.exit(0 if ok else 1)

    if args.test_correo:
        sys.exit(0 if enviar_correo(
            "Prueba: monitor de citas DIAN",
            "Si estas leyendo esto, el envio de correo funciona.") else 1)

    if args.respetar_horario and not en_horario() and not args.vigilar:
        log(f"Fuera de horario ({datetime.now().hour}:00), no se revisa")
        limpiar(silencioso=True)
        sys.exit(3)

    if not args.sin_limpieza:
        limpiar()

    if args.vigilar:
        try:
            if RAPIDO_SEG > 0:
                resumen = vigilar_rapido(duracion_min=args.duracion_min,
                                         asistido=args.abrir_navegador)
            else:
                resumen = vigilar(duracion_min=args.duracion_min,
                                  salir_fuera_horario=args.respetar_horario,
                                  asistido=args.abrir_navegador)
        except KeyboardInterrupt:
            log("Vigilancia detenida por el usuario")
            sys.exit(0)
        # Rojo en GitHub solo si ninguna revision logro leer la pagina.
        malas = resumen["revisiones"] and resumen["error"] == resumen["revisiones"]
        sys.exit(2 if malas else 0)

    log(f"Revision v{VERSION}, modalidades: {', '.join(modalidades_configuradas())}")
    estado, _ = una_revision(debug=args.debug, asistido=args.abrir_navegador)
    sys.exit({"hay_cupos": 0, "sin_cupos": 1, "otras_ciudades": 1}.get(estado, 2))


if __name__ == "__main__":
    main()
