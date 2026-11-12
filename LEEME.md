# Monitor de citas DIAN (v6)

Vigila la página de agendamiento de la DIAN (Agendar cita > Persona Natural >
Presencial o Videoatención > Devoluciones) y te avisa al celular en cuanto deja
de aparecer el mensaje "No se encontraron especialidades".

## Qué cambió en la v6

| Antes (v5) | Ahora (v6) |
|---|---|
| Revisaba cada 30 min, y GitHub arrancaba 5 a 20 min tarde | Revisa cada ~3 min, en bucle continuo |
| Solo videoatención | Presencial y videoatención en la misma pasada |
| Solo correo | Push al celular con alarma (ntfy), Telegram, llamada telefónica, WhatsApp y correo, todos a la vez |
| El aviso decía "entra a la página" | El aviso trae un botón que abre la página directamente |
| No sabías si seguía vivo | Un aviso suave cada día a las 8 a.m. confirma que sigue vigilando |
| Falsa alarma si la página fallaba | Distingue "cita disponible" (con llamada) de "posible cita" (sin llamada) |
| | Modo PC asistido: la página aparece en tu pantalla ya parada en la lista de citas |
| | Guarda el HTML de la pantalla de cupos para automatizar la reserva después |

## 1. Configura al menos un aviso al celular

Cada canal es opcional y se activa solo con poner sus datos. Recomendado:
**ntfy** (gratis, 2 minutos) y, si quieres que te despierte, **la llamada**.

### ntfy (recomendado, gratis)

1. Instala la app **ntfy** en el celular (Play Store o App Store).
2. Inventa un nombre de tema largo y difícil de adivinar, por ejemplo
   `dian-oscar-8f3k29xq`. Quien sepa el nombre puede leer los avisos, así que
   no uses algo obvio.
3. En la app: botón **+**, escribe ese nombre y suscríbete.
4. Ponlo en `DIAN_NTFY_TOPIC`.
5. Para que suene aunque el celular esté en silencio (Android): en la app,
   abre el tema > Configuración de notificaciones > prioridad máxima y permite
   que ignore el modo No molestar.

Los avisos de cita llegan con prioridad máxima y un botón **Abrir agendamiento
DIAN**: tocas y quedas en la página.

### Telegram (gratis)

1. En Telegram habla con **@BotFather**, escribe `/newbot` y sigue los pasos.
   Te da un token: va en `DIAN_TELEGRAM_TOKEN`.
2. Escríbele cualquier cosa a tu bot nuevo.
3. Abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` y
   busca `"chat":{"id":...}`. Ese número va en `DIAN_TELEGRAM_CHAT_ID`.

El aviso trae botón para abrir la página y la captura de lo que vio el monitor.

### Llamada telefónica (Twilio, de pago)

La única opción que te despierta seguro. Cuesta centavos de dólar por llamada.

1. Crea cuenta en twilio.com y compra un número (o usa el de prueba).
2. `DIAN_TWILIO_SID` y `DIAN_TWILIO_TOKEN`: los ves en la consola de Twilio.
3. `DIAN_TWILIO_FROM`: tu número de Twilio. `DIAN_TWILIO_TO`: tu celular, con
   `+57` adelante.

En cuenta de prueba solo puede llamar a números verificados y antes del mensaje
suena un aviso de Twilio. Solo llama cuando la cita está confirmada, y como
mucho una vez cada 30 minutos.

### WhatsApp (CallMeBot, gratis, no oficial)

Manda un mensaje de WhatsApp, no una llamada: WhatsApp no deja que un programa
te llame. Sigue las instrucciones de callmebot.com para obtener tu apikey y
ponla en `DIAN_WHATSAPP_APIKEY`, con tu número en `DIAN_WHATSAPP_PHONE`.

### Correo (Gmail)

Igual que antes: `DIAN_CORREO_DESTINO`, `DIAN_CORREO_ORIGEN` y `DIAN_CLAVE_APP`
(clave de aplicación de Google, myaccount.google.com/apppasswords).

### Probar los avisos

```bash
python check_dian_v5.py --test-avisos                # todos menos la llamada
python check_dian_v5.py --test-avisos --con-llamada  # incluida la llamada
```

## 2. Dónde correrlo

### En GitHub Actions (gratis, sin máquina)

El workflow `.github/workflows/dian.yml` vigila en bucle, una revisión cada ~3
minutos, de 5:00 a 21:00 hora de Colombia. Cada corrida dura casi 6 horas y la
siguiente queda en cola, así que no hay huecos.

Los datos de los avisos van como **secretos** del repositorio: Settings >
Secrets and variables > Actions > New repository secret. Usa exactamente los
mismos nombres (`DIAN_NTFY_TOPIC`, `DIAN_TELEGRAM_TOKEN`, etc.).

Para arrancarlo: pestaña **Actions** > Monitor de citas DIAN > **Run workflow**.
Si marcas la casilla "Solo mandar un aviso de prueba", no vigila: solo te manda
un aviso por cada canal configurado, para comprobar los secretos.

Ojo: los términos de GitHub piden que Actions se use para trabajo del propio
software del repositorio. Un monitor que corre todo el día es zona gris y
GitHub podría deshabilitarlo. Si eso pasa, usa el PC o una VM.

### En tu PC con Windows (modo asistido, el más rápido para agendar)

1. Instala Python y luego:

   ```powershell
   pip install playwright
   ```

2. Copia `vm/dian.env.example` como `dian.env` en esta carpeta y llena tus
   datos. Pon `DIAN_NAVEGADOR_CANAL=msedge` para usar el Edge que ya tienes
   (así no hace falta `playwright install`).
3. Doble clic en **`vigilar-pc.bat`**.

El navegador trabaja fuera de la pantalla. Cuando hay cupos:

- te llegan los avisos al celular,
- suena una alarma en el PC,
- la ventana aparece al frente **ya parada en la lista de citas de
  Devoluciones**. Solo escoges la cita, llenas tus datos y confirmas.

Cuando cierras esa ventana, sigue vigilando. Si no estabas y nadie la cierra, a
los 20 minutos la cierra sola y sigue.

### En el celular (script "Cita rápida", `dian-rapido.user.js`)

Un script que corre dentro del navegador del celular. Al tocar la alerta de
ntfy de Bogotá (el enlace termina en `#auto`), o el botón ⚡ del panel, hace
solo: Agendar cita > Persona Natural > Videoatención > Devoluciones > servicio
de Bogotá (prefiere el de vehículos eléctricos) > Siguiente > ciudad > sede >
primera fecha > primera hora > Siguiente. Si no hay cupo, reintenta 3 minutos.
En el paso 3 espera a que marques "No soy un robot" y escribe la cédula.

No toca el captcha, no adjunta archivos (el navegador no lo permite), no acepta
la política de datos ni confirma: eso lo hace la persona.

Instalación:

- **Android**: Firefox (como navegador predeterminado, para que la alerta abra
  ahí) + complemento Tampermonkey. Abrir
  `https://raw.githubusercontent.com/OdobGames/monitor-dian/main/dian-rapido.user.js`
  e instalar.
- **iPhone**: app Userscripts (gratis) activada en Safari; guardar el mismo
  archivo en la carpeta de la app.

La primera vez, en la página de la DIAN, botón ⚙ para guardar cédula, nombre,
celular y correo. Quedan solo en ese navegador.

### En una VM propia

Igual que antes (`subir-a-vm.ps1` y `vm/install.sh`). Ahora el temporizador
dispara cada 3 minutos. Los datos de avisos van en `/opt/dian/dian.env`.

## Por qué no reserva la cita solo

El paso 3 tiene un reCAPTCHA ("No soy un robot") que verifica que quien agenda
es una persona; ninguna parte de este repositorio lo toca. El script del
celular llega hasta ahí y la persona termina.

## Configuración

| Variable | Por defecto | Qué hace |
|---|---|---|
| `DIAN_MODALIDAD` | `ambas` | `ambas`, `presencial` o `videoatencion` |
| `DIAN_INTERVALO_SEG` | `180` | segundos entre el inicio de una revisión y la siguiente |
| `DIAN_HORA_INICIO` / `DIAN_HORA_FIN` | `5` / `21` | franja de vigilancia, hora de Colombia |
| `DIAN_COOLDOWN_AVISO_MIN` | `10` | no repite avisos de la misma racha de cupos antes de esto |
| `DIAN_COOLDOWN_LLAMADA_MIN` | `30` | mínimo entre llamadas |
| `DIAN_LATIDO` / `DIAN_HORA_LATIDO` | `1` / `8` | aviso diario de que sigue vivo (`0` lo apaga) |
| `DIAN_ESPERA_ASISTIDO_MIN` | `20` | modo PC: cuánto deja la página en pantalla esperándote |
| `DIAN_NAVEGADOR_CANAL` | vacío | `msedge` o `chrome` para usar el navegador instalado |

## Códigos de salida

`--una-vez`: `0` hay cupos, `1` sin cupos, `2` error de la página, `3` fuera de
horario. `--vigilar`: `0` terminó bien, `2` ninguna revisión pudo leer la página.

## Comandos

```bash
python check_dian_v5.py --test-avisos                 # probar avisos
python check_dian_v5.py --una-vez --debug             # una revisión con ventana visible
python check_dian_v5.py --vigilar                     # bucle continuo
python check_dian_v5.py --vigilar --abrir-navegador   # bucle en el PC, modo asistido
python check_dian_v5.py --una-vez --modalidad presencial
python check_dian_v5.py --limpiar                     # solo limpiar y salir
```

La guía de VM y de mudanza sigue en [MIGRAR.md](MIGRAR.md) y la chuleta de
GitHub en [COMANDOS.md](COMANDOS.md).
