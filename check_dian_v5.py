#!/usr/bin/env python3
"""
Monitor de citas DIAN — Devolucion IVA (certificado UPME)
=========================================================

v5.1 — completo: navega, detecta, avisa por correo, limpia lo que ensucia
       y se puede repetir solo (bucle propio o temporizador del sistema).

CONFIGURACION
    Se puede dejar escrita abajo, o (mejor en un servidor) pasarla por
    variables de entorno, que tienen prioridad:

        DIAN_CORREO_DESTINO   tu correo
        DIAN_CORREO_ORIGEN    el gmail desde el que sale el aviso
        DIAN_CLAVE_APP        contrasena de aplicacion de Google
                              (myaccount.google.com/apppasswords — NO la normal)
        DIAN_MODALIDAD        "videoatencion" o "presencial"
        DIAN_INTERVALO_MIN, DIAN_HORA_INICIO, DIAN_HORA_FIN

Uso:
    python check_dian_v5.py --test-correo          # verifica que el correo sale
    python check_dian_v5.py --una-vez --debug      # una revision, ventana visible
    python check_dian_v5.py --vigilar              # bucle continuo (dejar abierto)
    python check_dian_v5.py --una-vez --respetar-horario
                                                   # para temporizadores (systemd):
                                                   # fuera de horario sale sin hacer nada
    python check_dian_v5.py --limpiar              # solo limpia y termina

Cada ejecucion empieza limpiando: recorta el log, borra capturas viejas y
elimina los restos que Chromium deja en la carpeta temporal. Asi la maquina
no se llena aunque el monitor lleve meses corriendo.
"""

import argparse
import os
import shutil
import smtplib
import sys
import tempfile
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ══════════════════════════════════════════════════════════════════
# CONFIGURACION — EDITAR ESTO (o usar las variables de entorno)
# ══════════════════════════════════════════════════════════════════
# Los correos tampoco van escritos aqui: si el repositorio es publico, un dato
# personal dentro del codigo lo indexa cualquiera.
CORREO_DESTINO = os.getenv("DIAN_CORREO_DESTINO", "")
CORREO_ORIGEN  = os.getenv("DIAN_CORREO_ORIGEN",  "")
# La clave NO se escribe aqui: se pasa por entorno (dian.env en la VM, o
# `set DIAN_CLAVE_APP=...` en Windows). Un archivo de codigo se copia, se sube a
# un repositorio y se comparte sin pensarlo; un archivo de entorno con permisos
# 600, no.
CLAVE_APP      = os.getenv("DIAN_CLAVE_APP", "")

# Cada cuanto revisar, en minutos (solo lo usa --vigilar)
INTERVALO_MIN = int(os.getenv("DIAN_INTERVALO_MIN", "30"))

# Horario de vigilancia (hora local). Fuera de esto no revisa.
HORA_INICIO = int(os.getenv("DIAN_HORA_INICIO", "5"))
HORA_FIN    = int(os.getenv("DIAN_HORA_FIN", "21"))

# Modalidad a vigilar: "videoatencion" o "presencial"
MODALIDAD = os.getenv("DIAN_MODALIDAD", "videoatencion")

# ── Limpieza (evita que la maquina se llene) ──────────────────────
MAX_LOG_BYTES   = int(os.getenv("DIAN_MAX_LOG_BYTES", "1000000"))  # 1 MB
LINEAS_A_DEJAR  = int(os.getenv("DIAN_LINEAS_LOG", "3000"))
DIAS_CAPTURAS   = int(os.getenv("DIAN_DIAS_CAPTURAS", "3"))
MAX_SALIDA_MB   = int(os.getenv("DIAN_MAX_SALIDA_MB", "50"))
# ══════════════════════════════════════════════════════════════════

URL = "https://agendamiento.dian.gov.co/"
TEXTO_SIN_CUPOS = "No se encontraron especialidades"
LLAVE_MODALIDAD = {"presencial": "1", "videoatencion": "2"}
SEL_AGENDAR = 'div[nombre="btnSolicitarCita"]'
# Cortina negra con la palabra "Cargando" que la pagina pone encima mientras
# consulta. Mientras este visible, lo que se lea en pantalla no vale nada.
SEL_CARGANDO = "#mpcWPdivCargando"

# La pagina de la DIAN se pone lenta a ratos y el tiempo varia mucho de una
# corrida a otra: medido el 2026-08-12, el HTML inicial tardo entre 52 y 118
# segundos, y la interfaz aparecio unos 12 segundos despues. Los margenes de
# abajo dejan holgura sobre el peor caso visto; se pueden subir por entorno sin
# tocar el codigo.
TIMEOUT_CARGA_MS  = int(os.getenv("DIAN_TIMEOUT_CARGA_MS", "180000"))
TIMEOUT_BOTON_MS  = int(os.getenv("DIAN_TIMEOUT_BOTON_MS", "120000"))
TIMEOUT_CLIC_MS   = int(os.getenv("DIAN_TIMEOUT_CLIC_MS", "45000"))
TIMEOUT_CARGANDO_MS = int(os.getenv("DIAN_TIMEOUT_CARGANDO_MS", "150000"))
INTENTOS_CARGA    = int(os.getenv("DIAN_INTENTOS_CARGA", "2"))
# Margen de gracia tras irse la cortina: la respuesta ya llego, pero el modal
# tarda un instante en pintarse. Si en este plazo no aparece el aviso de "sin
# cupos", es que de verdad no lo hay.
ESPERA_MODAL_MS   = int(os.getenv("DIAN_ESPERA_MODAL_MS", "15000"))

OUT_DIR = Path(__file__).parent / "salida"
OUT_DIR.mkdir(exist_ok=True)
LOG_FILE = Path(__file__).parent / "monitor.log"

# Chromium dentro de una VM sin escritorio: sin estos argumentos revienta al
# arrancar (el sandbox de Ubuntu 24.04 bloquea los espacios de nombres de
# usuario sin privilegios, y /dev/shm suele ser demasiado pequeno).
ARGS_LINUX = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--no-first-run",
    "--mute-audio",
]


def log(msg: str) -> None:
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(linea, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


# ──────────────────────────────────────────────────────────────────
# LIMPIEZA — se ejecuta al principio de cada corrida
# ──────────────────────────────────────────────────────────────────
def _recortar_log() -> str | None:
    """Si el log paso de MAX_LOG_BYTES, deja solo las ultimas lineas."""
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size <= MAX_LOG_BYTES:
        return None
    antes = LOG_FILE.stat().st_size
    try:
        lineas = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    cola = lineas[-LINEAS_A_DEJAR:]
    tmp = LOG_FILE.with_suffix(".log.tmp")
    tmp.write_text("\n".join(cola) + "\n", encoding="utf-8")
    tmp.replace(LOG_FILE)
    return f"log recortado ({antes // 1024} KB -> {LOG_FILE.stat().st_size // 1024} KB)"


def _limpiar_capturas() -> str | None:
    """Borra capturas viejas y, si aun asi pesa demasiado, las mas antiguas."""
    if not OUT_DIR.is_dir():
        return None
    limite = time.time() - DIAS_CAPTURAS * 86400
    borradas = 0
    for png in OUT_DIR.glob("*.png"):
        try:
            if png.stat().st_mtime < limite:
                png.unlink()
                borradas += 1
        except OSError:
            pass

    # Tope duro por tamano: si sigue pasada, cae la mas antigua hasta bajar.
    def peso() -> int:
        return sum(p.stat().st_size for p in OUT_DIR.glob("*.png") if p.is_file())

    tope = MAX_SALIDA_MB * 1024 * 1024
    try:
        while peso() > tope:
            pngs = sorted(OUT_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
            if not pngs:
                break
            pngs[0].unlink()
            borradas += 1
    except OSError:
        pass

    return f"{borradas} captura(s) vieja(s) borrada(s)" if borradas else None


def _limpiar_temporales() -> str | None:
    """Perfiles y volcados que Chromium deja tirados cuando una corrida muere.

    Solo toca carpetas con nombre inequivoco de Playwright/Chromium y con mas
    de una hora de antiguedad, para no pisar una ejecucion en curso.
    """
    tmp = Path(tempfile.gettempdir())
    if not tmp.is_dir():
        return None
    patrones = (
        "playwright*",
        "playwright_chromiumdev_profile-*",
        ".org.chromium.Chromium.*",
        "Crashpad*",
        "chrome_*",
    )
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
    """Chromium de corridas anteriores que quedo colgado.

    Solo en Linux, solo procesos de este mismo usuario y solo si el ejecutable
    vive dentro de la cache de navegadores de Playwright: nunca toca un
    navegador del escritorio ni nada del servidor.
    """
    if not sys.platform.startswith("linux"):
        return None
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    marca = os.getenv("PLAYWRIGHT_BROWSERS_PATH") or "ms-playwright"
    mio = os.getuid()
    yo = os.getpid()
    matados = 0
    for entrada in proc.iterdir():
        if not entrada.name.isdigit():
            continue
        pid = int(entrada.name)
        if pid == yo:
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
            os.kill(pid, 9)
            matados += 1
        except OSError:
            pass
    return f"{matados} navegador(es) huerfano(s) cerrado(s)" if matados else None


def limpiar(silencioso: bool = False) -> None:
    for tarea in (_recortar_log, _limpiar_capturas,
                  _limpiar_temporales, _matar_navegadores_huerfanos):
        try:
            detalle = tarea()
        except Exception as e:            # la limpieza nunca debe tumbar el monitor
            detalle = f"fallo en {tarea.__name__}: {type(e).__name__}: {e}"
        if detalle and not silencioso:
            log(f"   limpieza: {detalle}")


# ──────────────────────────────────────────────────────────────────
# CORREO
# ──────────────────────────────────────────────────────────────────
def enviar_correo(asunto: str, cuerpo: str, adjunto: Path | None = None) -> bool:
    if not CLAVE_APP or "xxxx" in CLAVE_APP or not CORREO_DESTINO or not CORREO_ORIGEN:
        log("!! falta configuracion de correo (DIAN_CORREO_DESTINO, "
            "DIAN_CORREO_ORIGEN, DIAN_CLAVE_APP) — no se envia correo. "
            "En la VM: /opt/dian/dian.env; en Windows: variables de entorno; "
            "en GitHub: secretos del repositorio")
        return False

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = CORREO_ORIGEN
    msg["To"] = CORREO_DESTINO
    msg.set_content(cuerpo)

    if adjunto and adjunto.exists():
        msg.add_attachment(adjunto.read_bytes(), maintype="image",
                           subtype="png", filename=adjunto.name)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(CORREO_ORIGEN, CLAVE_APP.replace(" ", ""))
            s.send_message(msg)
        log(f"   correo enviado -> {CORREO_DESTINO}")
        return True
    except Exception as e:
        log(f"!! error enviando correo: {type(e).__name__}: {e}")
        return False


# ──────────────────────────────────────────────────────────────────
# NAVEGACION
# ──────────────────────────────────────────────────────────────────
def esperar_sin_cortina(page) -> None:
    """Espera a que se vaya la cortina de 'Cargando'.

    Es la diferencia entre leer la pantalla y leer una pantalla a medio pintar:
    con la cortina puesta no esta el aviso de "sin cupos" todavia, y el monitor
    lo tomaria por un cupo libre. Si la cortina no se va, revienta con
    PWTimeout, que arriba se traduce en error — nunca en falsa alarma.
    """
    page.wait_for_selector(SEL_CARGANDO, state="hidden",
                           timeout=TIMEOUT_CARGANDO_MS)


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


def revisar_citas(modalidad: str = MODALIDAD, debug: bool = False) -> dict:
    llave_mod = LLAVE_MODALIDAD[modalidad]
    nombre_mod = "Presencial" if modalidad == "presencial" else "Videoatención"
    captura_final = OUT_DIR / "resultado.png"

    with sync_playwright() as p:
        resultado = {"estado": "error", "detalle": "no se completo",
                     "captura": None}
        navegador = ctx = page = None

        try:
            # Ojo: el arranque del navegador va DENTRO del try. Si queda fuera y
            # falla (falta Chromium, se acabo la memoria...), Python revienta con
            # codigo 1 — el mismo que "sin cupos" — y el monitor parece sano
            # estando muerto.
            navegador = p.chromium.launch(
                headless=not debug,
                args=ARGS_LINUX if sys.platform.startswith("linux") else [],
            )
            ctx = navegador.new_context(
                viewport={"width": 1400, "height": 1000},
                locale="es-CO",
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
            )
            page = ctx.new_page()

            # No se espera a "networkidle": esa condicion pide 500 ms seguidos
            # sin trafico de red, y esta pagina no siempre llega a estar quieta.
            # Basta con el HTML y con que el boton de agendar sea visible; si la
            # pagina va lenta, se reintenta en vez de dar el dia por perdido.
            for intento in range(1, INTENTOS_CARGA + 1):
                try:
                    page.goto(URL, wait_until="domcontentloaded",
                              timeout=TIMEOUT_CARGA_MS)
                    page.wait_for_selector(SEL_AGENDAR, state="visible",
                                           timeout=TIMEOUT_BOTON_MS)
                    break
                except PWTimeout:
                    if intento == INTENTOS_CARGA:
                        raise
                    log(f"   pagina lenta, reintento {intento + 1}/{INTENTOS_CARGA}")
                    page.wait_for_timeout(5000)
            page.wait_for_timeout(3000)

            clic(page, SEL_AGENDAR, "Agendar cita",
                 paso="01_agendar", debug=debug, espera=3000)
            clic(page, 'div.btnTipoPersona[llave="1"]', "Persona Natural",
                 paso="02_persona", debug=debug)
            clic(page, f'div.btnTipoAtencion[llave="{llave_mod}"]', nombre_mod,
                 paso="03_modalidad", debug=debug, espera=3000)

            sel_dev = 'div.btnCategoria:has(span.btnCatSpan:text-is("Devoluciones."))'
            if page.locator(sel_dev).count() == 0:
                sel_dev = 'div.btnCategoria:has-text("Devoluciones")'
            clic(page, sel_dev, "Devoluciones",
                 paso="04_devoluciones", debug=debug, espera=2000)

            # Ultimo filtro contra la falsa alarma: la cortina ya se fue, pero
            # el modal se pinta un instante despues. Se le da un plazo a que
            # aparezca; solo si pasado ese plazo sigue sin estar cuenta como
            # cupo libre.
            try:
                page.wait_for_selector(f"text={TEXTO_SIN_CUPOS}",
                                       state="visible", timeout=ESPERA_MODAL_MS)
            except PWTimeout:
                pass

            page.screenshot(path=str(captura_final), full_page=True)
            cuerpo = page.inner_text("body")

            if TEXTO_SIN_CUPOS.lower() in cuerpo.lower():
                resultado = {"estado": "sin_cupos",
                             "detalle": f"[{nombre_mod}] sin cupos por ahora.",
                             "captura": captura_final}
            else:
                visible = cuerpo[-1500:].replace("\n", " | ")
                resultado = {"estado": "hay_cupos",
                             "detalle": (f"[{nombre_mod}] NO aparecio el modal. "
                                         f"Pantalla: {visible}"),
                             "captura": captura_final}

        except PWTimeout as e:
            resultado = {"estado": "error", "detalle": f"Timeout: {e}",
                         "captura": None}
        except Exception as e:
            resultado = {"estado": "error",
                         "detalle": f"{type(e).__name__}: {e}", "captura": None}
        finally:
            try:
                if debug and page:
                    page.wait_for_timeout(5000)
                if ctx:
                    ctx.close()
                if navegador:
                    navegador.close()
            except Exception:
                pass

    return resultado


# ──────────────────────────────────────────────────────────────────
# CICLO DE VIGILANCIA
# ──────────────────────────────────────────────────────────────────
def en_horario() -> bool:
    return HORA_INICIO <= datetime.now().hour < HORA_FIN


def una_revision(debug: bool = False, avisar_errores: bool = False) -> str:
    r = revisar_citas(debug=debug)
    log(f"   resultado: {r['estado']} — {r['detalle'][:120]}")

    if r["estado"] == "hay_cupos":
        enviar_correo(
            "🚨 CITA DIAN DISPONIBLE — devolucion IVA",
            "El sistema de agendamiento NO mostro el mensaje de sin cupos.\n"
            "Entra YA a agendar, los cupos se agotan en minutos:\n\n"
            f"{URL}\n\n"
            "Ruta: Agendar cita > Persona Natural > "
            f"{'Presencial' if MODALIDAD=='presencial' else 'Videoatencion'} "
            "> Devoluciones\n\n"
            f"Detalle tecnico:\n{r['detalle']}\n",
            adjunto=r["captura"],
        )
    elif r["estado"] == "error" and avisar_errores:
        enviar_correo(
            "⚠️ Monitor DIAN — error tecnico",
            f"El monitor no pudo completar la revision:\n\n{r['detalle']}\n\n"
            "Conviene revisar manualmente.",
        )

    return r["estado"]


def vigilar() -> None:
    log("=" * 62)
    log(f"VIGILANCIA ACTIVA — cada {INTERVALO_MIN} min, "
        f"entre las {HORA_INICIO}:00 y las {HORA_FIN}:00")
    log(f"Modalidad: {MODALIDAD} | Aviso a: {CORREO_DESTINO}")
    log("Ctrl+C para detener")
    log("=" * 62)

    fallos_seguidos = 0
    revisiones = 0

    while True:
        if en_horario():
            revisiones += 1
            log(f"Revision #{revisiones}")
            limpiar()
            # solo avisa de errores si se repiten, para no llenar el correo
            estado = una_revision(avisar_errores=(fallos_seguidos == 2))

            if estado == "error":
                fallos_seguidos += 1
            else:
                fallos_seguidos = 0

            if estado == "hay_cupos":
                log(">>> CUPOS DETECTADOS — revisa tu correo <<<")
        else:
            log(f"Fuera de horario ({datetime.now().hour}:00) — en espera")

        time.sleep(INTERVALO_MIN * 60)


# ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vigilar", action="store_true",
                    help="bucle continuo (dejar la ventana abierta)")
    ap.add_argument("--una-vez", action="store_true",
                    help="una sola revision y termina")
    ap.add_argument("--test-correo", action="store_true",
                    help="envia un correo de prueba y termina")
    ap.add_argument("--debug", action="store_true",
                    help="navegador visible + capturas de cada paso")
    ap.add_argument("--respetar-horario", action="store_true",
                    help="si esta fuera del horario, sale sin revisar (para systemd/cron)")
    ap.add_argument("--limpiar", action="store_true",
                    help="solo hace la limpieza de disco y termina")
    ap.add_argument("--sin-limpieza", action="store_true",
                    help="no limpiar antes de revisar")
    args = ap.parse_args()

    if args.limpiar:
        limpiar()
        log("limpieza terminada")
        sys.exit(0)

    if args.test_correo:
        ok = enviar_correo(
            "Prueba — monitor de citas DIAN",
            "Si estas leyendo esto, el envio de correo funciona.\n"
            "El monitor ya puede avisarte cuando haya cupos.",
        )
        sys.exit(0 if ok else 1)

    if args.respetar_horario and not en_horario():
        log(f"Fuera de horario ({datetime.now().hour}:00) — no se revisa")
        limpiar(silencioso=True)
        sys.exit(3)

    if not args.sin_limpieza:
        limpiar()

    if args.vigilar:
        try:
            vigilar()
        except KeyboardInterrupt:
            log("Vigilancia detenida por el usuario")
        sys.exit(0)

    # por defecto: una revision
    log(f"Revision — modalidad {MODALIDAD}")
    estado = una_revision(debug=args.debug)
    sys.exit({"hay_cupos": 0, "sin_cupos": 1}.get(estado, 2))
