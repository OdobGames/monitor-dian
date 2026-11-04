#!/usr/bin/env python3
"""
Explorador de la pagina de agendamiento de la DIAN
==================================================

Recorre Agendar cita > Persona Natural > Videoatencion > Devoluciones y guarda
todo lo necesario para entender la pagina por dentro:

  * HTML, texto visible y captura de cada paso
  * Elementos clicables y listas desplegables (con sus opciones)
  * Todas las peticiones de red que hace la pagina, con sus respuestas
    (asi se ve que le pregunta la pagina a la DIAN y que le contesta)
  * El codigo JavaScript propio de la pagina

Si encuentra cupos, entra un paso mas (la especialidad de vehiculos
electricos) y guarda la lista de seccionales. NO agenda nada: no llena datos ni
confirma; solo mira.

Uso:
    python explorar.py                       # una pasada
    python explorar.py --buscar-min 50       # repite hasta ver cupos (max 50 min)
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import check_dian_v5 as mon
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

RE_VEHICULO = re.compile(r"veh[ií]cul|el[eé]ctric|h[ií]brid", re.I)

JS_ELEMENTOS = """() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length);
  const out = [];
  for (const e of document.querySelectorAll('body *')) {
    if (!vis(e)) continue;
    const propio = Array.from(e.childNodes).filter(n => n.nodeType === 3)
                        .map(n => n.textContent.trim()).join(' ').trim();
    const clic = e.onclick || e.getAttribute('onclick') || e.tagName === 'A' ||
                 e.tagName === 'BUTTON' || e.tagName === 'SELECT' || e.tagName === 'INPUT' ||
                 /btn|boton|opcion|item|card|seccional/i.test(e.className || '');
    if (!propio && !clic) continue;
    const attrs = {};
    for (const a of e.attributes) if (a.value.length < 200) attrs[a.name] = a.value;
    out.push({tag: e.tagName.toLowerCase(), texto: (e.innerText || '').trim().slice(0, 160),
              propio: propio.slice(0, 160), attrs, clicable: !!clic});
  }
  return out.slice(0, 1500);
}"""

JS_SELECTS = """() => Array.from(document.querySelectorAll('select')).map(s => ({
  id: s.id, name: s.name, cls: s.className, visible: !!(s.offsetParent),
  opciones: Array.from(s.options).map(o => ({valor: o.value, texto: o.text.trim()}))
}))"""

JS_SCRIPTS_INLINE = """() => Array.from(document.scripts).filter(s => !s.src)
  .map(s => s.textContent).filter(t => t && t.trim().length > 40)"""


class Grabador:
    """Guarda peticiones y respuestas de red de una pagina."""

    def __init__(self, page, host: str):
        self.host = host
        self.respuestas = []
        self.peticiones = []
        page.on("request", lambda r: self._req(r))
        page.on("response", lambda r: self.respuestas.append(r))

    def _req(self, r):
        try:
            datos = r.post_data
        except Exception:
            datos = None
        self.peticiones.append({"t": time.time(), "metodo": r.method, "url": r.url,
                                "tipo": r.resource_type,
                                "post": (datos or "")[:4000] or None})

    def volcar(self, carpeta: Path, js_dir: Path | None) -> list[dict]:
        """Lee los cuerpos de las respuestas pendientes y las escribe."""
        salida = []
        pendientes, self.respuestas = self.respuestas, []
        for r in pendientes:
            req = r.request
            tipo = req.resource_type
            entrada = {"metodo": req.method, "url": r.url, "tipo": tipo,
                       "estado": r.status, "content_type": r.headers.get("content-type", "")}
            try:
                entrada["post"] = (req.post_data or "")[:4000] or None
            except Exception:
                entrada["post"] = None
            propio = self.host in urlparse(r.url).netloc or "dian" in urlparse(r.url).netloc
            if propio and tipo in ("xhr", "fetch", "document", "script", "other"):
                try:
                    cuerpo = r.body()
                    texto = cuerpo.decode("utf-8", "replace")
                    if tipo == "script" and js_dir is not None:
                        nombre = re.sub(r"[^A-Za-z0-9._-]+", "_", urlparse(r.url).path)[-120:] or "script.js"
                        (js_dir / nombre).write_text(texto, encoding="utf-8")
                        entrada["guardado_en"] = f"js/{nombre}"
                    else:
                        entrada["cuerpo"] = texto[:200000]
                except Exception as e:
                    entrada["cuerpo_error"] = f"{type(e).__name__}: {e}"[:200]
            salida.append(entrada)
        (carpeta / "red.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        return salida


def foto(page, carpeta: Path, nombre: str) -> dict:
    """Captura, HTML, texto, elementos y selects del estado actual."""
    carpeta.mkdir(parents=True, exist_ok=True)
    info = {"paso": nombre, "url": page.url}
    try:
        page.screenshot(path=str(carpeta / f"{nombre}.png"), full_page=True)
    except Exception as e:
        info["captura_error"] = str(e)[:200]
    for archivo, funcion in ((f"{nombre}.html", lambda: page.content()),
                             (f"{nombre}.txt", lambda: page.inner_text("body"))):
        try:
            (carpeta / archivo).write_text(funcion(), encoding="utf-8")
        except Exception as e:
            info[f"{archivo}_error"] = str(e)[:200]
    try:
        elementos = page.evaluate(JS_ELEMENTOS)
        (carpeta / f"{nombre}_elementos.json").write_text(
            json.dumps(elementos, ensure_ascii=False, indent=1), encoding="utf-8")
        info["n_elementos"] = len(elementos)
    except Exception as e:
        info["elementos_error"] = str(e)[:200]
    try:
        selects = page.evaluate(JS_SELECTS)
        info["selects"] = selects
    except Exception:
        info["selects"] = []
    return info


def recorrer(navegador, carpeta: Path, guardar_js: bool) -> dict:
    """Una pasada completa. Devuelve el resumen de la pasada."""
    ctx = navegador.new_context(viewport={"width": 1400, "height": 1000},
                                locale="es-CO", user_agent=mon.USER_AGENT)
    page = ctx.new_page()
    host = urlparse(mon.URL).netloc
    rec = Grabador(page, host)
    js_dir = None
    if guardar_js:
        js_dir = carpeta / "js"
        js_dir.mkdir(parents=True, exist_ok=True)
    pasos = []
    res = {"inicio": f"{datetime.now():%Y-%m-%d %H:%M:%S}", "pasos": pasos}
    try:
        mon._abrir_pagina(page)
        pasos.append(foto(page, carpeta, "00_inicio"))
        if guardar_js:
            try:
                inline = page.evaluate(JS_SCRIPTS_INLINE)
                for i, t in enumerate(inline):
                    (js_dir / f"inline_{i:02d}.js").write_text(t, encoding="utf-8")
            except Exception:
                pass
        mon.clic(page, mon.SEL_AGENDAR, "Agendar cita", espera=3000)
        pasos.append(foto(page, carpeta, "01_agendar"))
        mon.clic(page, 'div.btnTipoPersona[llave="1"]', "Persona Natural")
        mon.clic(page, 'div.btnTipoAtencion[llave="2"]', "Videoatencion", espera=3000)
        pasos.append(foto(page, carpeta, "02_categorias"))
        sel_dev = 'div.btnCategoria:has(span.btnCatSpan:text-is("Devoluciones."))'
        if page.locator(sel_dev).count() == 0:
            sel_dev = 'div.btnCategoria:has-text("Devoluci")'
        mon.clic(page, sel_dev, "Devoluciones", espera=2500)
        try:
            page.wait_for_selector(f"text={mon.TEXTO_SIN_CUPOS}", state="visible",
                                   timeout=mon.ESPERA_MODAL_MS)
        except PWTimeout:
            pass
        pasos.append(foto(page, carpeta, "03_devoluciones"))
        cuerpo = page.inner_text("body")
        res["sin_cupos"] = mon.TEXTO_SIN_CUPOS.lower() in cuerpo.lower()

        if not res["sin_cupos"]:
            # Buscar la especialidad de vehiculos electricos y entrar un paso.
            candidatos = page.locator("body *").filter(has_text=RE_VEHICULO)
            objetivo = None
            for i in range(min(candidatos.count(), 80)):
                el = candidatos.nth(i)
                try:
                    if el.is_visible() and len(el.inner_text()) < 200:
                        objetivo = el  # el mas interno que contiene el texto
                except Exception:
                    continue
            res["especialidad_encontrada"] = objetivo is not None
            if objetivo is not None:
                res["especialidad_texto"] = objetivo.inner_text()[:200]
                objetivo.click(force=True)
                page.wait_for_timeout(1500)
                try:
                    mon.esperar_sin_cortina(page)
                except Exception:
                    pass
                page.wait_for_timeout(2500)
                pasos.append(foto(page, carpeta, "04_especialidad"))
            texto = page.inner_text("body")
            res["menciona_bogota"] = bool(re.search(r"bogot", texto, re.I))
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        try:
            pasos.append(foto(page, carpeta, "99_error"))
        except Exception:
            pass
    finally:
        try:
            red = rec.volcar(carpeta, js_dir)
            res["peticiones_api"] = [f"{r['metodo']} {r['url']}" for r in red
                                     if r["tipo"] in ("xhr", "fetch")]
        except Exception as e:
            res["red_error"] = str(e)[:200]
        (carpeta / "peticiones_todas.json").write_text(
            json.dumps(rec.peticiones, ensure_ascii=False, indent=1), encoding="utf-8")
        ctx.close()
    res["fin"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    (carpeta / "pasada.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", default="explorar")
    ap.add_argument("--buscar-min", type=float, default=0,
                    help="repetir hasta ver cupos durante N minutos (0 = una pasada)")
    ap.add_argument("--intervalo-seg", type=int, default=90)
    args = ap.parse_args()

    raiz = Path(args.salida)
    raiz.mkdir(parents=True, exist_ok=True)
    fin = time.monotonic() + args.buscar_min * 60
    indice_file = raiz / "indice.json"
    try:
        indice = json.loads(indice_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        indice = []

    with sync_playwright() as p:
        opciones = {"headless": True,
                    "args": mon.ARGS_LINUX if sys.platform.startswith("linux") else []}
        if mon.NAVEGADOR_RUTA:
            opciones["executable_path"] = mon.NAVEGADOR_RUTA
        navegador = p.chromium.launch(**opciones)
        n = 0
        while True:
            n += 1
            nombre = f"{datetime.now():%Y%m%d_%H%M%S}"
            primera = not (raiz / "js").exists()
            carpeta = raiz / "pasadas" / nombre
            if primera:
                carpeta = raiz / "primera"
            res = recorrer(navegador, carpeta, guardar_js=primera)
            if primera:
                # mover los js a la raiz para que esten en un solo lugar
                js = carpeta / "js"
                if js.exists():
                    js.rename(raiz / "js")
            indice.append({"carpeta": str(carpeta.relative_to(raiz)), **{
                k: res.get(k) for k in ("inicio", "sin_cupos", "especialidad_encontrada",
                                        "especialidad_texto", "menciona_bogota", "error")}})
            indice_file.write_text(json.dumps(indice, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
            print(f"pasada {n}: sin_cupos={res.get('sin_cupos')} "
                  f"error={res.get('error')} bogota={res.get('menciona_bogota')}", flush=True)
            # Las pasadas sin cupos repetidas no aportan: se borran salvo la primera.
            if res.get("sin_cupos") and not res.get("error") and carpeta.name != "primera":
                import shutil
                shutil.rmtree(carpeta, ignore_errors=True)
                indice[-1]["carpeta"] = "(borrada: sin cupos)"
                indice_file.write_text(json.dumps(indice, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
            if res.get("sin_cupos") is False or time.monotonic() + args.intervalo_seg > fin:
                break
            time.sleep(args.intervalo_seg)
        navegador.close()


if __name__ == "__main__":
    main()
