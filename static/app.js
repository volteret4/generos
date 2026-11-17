"use strict";
const $ = (s) => document.querySelector(s);
const SEP = " / ";
let TAX = [], BYID = {}, BYNAME = {};
let estado = { offset: 0, limit: 100, total: 0, items: [], abierto: null, sel: new Set() };

async function api(url, opts = {}) {
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : String(k));
  return e;
}

/* ---------- taxonomía ---------- */

async function cargarTax() {
  TAX = await api("/api/taxonomia");
  BYID = {}; BYNAME = {};
  const dl = $("#dl-generos");
  dl.innerHTML = "";
  for (const s of TAX) {
    BYID[s.id] = s; BYNAME[s.name.toLowerCase()] = s;
    dl.append(el("option", { value: s.name }, "supergénero"));
    for (const g of s.subs) {
      BYID[g.id] = g; BYNAME[g.name.toLowerCase()] = g;
      dl.append(el("option", { value: g.name }, s.name));
    }
  }
}

function chipGenero(g, extra = {}) {
  const cls = "chip tax " + (g.parent_id == null ? "super" : "sub");
  return el("li", { class: cls, title: g.parent_id == null ? "supergénero" : "subgénero de " + (BYID[g.parent_id] || {}).name, ...extra }, g.name);
}

/* ---------- lista ---------- */

async function cargarLista() {
  const p = new URLSearchParams({ offset: estado.offset, limit: estado.limit });
  if ($("#f-root").value) p.set("root", $("#f-root").value);
  if ($("#f-status").value) p.set("status", $("#f-status").value);
  if ($("#f-q").value.trim()) p.set("q", $("#f-q").value.trim());
  const j = await api("/api/items?" + p);
  estado.total = j.total; estado.items = j.items;
  const tb = $("#filas");
  tb.innerHTML = "";
  for (const it of j.items) {
    const cb = el("input", { type: "checkbox", onclick: (ev) => { ev.stopPropagation(); clickSel(it.id, ev.target.checked, ev.shiftKey); } });
    cb.checked = estado.sel.has(it.id);
    const gen = it.genres.length
      ? el("span", { class: "chips" }, it.genres.map((g) => chipGenero(g)), el("span", { class: "estado " + it.status }, it.status))
      : el("span", { class: "raw" }, it.genre_raw || "—");
    const tr = el("tr", { class: "fila" + (estado.abierto && estado.abierto.id === it.id ? " abierta" : ""), onclick: () => abrir(it.id) },
      el("td", {}, cb),
      el("td", {}, it.artist || "—"),
      el("td", {}, it.kind === "track" ? (it.title || it.path.split("/").pop()) : (it.album || it.path.split("/").pop()),
         el("div", { class: "dim" }, it.root + (it.kind === "album" ? ` · ${it.tracks} pistas` : ""))),
      el("td", {}, it.year || ""),
      el("td", {}, gen));
    tr.dataset.id = it.id;
    tb.append(tr);
  }
  $("#pag-info").textContent = `${estado.offset + 1}–${Math.min(estado.offset + estado.limit, estado.total)} de ${estado.total}`;
  pintarLote();
}

let ultimoClic = null;
function clickSel(id, on, shift) {
  const ids = estado.items.map((i) => i.id);
  if (shift && ultimoClic !== null && ids.includes(ultimoClic)) {
    const [a, b] = [ids.indexOf(ultimoClic), ids.indexOf(id)].sort((x, y) => x - y);
    for (const i of ids.slice(a, b + 1)) on ? estado.sel.add(i) : estado.sel.delete(i);
    document.querySelectorAll("#filas tr").forEach((tr) => { tr.querySelector("input").checked = estado.sel.has(+tr.dataset.id); });
  } else {
    on ? estado.sel.add(id) : estado.sel.delete(id);
  }
  ultimoClic = id;
  pintarLote();
}

/* los ids sobre los que actúan los botones del editor: el abierto + los marcados */
function destino() {
  const ids = new Set(estado.sel);
  if (estado.abierto) ids.add(estado.abierto.id);
  return [...ids];
}

function pintarLote() {
  const n = estado.sel.size;
  $("#lote").hidden = n === 0;
  $("#lote-n").textContent = `${n} marcados · los botones del editor se aplican al abierto y a los marcados`;
  const t = destino().length;
  $("#e-guardar").textContent = t > 1 ? `Guardar (${t})` : "Guardar";
  $("#e-escribir").textContent = t > 1 ? `Guardar y escribir (${t})` : "Guardar y escribir";
}

/* ---------- editor ---------- */

let gen = [];   // ids en orden

async function abrir(id) {
  $("#editor").hidden = false;
  document.querySelectorAll("tr.fila").forEach((t) => t.classList.toggle("abierta", +t.dataset.id === id));
  $("#e-prop").innerHTML = "<span class='dim'>consultando Last.fm, Discogs, MusicBrainz…</span>";
  const it = await api(`/api/item/${id}?web=0`);      // rápido: fichero + escuchowsky
  pintarEditor(it);
  const full = await api(`/api/item/${id}`);           // y luego las fuentes web
  if (estado.abierto && estado.abierto.id === id) pintarProp(full.proposals);
}

function pintarEditor(it) {
  estado.abierto = it;
  $("#e-titulo").textContent = `${it.artist || "—"} — ${it.kind === "track" ? it.title : it.album}${it.year ? " (" + it.year + ")" : ""}`;
  $("#e-ruta").textContent = it.path;
  $("#e-raw").textContent = it.genre_raw || "(vacío)";
  const st = $("#e-estado");
  st.textContent = it.status; st.className = "estado " + it.status;
  gen = it.genres.map((g) => g.id);
  // sin géneros guardados: se parte de lo que haya en el fichero y ya esté en la taxonomía
  if (!gen.length) {
    for (const p of it.proposals) if (p.genre && p.sources.includes("fichero") && !gen.includes(p.genre.id)) gen.push(p.genre.id);
    ponerSuperPrimero();
  }
  pintarGeneros();
  pintarProp(it.proposals);
  pintarLote();
}

function ponerSuperPrimero() {
  const i = gen.findIndex((g) => BYID[g] && BYID[g].parent_id == null);
  if (i > 0) gen.unshift(gen.splice(i, 1)[0]);
  // si solo hay subgéneros, se añade el padre del primero
  if (gen.length && i === -1) {
    const p = BYID[gen[0]].parent_id;
    if (p && !gen.includes(p)) gen.unshift(p);
  }
}

function pintarGeneros() {
  const ol = $("#e-generos");
  ol.innerHTML = "";
  gen.forEach((id, i) => {
    const g = BYID[id];
    if (!g) return;
    const li = chipGenero(g, { draggable: "true" });
    if (i === 0 && g.parent_id != null) li.classList.add("malo");
    li.append(el("span", { class: "x", title: "quitar", onclick: () => { gen.splice(i, 1); pintarGeneros(); } }, "×"));
    li.addEventListener("dragstart", (ev) => ev.dataTransfer.setData("text/plain", i));
    li.addEventListener("dragover", (ev) => ev.preventDefault());
    li.addEventListener("drop", (ev) => {
      ev.preventDefault();
      const from = +ev.dataTransfer.getData("text/plain");
      gen.splice(i, 0, gen.splice(from, 1)[0]);
      pintarGeneros();
    });
    ol.append(li);
  });
  const ok = gen.length && BYID[gen[0]] && BYID[gen[0]].parent_id == null;
  const pv = $("#e-preview");
  pv.textContent = gen.length ? "GENRE = " + gen.map((g) => BYID[g].name).join(SEP) + (ok ? "" : "   ← el primero tiene que ser un supergénero") : "";
  pv.className = "preview" + (ok ? "" : " malo");
  // las propuestas ya puestas se atenúan
  document.querySelectorAll("#e-prop .chip").forEach((c) => c.classList.toggle("ya", gen.includes(+c.dataset.gid)));
}

function pintarProp(props) {
  const box = $("#e-prop");
  box.innerHTML = "";
  if (!props.length) box.append(el("span", { class: "dim" }, "sin propuestas"));
  for (const p of props) {
    const known = !!p.genre;
    const c = el("span", {
      class: "chip" + (known ? " tax " + (p.genre.parent_id == null ? "super" : "sub") : ""),
      title: (known ? "" : "no está en la taxonomía · ") + "fuentes: " + p.sources.join(", "),
      onclick: () => known ? anadir(p.genre.id) : dialogoNuevo(p.text),
    }, p.text, el("span", { class: "src" }, p.sources.map((s) => s[0]).join("")));
    if (known) c.dataset.gid = p.genre.id;
    box.append(c);
  }
  pintarGeneros();
}

function anadir(id) {
  if (!gen.includes(id)) gen.push(id);
  if (gen.length === 1 || BYID[gen[0]].parent_id != null) ponerSuperPrimero();
  pintarGeneros();
}

async function guardar(escribir) {
  const it = estado.abierto;
  const ids = destino();
  const nombres = gen.map((g) => BYID[g].name).join(SEP);
  if (ids.length > 1 && !confirm(`¿${escribir ? "Guardar y escribir" : "Guardar"} «${nombres}» en ${ids.length} discos/canciones (el abierto y los marcados)?`)) return;
  try {
    if (ids.length > 1) await api("/api/lote", { method: "POST", body: { ids, genres: gen } });
    else await api(`/api/item/${it.id}/generos`, { method: "POST", body: { genres: gen } });
    if (escribir) {
      const r = await api("/api/aplicar", { method: "POST", body: { ids } });
      const err = r.results.filter((x) => x.error || (x.errors && x.errors.length));
      if (err.length) alert(`${err.length} con errores al escribir:\n` + err.map((x) => x.id + ": " + (x.error || x.errors.join("; "))).join("\n"));
    }
  } catch (e) { alert(e.message); return; }
  if (ids.length > 1) { estado.sel.clear(); ultimoClic = null; }
  await cargarLista();
  await cargarStats();
  const fresh = await api(`/api/item/${it.id}?web=0`);
  pintarEditor(fresh);
}

/* ---------- crear / asociar un género nuevo ---------- */

async function dialogoNuevo(texto) {
  const d = $("#dlg-nuevo");
  $("#n-texto").textContent = texto;
  $("#n-nombre").value = texto.replace(/\b\w/g, (c) => c.toUpperCase());
  const sa = $("#n-alias"), sp = $("#n-padre");
  sa.innerHTML = ""; sp.innerHTML = "";
  for (const s of TAX) {
    sp.append(el("option", { value: s.id }, s.name));
    const og = el("optgroup", { label: s.name });
    og.append(el("option", { value: s.id }, s.name));
    for (const g of s.subs) og.append(el("option", { value: g.id }, g.name));
    sa.append(og);
  }
  $("#n-rym").textContent = "";
  try {
    const sg = await api("/api/sugerir_padre?name=" + encodeURIComponent(texto));
    if (sg.parent) { sp.value = sg.parent.id; d.querySelector("[value=sub]").checked = true; }
    if (sg.rym && sg.rym.length) $("#n-rym").textContent = "RYM: " + sg.rym.join(" › ");
  } catch (e) { /* sin sugerencia */ }
  d.returnValue = "";
  d.showModal();
  d.onclose = async () => {
    if (d.returnValue !== "ok") return;
    const modo = d.querySelector("[name=n-modo]:checked").value;
    try {
      let id;
      if (modo === "alias") {
        id = +sa.value;
        await api("/api/alias", { method: "POST", body: { text: texto, genre_id: id } });
      } else {
        const body = { name: $("#n-nombre").value.trim(), aliases: [texto] };
        if (modo === "sub") body.parent_id = +sp.value;
        id = (await api("/api/genero", { method: "POST", body })).id;
      }
      await cargarTax();
      anadir(id);
      const full = await api(`/api/item/${estado.abierto.id}`);
      pintarProp(full.proposals);
    } catch (e) { alert(e.message); }
  };
}

/* ---------- vista de taxonomía ---------- */

function pintarTax() {
  const box = $("#tax");
  box.innerHTML = "";
  for (const s of TAX) {
    const ul = el("ul", {});
    for (const g of s.subs) {
      ul.append(el("li", {},
        el("span", {}, g.name, " ", el("span", { class: "n" }, g.items || ""),
           g.aliases.length > 1 ? el("div", { class: "alias" }, "= " + g.aliases.join(", ")) : null),
        el("span", { class: "ops" },
          el("button", { title: "renombrar", onclick: () => renombrar(g) }, "✎"),
          el("button", { title: "mover a otro supergénero", onclick: () => mover(g) }, "⇄"),
          el("button", { title: "borrar", onclick: () => borrar(g) }, "×"))));
    }
    box.append(el("div", { class: "sup" },
      el("h4", {}, el("span", {}, s.name, " ", el("span", { class: "n" }, s.items || "")),
        el("span", { class: "ops" },
          el("button", { title: "nuevo subgénero", onclick: () => nuevoSub(s) }, "+"),
          el("button", { title: "renombrar", onclick: () => renombrar(s) }, "✎"),
          el("button", { title: "borrar", onclick: () => borrar(s) }, "×"))),
      s.aliases.length > 1 ? el("div", { class: "alias" }, "= " + s.aliases.join(", ")) : null,
      ul));
  }
}

async function accionTax(fn) {
  try { await fn(); await cargarTax(); pintarTax(); } catch (e) { alert(e.message); }
}
const renombrar = (g) => { const n = prompt("Nuevo nombre (los discos con este género habrá que reescribirlos):", g.name); if (n) accionTax(() => api(`/api/genero/${g.id}`, { method: "PATCH", body: { name: n } })); };
const borrar = (g) => { if (confirm(`¿Borrar «${g.name}»?`)) accionTax(() => api(`/api/genero/${g.id}`, { method: "DELETE" })); };
const nuevoSub = (s) => { const n = prompt(`Nuevo subgénero de ${s.name}:`); if (n) accionTax(() => api("/api/genero", { method: "POST", body: { name: n, parent_id: s.id } })); };
const mover = (g) => {
  const n = prompt("Nombre del supergénero al que pasa (vacío = que sea supergénero):", BYID[g.parent_id].name);
  if (n === null) return;
  const dest = n.trim() ? BYNAME[n.trim().toLowerCase()] : null;
  if (n.trim() && (!dest || dest.parent_id != null)) { alert("No existe ese supergénero"); return; }
  accionTax(() => api(`/api/genero/${g.id}`, { method: "PATCH", body: { parent_id: dest ? dest.id : null } }));
};

/* ---------- estado y escaneo ---------- */

async function cargarStats() {
  const j = await api("/api/stats");
  const partes = Object.entries(j.roots).map(([r, s]) =>
    `${r}: ${s.escrito || 0} escritos · ${s.guardado || 0} guardados · ${s.pendiente || 0} pendientes`);
  if (j.mix_pending) partes.push(`${j.mix_pending} esperando a escribirse en otro equipo`);
  const sel = $("#f-root");
  for (const r of Object.keys(j.roots)) {
    if (![...sel.options].some(o => o.value === r)) sel.add(new Option(r, r));
  }
  const sc = j.scan;
  if (sc.activo) partes.unshift(`escaneando: ${sc.fase} ${sc.hechos}/${sc.total}`);
  else if (sc.error) partes.unshift(`error al escanear: ${sc.error}`);
  $("#stats").textContent = partes.join("  |  ");
  return sc.activo;
}

async function vigilarEscaneo() {
  while (await cargarStats()) await new Promise((r) => setTimeout(r, 2000));
  cargarLista();
}

/* ---------- arranque ---------- */

document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("on", x === b));
  for (const v of ["discos", "taxonomia", "jugar"]) $("#v-" + v).hidden = b.dataset.view !== v;
  if (b.dataset.view === "taxonomia") cargarTax().then(pintarTax);
  if (b.dataset.view === "jugar") jEntrar();
}));
let tq;
$("#f-q").addEventListener("input", () => { clearTimeout(tq); tq = setTimeout(() => { estado.offset = 0; cargarLista(); }, 300); });
$("#f-root").addEventListener("change", () => { estado.offset = 0; cargarLista(); });
$("#f-status").addEventListener("change", () => { estado.offset = 0; cargarLista(); });
$("#prev").addEventListener("click", () => { estado.offset = Math.max(0, estado.offset - estado.limit); cargarLista(); });
$("#next").addEventListener("click", () => { if (estado.offset + estado.limit < estado.total) { estado.offset += estado.limit; cargarLista(); } });
$("#sel-all").addEventListener("change", (ev) => {
  for (const it of estado.items) ev.target.checked ? estado.sel.add(it.id) : estado.sel.delete(it.id);
  cargarLista();
});
$("#lote-quitar").addEventListener("click", () => { estado.sel.clear(); ultimoClic = null; $("#sel-all").checked = false; cargarLista(); });
$("#e-buscar").addEventListener("change", (ev) => {
  const g = BYNAME[ev.target.value.trim().toLowerCase()];
  if (g) { anadir(g.id); ev.target.value = ""; }
});
$("#e-guardar").addEventListener("click", () => guardar(false));
$("#e-escribir").addEventListener("click", () => guardar(true));
$("#lote-escribir").addEventListener("click", async () => {
  if (!confirm(`¿Escribir GENRE en los ficheros de ${estado.sel.size} seleccionados? (solo los que tienen géneros guardados)`)) return;
  const r = await api("/api/aplicar", { method: "POST", body: { ids: [...estado.sel] } });
  const err = r.results.filter((x) => x.error || (x.errors && x.errors.length));
  if (err.length) alert(`${err.length} con errores:\n` + err.map((x) => x.id + ": " + (x.error || x.errors.join("; "))).join("\n"));
  estado.sel.clear();
  cargarLista(); cargarStats();
});
$("#btn-scan").addEventListener("click", async () => { await api("/api/escanear", { method: "POST", body: {} }); vigilarEscaneo(); });
$("#t-crear").addEventListener("click", () => {
  const n = $("#t-nuevo").value.trim();
  if (n) accionTax(() => api("/api/genero", { method: "POST", body: { name: n } })).then(() => { $("#t-nuevo").value = ""; });
});

cargarTax().then(cargarLista);
vigilarEscaneo();
