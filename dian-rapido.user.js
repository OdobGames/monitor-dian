// ==UserScript==
// @name         DIAN cita rapida
// @namespace    https://github.com/OdobGames/monitor-dian
// @version      1.0.0
// @description  Agendamiento DIAN: hace sola la ruta hasta la cita de Bogota y llena la cedula. El captcha, los archivos y la confirmacion los haces tu.
// @match        https://agendamiento.dian.gov.co/*
// @run-at       document-idle
// @grant        none
// @downloadURL  https://raw.githubusercontent.com/OdobGames/monitor-dian/main/dian-rapido.user.js
// @updateURL    https://raw.githubusercontent.com/OdobGames/monitor-dian/main/dian-rapido.user.js
// ==/UserScript==

/*
 * Que hace
 *   Al abrir la pagina desde la alerta de ntfy (la direccion termina en #auto)
 *   o al tocar el boton "Cita rapida":
 *     Agendar cita > Persona Natural > Videoatencion > Devoluciones
 *     > servicio de Bogota (prefiere el de vehiculos electricos o hibridos)
 *     > Siguiente > ciudad > sede > primera fecha > primera hora > Siguiente
 *   Si no hay cupo en Bogota, reintenta durante unos minutos.
 *   En el paso 3 espera a que marques "No soy un robot" y escribe tu cedula
 *   (y nombre, celular y correo si la pagina los pide vacios).
 *
 * Que NO hace (a proposito)
 *   No toca el captcha, no adjunta archivos (el navegador no lo permite), no
 *   acepta la politica de datos ni confirma la cita. Eso lo haces tu.
 *
 * Tus datos se guardan solo en este navegador (boton del engranaje).
 */
(function () {
  'use strict';
  if (window.top !== window.self) return; // no correr dentro del captcha

  const VERSION = '1.0.0';
  const CLAVE_CFG = 'dianRapidoCfg';
  const CLAVE_RONDA = 'dianRapidoRonda';
  const SIN_CUPOS = 'No se encontraron especialidades';

  const S = {
    cargando: '#mpcWPdivCargando',
    agendar: 'div[nombre="btnSolicitarCita"]',
    persona: 'div.btnTipoPersona[llave="1"]',
    video: 'div.btnTipoAtencion[llave="2"]',
    categoria: 'div.btnCategoria',
    modalCerrar: ['div[pantalla="ModalError"][nombre="btnAceptar"]',
                  'div[pantalla="ModalError"][nombre="btnCerrarModal"]'],
    servicio: 'div[contenido="servicio"] select',
    sig1: 'div[contenido="botonesPasoUno"] > div[nombrecontrol="btnSiguiente"]',
    ciudad: 'div[contenido="Ciudad"] select',
    sede: 'div[contenido="Sede"] select',
    btnFecha: 'div[pantalla="PasoDos"][nombre="btnFecha"]',
    calendario: 'div[pantalla="PasoDos"][nombre="Calendario"]',
    hora: 'div[contenido="Hora"] select',
    sig2: 'div[contenido="botonesPasoDos"] > div[nombrecontrol="btnSiguiente"]',
    numDoc: 'div[pantalla="PasoTres"][nombre="tbxNumeroDoc"] input',
    nombre: 'div[pantalla="PasoTres"][nombre="tbxNombreCompleto"] input',
    tel: 'div[pantalla="PasoTres"][nombre="tbxTelefono"] input',
    correo: 'div[pantalla="PasoTres"][nombre="tbxCorreo"] input',
  };

  let corriendo = false;
  let detenido = false;

  // ── utilidades ──────────────────────────────────────────────────────
  const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!el && el.getClientRects().length > 0 &&
    getComputedStyle(el).visibility !== 'hidden';
  const uno = (sel) => [...document.querySelectorAll(sel)].find(visible) || null;
  const cargando = () => visible(document.querySelector(S.cargando));
  const hhmm = (t) => new Date(t).toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' });

  function leer(clave, almacen) {
    try { return JSON.parse(almacen.getItem(clave) || 'null'); } catch (e) { return null; }
  }
  function guardar(clave, valor, almacen) {
    try {
      if (valor === null) almacen.removeItem(clave); else almacen.setItem(clave, JSON.stringify(valor));
    } catch (e) { /* almacenamiento bloqueado: se sigue sin el */ }
  }
  const cfg = () => leer(CLAVE_CFG, localStorage) || {};
  const ronda = () => leer(CLAVE_RONDA, sessionStorage);
  const fijarRonda = (r) => guardar(CLAVE_RONDA, r, sessionStorage);

  async function esperar(fn, ms, paso = 120) {
    const fin = Date.now() + ms;
    while (Date.now() < fin) {
      if (detenido) return null;
      let v = null;
      try { v = fn(); } catch (e) { v = null; }
      if (v) return v;
      await dormir(paso);
    }
    return null;
  }
  async function quieto() {
    await esperar(() => !cargando(), 20000);
    await dormir(150);
  }
  function clic(el) {
    try { el.scrollIntoView({ block: 'center' }); } catch (e) { /* nada */ }
    for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup']) {
      el.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    }
    el.click();
  }
  async function clicYEspera(el) {
    clic(el);
    await dormir(300);
    await quieto();
  }
  const opciones = (sel) => [...sel.options].filter(
    (o) => o.value && o.value !== '0' && !/seleccion/i.test(o.text));
  function elegir(sel, valor) {
    sel.value = valor;
    sel.dispatchEvent(new Event('input', { bubbles: true }));
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }
  function escribir(input, valor) {
    input.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, valor);
    for (const t of ['input', 'keyup', 'change']) input.dispatchEvent(new Event(t, { bubbles: true }));
    input.blur();
  }
  const selectConOpciones = (css) => () => {
    const s = uno(css);
    return s && opciones(s).length ? s : null;
  };
  const botonDevoluciones = () => [...document.querySelectorAll(S.categoria)]
    .filter(visible).find((e) => /devoluci/i.test(e.innerText || '')) || null;
  const sinCupos = () => (document.body.innerText || '').includes(SIN_CUPOS);
  const ciudadDe = (t) => (t.replace(/[“”"«»]/g, '').trim().split(/[\s-]+/)[0] || '?');
  const reCiudad = () => new RegExp(cfg().ciudad || 'bogot', 'i');

  function vibrar() {
    try { navigator.vibrate && navigator.vibrate([400, 150, 400, 150, 400]); } catch (e) { /* nada */ }
  }

  // ── panel ───────────────────────────────────────────────────────────
  let panel, texto;
  function crearPanel() {
    panel = document.createElement('div');
    panel.id = 'dianRapidoPanel';
    // pointer-events:none: el panel no tapa la pagina (captcha, botones); solo sus botones reciben toques
    panel.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;z-index:2147483647;pointer-events:none;' +
      'display:flex;gap:8px;align-items:center;padding:8px 10px;border-radius:10px;' +
      'font:15px/1.3 system-ui,sans-serif;color:#fff;background:#334;' +
      'box-shadow:0 2px 10px rgba(0,0,0,.35)';
    texto = document.createElement('div');
    texto.style.cssText = 'flex:1;min-width:0';
    texto.textContent = 'Cita rápida lista.';
    const boton = (etiqueta, accion) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = etiqueta;
      b.style.cssText = 'border:0;border-radius:8px;padding:8px 10px;font:600 15px system-ui;' +
        'background:#fff;color:#223;flex:none;pointer-events:auto';
      b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); accion(); });
      return b;
    };
    panel.append(texto,
      boton('⚡', () => { fijarRonda(null); iniciar(); }),
      boton('■', () => { detenido = true; fijarRonda(null); estado('Detenido.'); }),
      boton('⚙', configurar));
    document.body.appendChild(panel);
  }
  function estado(msg, tipo) {
    if (!panel) return;
    texto.textContent = msg;
    panel.style.background = { ok: '#11823b', accion: '#b35c00', error: '#a11' }[tipo] || '#334';
  }
  function configurar() {
    const c = cfg();
    const ced = prompt('Número de cédula (sin puntos):', c.cedula || '');
    if (ced === null) return;
    const nom = prompt('Nombre completo (solo si la página lo pide):', c.nombre || '');
    const cel = prompt('Celular:', c.celular || '');
    const cor = prompt('Correo:', c.correo || '');
    guardar(CLAVE_CFG, {
      cedula: (ced || '').replace(/\D/g, ''), nombre: (nom || '').trim(),
      celular: (cel || '').replace(/\D/g, ''), correo: (cor || '').trim(),
      ciudad: c.ciudad || 'bogot', reintentarMin: c.reintentarMin || 3,
    }, localStorage);
    estado(cfg().cedula ? 'Datos guardados en este navegador.' : 'Falta la cédula (⚙).');
  }

  // ── ruta ────────────────────────────────────────────────────────────
  async function irADevoluciones() {
    const cadena = [[S.video, S.categoria], [S.persona, S.video], [S.agendar, S.persona]];
    for (let i = 0; i < 6; i++) {
      if (detenido) return false;
      await quieto();
      if (botonDevoluciones()) return true;
      let hecho = false;
      for (const [css, siguiente] of cadena) {
        const el = uno(css);
        if (!el) continue;
        await clicYEspera(el);
        if (!await esperar(() => uno(siguiente), 12000)) return false;
        await dormir(250);
        hecho = true;
        break;
      }
      if (!hecho && !await esperar(() => uno(S.agendar) || botonDevoluciones(), 20000)) return false;
    }
    return !!botonDevoluciones();
  }

  function cerrarAviso() {
    for (const css of S.modalCerrar) {
      const el = uno(css);
      if (el) { clic(el); return; }
    }
  }

  async function iniciar() {
    if (corriendo) return;
    corriendo = true;
    detenido = false;
    let r = ronda();
    if (!r || Date.now() > r.fin) {
      r = { fin: Date.now() + (Number(cfg().reintentarMin) || 3) * 60000, recargas: 0 };
      fijarRonda(r);
    }
    try {
      while (!detenido && Date.now() < r.fin) {
        estado('Buscando cupo en Bogotá…');
        if (!await irADevoluciones()) {
          if (detenido) return;
          estado('No pude llegar a Devoluciones. Recargo…');
          return recargar(r);
        }
        await clicYEspera(botonDevoluciones());
        const res = await esperar(
          () => (sinCupos() ? 'sin' : (selectConOpciones(S.servicio)() ? 'lista' : null)), 25000);
        if (detenido) return;
        if (res === 'lista') {
          const sel = selectConOpciones(S.servicio)();
          const ops = opciones(sel);
          const bog = ops.filter((o) => reCiudad().test(o.text));
          if (bog.length) {
            fijarRonda(null);
            const o = bog.find((x) => /el[eé]ctric|h[ií]brid/i.test(x.text)) || bog[0];
            return await agendar(sel, o);
          }
          estado(`Hay cupos en ${[...new Set(ops.map((o) => ciudadDe(o.text)))].join(', ')}, ` +
            'no en Bogotá. Reintento…');
          await dormir(800);
          return recargar(r);
        }
        if (res === 'sin') {
          estado(`Sin cupos en este momento. Sigo intentando hasta las ${hhmm(r.fin)}…`);
          cerrarAviso();
          await dormir(1200);
          continue;
        }
        estado('La página no respondió. Recargo…');
        return recargar(r);
      }
      if (!detenido) {
        fijarRonda(null);
        estado('No salió cupo de Bogotá. Toca ⚡ para intentar otra vez.', 'error');
      }
    } finally {
      corriendo = false;
    }
  }

  function recargar(r) {
    r.recargas = (r.recargas || 0) + 1;
    fijarRonda(r);
    location.reload();
  }

  const manual = (msg) => { estado('Sigue tú: ' + msg, 'accion'); vibrar(); };

  function primerDia() {
    const cal = document.querySelector(S.calendario);
    if (!cal) return null;
    return [...cal.querySelectorAll('td')].find((td) =>
      (td.classList.contains('k-calendar-td') || td.getAttribute('role') === 'gridcell') &&
      !td.classList.contains('k-disabled') && !td.classList.contains('k-state-disabled') &&
      (td.textContent || '').trim()) || null;
  }

  async function agendar(selServicio, opcion) {
    estado('¡Cupo en Bogotá! Avanzando…', 'ok');
    vibrar();
    // Paso 1: servicio y Siguiente
    elegir(selServicio, opcion.value);
    const sig1 = await esperar(() => uno(S.sig1), 8000);
    if (!sig1) return manual('toca Siguiente.');
    await clicYEspera(sig1);

    // Paso 2: ciudad, sede, fecha, hora
    const ciu = await esperar(selectConOpciones(S.ciudad), 15000);
    if (!ciu) return manual('escoge ciudad, sede, fecha y hora.');
    const oc = opciones(ciu);
    elegir(ciu, (oc.find((o) => reCiudad().test(o.text)) || oc[0]).value);
    await dormir(300); await quieto();

    const sede = await esperar(selectConOpciones(S.sede), 15000);
    if (!sede) return manual('escoge sede, fecha y hora.');
    elegir(sede, opciones(sede)[0].value);
    await dormir(300); await quieto();

    const btnFecha = await esperar(() => uno(S.btnFecha), 15000);
    if (!btnFecha) return manual('escoge fecha y hora.');
    if (!document.querySelector('.calendario.calendario-open')) await clicYEspera(btnFecha);
    let dia = await esperar(primerDia, 6000);
    if (!dia) {
      const cal = document.querySelector(S.calendario);
      const sig = cal && cal.querySelector('.k-nav-next, .k-calendar-nav-next');
      if (sig) { clic(sig); dia = await esperar(primerDia, 4000); }
    }
    if (!dia) return manual('escoge fecha y hora.');
    await clicYEspera(dia.querySelector('a, .k-link') || dia);

    const hora = await esperar(selectConOpciones(S.hora), 15000);
    if (!hora) return manual('escoge la hora.');
    elegir(hora, opciones(hora)[0].value);
    await dormir(300); await quieto();

    const sig2 = await esperar(() => uno(S.sig2), 8000);
    if (!sig2) return manual('toca Siguiente.');
    await clicYEspera(sig2);

    // Paso 3: el captcha lo marca la persona; despues aparece la cedula
    estado('Marca "No soy un robot". Después lleno tu cédula.', 'accion');
    vibrar();
    const captcha = await esperar(() => uno('div[pantalla="PasoTres"][nombre="Captcha"]'), 5000);
    if (captcha) captcha.scrollIntoView({ block: 'center' });
    const doc = await esperar(() => uno(S.numDoc), 5 * 60000, 200);
    if (!doc) return;
    const c = cfg();
    if (!c.cedula) return manual('escribe tu cédula (no está guardada, botón ⚙).');
    escribir(doc, c.cedula);
    await dormir(1500); await quieto();
    for (const [css, valor] of [[S.nombre, c.nombre], [S.tel, c.celular], [S.correo, c.correo]]) {
      const el = uno(css);
      if (el && valor && !el.value) escribir(el, valor);
    }
    estado('Listo: revisa tus datos, carga los 10 PDF en "Documentos", acepta la política, ' +
      'Siguiente y "Agendar cita".', 'ok');
    vibrar();
  }

  // ── arranque ────────────────────────────────────────────────────────
  function arrancar() {
    crearPanel();
    estado(cfg().cedula ? `Cita rápida v${VERSION}: toca ⚡ para buscar.`
      : `Cita rápida v${VERSION}: guarda tu cédula con ⚙.`);
    const r = ronda();
    if (location.hash === '#auto') {
      history.replaceState(null, '', location.pathname + location.search);
      fijarRonda(null);
      iniciar();
    } else if (r && Date.now() < r.fin) {
      iniciar(); // viene de una recarga durante la busqueda
    } else if (r) {
      fijarRonda(null);
      estado('No salió cupo de Bogotá. Toca ⚡ para intentar otra vez.', 'error');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
  else arrancar();
})();
