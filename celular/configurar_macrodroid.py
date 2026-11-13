#!/usr/bin/env python3
"""Deja MacroDroid listo en el celular (pensado para Xiaomi, Redmi y POCO,
sirve en cualquier Android) para que el webhook del monitor DIAN pueda prender
la pantalla y abrir la pagina aunque el celular este bloqueado.

Que hace, por ADB:
  1. Si no tienes adb, lo descarga (herramientas oficiales de Google).
  2. Se conecta al celular por USB, o por WiFi con la depuracion inalambrica.
  3. Quita restricciones de bateria y de segundo plano a MacroDroid.
  4. En Xiaomi intenta dar "ventanas en segundo plano", "pantalla de bloqueo"
     e "inicio automatico". Si el sistema no lo deja, abre esas pantallas para
     que actives el interruptor a mano.
  5. Con --webhook: apaga la pantalla, llama el webhook y revisa si se prendio
     la pantalla y se abrio el navegador.

Antes, en el celular:
  Ajustes > Sobre el telefono > toca 7 veces "Version de HyperOS/MIUI".
  Ajustes adicionales > Opciones de desarrollador > activa:
    - Depuracion USB
    - Depuracion USB (configuracion de seguridad)   <- en Xiaomi, si aparece

Uso (en el PC, con el celular conectado por cable):
  python configurar_macrodroid.py --webhook https://trigger.macrodroid.com/XXXX/dian

Por WiFi (Opciones de desarrollador > Depuracion inalambrica):
  python configurar_macrodroid.py --emparejar IP:PUERTO_EMPAREJAR --codigo CODIGO \
      --conectar IP:PUERTO --webhook https://trigger.macrodroid.com/XXXX/dian
"""
from __future__ import annotations

import argparse
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

PAQUETE = "com.arlosoft.macrodroid"
CARPETA = Path(__file__).resolve().parent
EXE = "adb.exe" if os.name == "nt" else "adb"
ADB: list[str] = []

ERROR = re.compile(r"(?i)exception|unknown|not allowed|error|denied|failed|usage:")

PASOS = [
    ("Sin restriccion de bateria", f"dumpsys deviceidle whitelist +{PAQUETE}"),
    ("Puede correr en segundo plano", f"cmd appops set {PAQUETE} RUN_IN_BACKGROUND allow"),
    ("Puede correr en segundo plano (siempre)", f"cmd appops set {PAQUETE} RUN_ANY_IN_BACKGROUND allow"),
    ("Mostrar sobre otras apps", f"appops set {PAQUETE} SYSTEM_ALERT_WINDOW allow"),
    ("Alarmas exactas", f"appops set {PAQUETE} SCHEDULE_EXACT_ALARM allow"),
    ("Grupo de apps siempre activas", f"am set-standby-bucket {PAQUETE} active"),
]
# Operaciones propias de Xiaomi (MIUI / HyperOS)
PASOS_XIAOMI = [
    ("Xiaomi: mostrar ventanas en segundo plano", f"appops set {PAQUETE} 10021 allow"),
    ("Xiaomi: mostrar en pantalla de bloqueo", f"appops set {PAQUETE} 10020 allow"),
    ("Xiaomi: inicio automatico", f"appops set {PAQUETE} 10008 allow"),
]
PANTALLAS_XIAOMI = [
    ("Otros permisos de MacroDroid",
     "Pon en Permitir: 'Mostrar ventanas emergentes mientras se ejecuta en segundo "
     "plano' y 'Mostrar en pantalla de bloqueo'.",
     "am start -n com.miui.securitycenter/com.miui.permcenter.permissions.PermissionsEditorActivity "
     f"-a miui.intent.action.APP_PERM_EDITOR --es extra_pkgname {PAQUETE}"),
    ("Inicio automatico",
     "Activa el interruptor de MacroDroid.",
     "am start -n com.miui.securitycenter/com.miui.permcenter.autostart.AutoStartManagementActivity"),
]


def buscar_adb() -> str | None:
    local = CARPETA / "platform-tools" / EXE
    if local.exists():
        return str(local)
    return shutil.which("adb")


def bajar_adb() -> str:
    so = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}[platform.system()]
    url = f"https://dl.google.com/android/repository/platform-tools-latest-{so}.zip"
    print(f"No encontre adb. Lo descargo de Google ({url})...")
    datos = urllib.request.urlopen(url, timeout=180).read()
    zipfile.ZipFile(io.BytesIO(datos)).extractall(CARPETA)
    exe = CARPETA / "platform-tools" / EXE
    if os.name != "nt":
        exe.chmod(0o755)
    return str(exe)


def adb(*args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run([*ADB, *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 1, "tiempo agotado"
    return r.returncode, (r.stdout + r.stderr).strip()


def sh(cmd: str, timeout: int = 30) -> tuple[int, str]:
    return adb("shell", cmd, timeout=timeout)


def ok(codigo: int, salida: str) -> bool:
    return codigo == 0 and not ERROR.search(salida)


def conectar(a) -> str:
    if a.emparejar:
        print(adb("pair", a.emparejar, a.codigo or "", timeout=40)[1])
    if a.conectar:
        print(adb("connect", a.conectar, timeout=40)[1])
    avisado = ""
    for _ in range(90):
        _, out = adb("devices")
        filas = [f.split() for f in out.splitlines()[1:] if f.strip()]
        listos = [f[0] for f in filas if len(f) > 1 and f[1] == "device"]
        if a.serial and a.serial in listos:
            return a.serial
        if listos:
            if len(listos) > 1:
                print(f"Hay varios dispositivos ({', '.join(listos)}); uso {listos[0]}. "
                      "Para otro, usa --serial.")
            return listos[0]
        msg = ("En el celular acepta 'Permitir depuracion USB' (marca 'Permitir siempre')."
               if any(len(f) > 1 and f[1] == "unauthorized" for f in filas)
               else "Esperando el celular: conectalo por cable con la depuracion USB activada...")
        if msg != avisado:
            print(msg)
            avisado = msg
        time.sleep(2)
    sys.exit("No encontre el celular. Revisa el cable y la depuracion USB.")


def es_xiaomi() -> bool:
    _, marca = sh("getprop ro.product.manufacturer")
    _, miui = sh("getprop ro.miui.ui.version.name")
    _, hyper = sh("getprop ro.mi.os.version.name")
    return "xiaomi" in marca.lower() or bool(miui.strip()) or bool(hyper.strip())


def aplicar(pasos) -> list[str]:
    fallidos = []
    for nombre, cmd in pasos:
        codigo, out = sh(cmd)
        bien = ok(codigo, out)
        print(f"  [{'OK' if bien else 'NO'}] {nombre}" + ("" if bien else f"  ({out[:90]})"))
        if not bien:
            fallidos.append(nombre)
    return fallidos


def abrir_pantallas_xiaomi() -> None:
    print("\nEl sistema no dejo cambiar algunos permisos de Xiaomi por ADB. "
          "Te abro las pantallas en el celular:")
    for nombre, que_hacer, cmd in PANTALLAS_XIAOMI:
        codigo, out = sh(cmd)
        if not ok(codigo, out):
            print(f"  No pude abrir '{nombre}' ({out[:80]}). Buscala en Ajustes > Apps > "
                  "Administrar apps > MacroDroid.")
        print(f"\n  >> {nombre}: {que_hacer}")
        input("     Cuando lo actives en el celular, presiona Enter aqui...")


def probar(webhook: str) -> None:
    print("\nPrueba: apago la pantalla, llamo el webhook y reviso el celular.")
    codigo, out = sh("input keyevent 223")
    if not ok(codigo, out):
        input("  No pude apagar la pantalla por ADB. Apagala con el boton y presiona Enter...")
    time.sleep(4)
    try:
        r = urllib.request.urlopen(webhook, timeout=20)
        print(f"  Webhook: HTTP {r.status}")
    except Exception as e:
        print(f"  El webhook fallo: {e}")
        return
    time.sleep(10)
    _, energia = sh("dumpsys power")
    _, actividad = sh("dumpsys activity activities")
    despierta = bool(re.search(r"mWakefulness=Awake", energia))
    arriba = re.findall(r"(?:topResumedActivity|mResumedActivity|ResumedActivity)[^\n]*", actividad)
    navegador = any(re.search(r"firefox|mozilla|chrome|browser", l, re.I) for l in arriba)
    print(f"  Pantalla prendida: {'SI' if despierta else 'NO'}")
    print(f"  Navegador al frente: {'SI' if navegador else 'NO'}"
          + (f"  ({arriba[0].strip()[:100]})" if arriba else ""))
    if despierta and navegador:
        print("\nTodo funciona. Desbloquea y vas a ver la pagina de la DIAN.")
    elif navegador:
        print("\nLa pagina se abrio pero la pantalla no prendio. Revisa 'Mostrar en pantalla de "
              "bloqueo' y en Ajustes > Pantalla de bloqueo, 'Activar pantalla con notificaciones'.")
    else:
        print("\nNo se abrio la pagina. Revisa 'Mostrar ventanas emergentes mientras se ejecuta en "
              "segundo plano' en Otros permisos de MacroDroid, y que la macro este activa.")


def main() -> None:
    p = argparse.ArgumentParser(description="Configura MacroDroid por ADB para el monitor DIAN.")
    p.add_argument("--webhook", help="URL del webhook de MacroDroid, para hacer la prueba al final")
    p.add_argument("--serial", help="dispositivo a usar si hay varios (ver 'adb devices')")
    p.add_argument("--emparejar", metavar="IP:PUERTO", help="depuracion inalambrica: emparejar")
    p.add_argument("--codigo", help="codigo de emparejamiento de la depuracion inalambrica")
    p.add_argument("--conectar", metavar="IP:PUERTO", help="depuracion inalambrica: conectar")
    a = p.parse_args()

    global ADB
    ADB = [buscar_adb() or bajar_adb()]
    adb("start-server", timeout=60)
    serie = conectar(a)
    ADB = [ADB[0], "-s", serie]
    _, modelo = sh("getprop ro.product.model")
    _, android = sh("getprop ro.build.version.release")
    print(f"Conectado: {modelo.strip()} (Android {android.strip()})")

    _, pk = sh(f"pm list packages {PAQUETE}")
    if PAQUETE not in pk:
        sys.exit("MacroDroid no esta instalado en este celular.")

    print("\nPermisos de Android:")
    aplicar(PASOS)
    if es_xiaomi():
        print("\nPermisos de Xiaomi:")
        if aplicar(PASOS_XIAOMI):
            abrir_pantallas_xiaomi()
        print("\nUltimo paso manual: abre las apps recientes, manten presionada MacroDroid y toca "
              "el candado, para que 'limpiar todo' no la cierre.")
    if a.webhook:
        probar(a.webhook)
    else:
        print("\nListo. Para probar: repite con --webhook TU_URL")


if __name__ == "__main__":
    main()
