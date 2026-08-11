# Mudar el monitor fuera de Oracle

La cuenta de Oracle Cloud fue eliminada por el sistema y con ella la VM. El
monitor no depende de Oracle en nada: es un script de Python que abre Chromium,
mira una pagina y manda un correo. Corre en cualquier parte.

Hay dos rutas listas para usar. La primera no cuesta nada; la segunda es
puntual al segundo pero necesita una maquina.

---

## Antes de nada: la clave de aplicacion

La contrasena de aplicacion de Google que estaba escrita dentro de
`check_dian_v5.py` hay que darla por comprometida: vivio en texto plano en un
disco que ahora esta en manos de Oracle.

1. Entra a <https://myaccount.google.com/apppasswords>.
2. Borra la clave vieja.
3. Crea una nueva y guardala donde toque segun la ruta que elijas (secreto de
   GitHub, o `dian.env` con permisos 600). **Nunca dentro de un archivo `.py`.**

El codigo ya no la lleva escrita: ahora solo se lee de la variable de entorno
`DIAN_CLAVE_APP`. Si falta, el monitor avisa en el registro y no manda correo.

---

## Ruta A — GitHub Actions (gratis, sin maquina)

Corre en los servidores de GitHub. En un repositorio publico los minutos son
ilimitados, asi que el costo es cero y no hay nada que mantener ni ninguna
cuenta que un sistema antifraude pueda cerrar.

El workflow ya esta escrito en `.github/workflows/dian.yml`.

### Montarlo

```bash
cd "C:\Users\Odob\Documents\Automatizacion dIAN"
git init
git add .
git commit -m "Monitor de citas DIAN"

# Crea el repositorio PUBLICO y sube. Publico = minutos ilimitados.
gh repo create monitor-dian --public --source=. --push
```

Luego los tres secretos (`Settings > Secrets and variables > Actions`, o por
consola):

```bash
gh secret set DIAN_CORREO_DESTINO --body "tu_correo@gmail.com"
gh secret set DIAN_CORREO_ORIGEN  --body "tu_correo@gmail.com"
gh secret set DIAN_CLAVE_APP      --body "la clave nueva de 16 letras"
```

Los secretos van cifrados y no se ven en los registros aunque el repositorio sea
publico. El codigo si queda a la vista: revisalo antes de subir para que no
quede ningun dato personal dentro.

### Comprobar

```bash
gh workflow run "Monitor de citas DIAN"   # fuerza una revision ahora
gh run watch                              # verla en vivo
gh run list --workflow=dian.yml           # historial
```

En la pestana **Actions** del repositorio se ve lo mismo con botones.

### Ver las capturas

Tres formas, de la mas comoda a la mas completa:

| Donde | Que muestra | Cuando se actualiza |
|---|---|---|
| Rama `resultados` del repositorio | La ultima captura, se abre en el navegador sin descargar nada | En hora cerrada, y siempre que haya cupos |
| Resumen de la corrida (pestana Actions) | Estado, hora de Bogota, modalidad y codigo de salida | Cada corrida |
| **Artifacts**, al pie de la pagina de la corrida | La captura de esa corrida concreta, en un zip | Cada corrida, se borra a los 7 dias |

La rama `resultados` queda en:

```
https://github.com/TU_USUARIO/monitor-dian/tree/resultados
```

Es una rama huerfana que se reescribe entera cada vez, asi que no acumula
historial ni engorda el repositorio. Los artefactos si guardan una captura por
corrida, pero se borran solos a los 7 dias y en un repositorio publico no se
cobra el almacenamiento.

### Lo que hay que aceptar

| Cosa | Detalle |
|---|---|
| **Puntualidad** | El cron de GitHub no es exacto. Con la cola cargada una corrida arranca 5-20 minutos tarde, y en picos muy altos se salta. Para cupos que duran minutos, es un costo real |
| **Repositorio publico** | Necesario para minutos ilimitados. Los secretos siguen cifrados; el codigo queda visible |
| **60 dias sin actividad** | GitHub apaga los workflows programados. El workflow hace un commit de latido el dia 1 de cada mes para evitarlo |
| **Gmail desde IP de GitHub** | Las claves de aplicacion funcionan desde cualquier IP. Si algun dia Google lo bloquea, se cambia el aviso a un bot de Telegram |

---

## Ruta B — una VM propia (puntual al segundo)

`vm/install.sh` ya no depende de Oracle: sirve en cualquier Ubuntu 24.04 con
systemd, x86-64 o ARM. Detecta si la maquina es chica y le crea 2 GB de
intercambio, porque Chromium pide unos 600 MB en el pico y en 1 GB de RAM sin
intercambio el kernel empieza a matar procesos.

El temporizador dispara en hora cerrada, sin desfase y con precision de un
segundo. Eso es lo que GitHub Actions no puede dar.

### Donde

| Proveedor | Precio real | Nota |
|---|---|---|
| **Hetzner CAX11** (ARM, 2 vCPU, 4 GB) | ~EUR 3,29/mes | Lo mas maquina por el dinero. Facturacion normal, sin sorpresas |
| **Google e2-micro** (1 vCPU, 1 GB) | ~USD 3,65/mes | **La VM es gratis, la IP publica no.** El free tier de Google excluye las direcciones IPv4 externas, efimeras y estaticas por igual. Sin IP externa no hay salida a internet salvo con Cloud NAT, que cuesta bastante mas |
| **Tu propio PC** | 0 | Funciona, pero solo vigila mientras el PC este encendido |

Si vas a pagar de todas formas, Hetzner da mucha mas maquina por lo mismo que
Google cobra solo por la IP.

### Instalar

Edita `vm-config.cmd` con la IP, el usuario y la llave de la maquina nueva, y:

```powershell
powershell -ExecutionPolicy Bypass -File .\subir-a-vm.ps1 -VmIp 1.2.3.4 -VmUser ubuntu -VmKey "$env:USERPROFILE\.ssh\tu_llave"
```

Copia el codigo, corre `vm/install.sh` y deja el temporizador andando. Todo lo
demas (`ver-corridas.cmd`, `revisar-ahora.cmd`, `conectar-vm.cmd`) sigue igual.

Antes de la primera corrida, la clave nueva va en la VM:

```bash
sudo nano /opt/dian/dian.env     # DIAN_CLAVE_APP=...
sudo chmod 600 /opt/dian/dian.env
sudo systemctl start dian-monitor.service
sudo tail -f /opt/dian/monitor.log
```

---

## Ruta C — este mismo PC, mientras tanto

Sin montar nada, para no quedarte sin vigilancia durante la mudanza:

```powershell
$env:DIAN_CLAVE_APP = "la clave nueva"
python check_dian_v5.py --vigilar
```

Deja la ventana abierta. Revisa cada 30 minutos dentro del horario y avisa por
correo. Se corta si apagas el PC.

---

## Recomendacion

Empieza por la **Ruta A**: cero costo, cero mantenimiento, y ninguna cuenta que
se pueda cerrar sola. Si notas que las corridas llegan tarde y por eso se te
escapan los cupos, pasate a la **Ruta B** con Hetzner — la instalacion son dos
comandos y el codigo es exactamente el mismo.

---

## Cloudflare — por que no

Browser Rendering en plan gratuito da **10 minutos de navegador al dia**; pasado
ese tope, error 429 hasta el siguiente dia UTC. Una revision tarda unos 40
segundos, y 32 revisiones diarias son ~21 minutos: el doble del tope. Solo
cabria revisando cada hora, y ahi se pierde la mitad de la vigilancia.

Ademas obligaria a reescribirlo entero: Python a JavaScript, Playwright a
Puppeteer, y el envio por SMTP a una API HTTP de terceros, porque los Workers no
hablan SMTP y la via de MailChannels se cerro en 2024. Mas trabajo para menos
capacidad.
