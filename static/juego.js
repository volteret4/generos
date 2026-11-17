"use strict";
/* Modo «Jugar»: lotes de discos pendientes parecidos a un género o artista; cada disco con sus géneros.
   Usa $, el, api, chipGenero, abrir, cargarTax, cargarStats, BYID, BYNAME, TAX y SEP de app.js */

const J = { lote: null, sel: new Set(), gen: {}, pistas: {} };
const TROZO = 30;                       // segundos de cada pista antes de pasar a la siguiente
const P = { card: null, it: null, list: [], idx: 0, timer: null };   // reproductor (uno a la vez)   // gen: clave de disco → [ids de género]

const key = (it) => it.ids[0];
const valido = (gs) => gs.length > 0 && BYID[gs[0]] && BYID[gs[0]].parent_id == null;

function jToast(html, cls = "") {
  const t = el("div", { class: "j-toast " + cls });
  t.innerHTML = html;
  document.body.append(t);
  setTimeout(() => t.classList.add("fuera"), 2600);
  setTimeout(() => t.remove(), 3200);
}

/* ---------- estadísticas ---------- */

async function jStats(s) {
  s = s || await api("/api/juego/stats");
  const pct = Math.round(100 * (s.xp - s.desde) / Math.max(1, s.siguiente - s.desde));
  const hechos = s.total - s.pendientes;
  const box = $("#j-stats");
  box.innerHTML = "";
  box.append(
    el("div", { class: "j-nivel" },
      el("b", {}, `Nivel ${s.nivel} · ${s.titulo}`),
      el("div", { class: "j-barra" }, el("i", { style: `width:${pct}%` })),
      el("span", { class: "dim" }, `${s.xp} / ${s.siguiente} puntos`)),
    el("div", { class: "j-num" }, el("b", {}, s.hoy), el("span", { class: "dim" }, "discos hoy")),
    el("div", { class: "j-num" }, el("b", {}, s.racha + (s.racha ? " 🔥" : "")), el("span", { class: "dim" }, "días seguidos")),
    el("div", { class: "j-num" }, el("b", {}, s.pendientes), el("span", { class: "dim" }, "pendientes")),
    el("div", { class: "j-nivel" },
      el("b", {}, `${Math.round(100 * hechos / Math.max(1, s.total))}% de la biblioteca`),
      el("div", { class: "j-barra ok" }, el("i", { style: `width:${100 * hechos / Math.max(1, s.total)}%` })),
      el("span", { class: "dim" }, `${hechos} de ${s.total} con géneros`)),
  );
  if (!s.audiomuse) box.append(el("div", { class: "dim" }, "AudioMuse sin configurar: los lotes solo usan las etiquetas de los ficheros."));
  const max = Math.max(1, ...s.dias.map((d) => d.n));
  const byDay = Object.fromEntries(s.dias.map((d) => [d.day, d.n]));
  const g = $("#j-dias");
  g.innerHTML = "";
  for (let i = 29; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    const n = byDay[d] || 0;
    g.append(el("i", { style: `height:${Math.max(2, 48 * n / max)}px`, title: `${d}: ${n}`, class: n ? "" : "cero" }));
  }
  const lg = $("#j-logros");
  lg.innerHTML = "";
  for (const l of s.logros) {
    lg.append(el("div", { class: "j-logro" + (l.ts ? " si" : ""), title: l.desc + (l.ts ? ` · ${l.ts.slice(0, 10)}` : "") },
      el("b", {}, (l.ts ? "🏅 " : "🔒 ") + l.titulo), el("span", { class: "dim" }, l.desc)));
  }
}

/* ---------- semilla ---------- */

function jPintarSemillas() {
  const s = $("#j-genero");
  const prev = s.value;
  s.innerHTML = "";
  for (const sup of TAX) {
    const og = el("optgroup", { label: sup.name });
    og.append(el("option", { value: sup.id }, `${sup.name} (todo) · ${sup.items}`));
    for (const g of sup.subs) og.append(el("option", { value: g.id }, `${g.name} · ${g.items}`));
    s.append(og);
  }
  if (prev) s.value = prev;
}

let jtq;
function jArtistas() {
  clearTimeout(jtq);
  jtq = setTimeout(async () => {
    const r = await api("/api/juego/artistas?q=" + encodeURIComponent($("#j-artista").value));
    const dl = $("#dl-artistas");
    dl.innerHTML = "";
    for (const a of r) dl.append(el("option", { value: a.artist }, `${a.pendientes} pendientes`));
  }, 200);
}

function jModo() {
  const g = $("#j-modo").value === "genero";
  $("#j-genero").hidden = !g;
  $("#j-artista").hidden = g;
}

async function jDame() {
  const p = new URLSearchParams({ n: $("#j-n").value });
  if ($("#j-modo").value === "genero") p.set("genre_id", $("#j-genero").value);
  else {
    const a = $("#j-artista").value.trim();
    if (!a) { $("#j-artista").focus(); return; }
    p.set("artist", a);
  }
  $("#j-dame").disabled = true;
  $("#j-info").textContent = "Buscando parecidos… (la primera vez con AudioMuse tarda unos segundos)";
  $("#j-lote").hidden = false;
  $("#j-grid").innerHTML = "";
  history.replaceState(null, "", "#jugar&" + (p.get("genre_id") ? "genero=" + p.get("genre_id") : "artista=" + encodeURIComponent(p.get("artist"))));
  try {
    J.lote = await api("/api/juego/lote?" + p);
  } catch (e) {
    $("#j-info").textContent = "Error: " + e.message;
    return;
  } finally {
    $("#j-dame").disabled = false;
  }
  J.gen = {};
  J.sel = new Set();
  for (const it of J.lote.items) {
    J.gen[key(it)] = [...(it.suggest || [])];
    if (it.score >= 0.25 && valido(J.gen[key(it)])) J.sel.add(key(it));
  }
  const modo = $("#j-modo").value === "genero"
    ? (J.lote.seeds ? `parecidos a ${J.lote.seeds} discos tuyos` : "por etiquetas y AudioMuse") : "artista y parecidos";
  $("#j-info").textContent = J.lote.items.length
    ? `${J.lote.items.length} discos pendientes (${modo}). Cada uno trae su propuesta: revísala, cámbiala si hace falta y guarda.`
    : "No hay pendientes parecidos para esta semilla. Prueba otra.";
  const grid = $("#j-grid");
  for (const it of J.lote.items) grid.append(jCard(it));
  jCuenta();
}

function jCuenta() {
  const n = [...J.sel].filter((k) => valido(J.gen[k] || [])).length;
  $("#j-cuenta").textContent = J.lote ? `${J.lote.items.length} en el lote · ${n} marcados listos` : "";
  $("#j-guardar").disabled = !n;
  $("#j-escribir").disabled = !n;
}

/* ---------- tarjeta de un disco ---------- */

function jCard(it) {
  const k = key(it);
  const card = el("div", { class: "j-card", "data-k": k });
  const cb = el("input", { type: "checkbox", title: "Marcar para «Guardar marcados»" });
  const marcar = (on) => {
    if (on) J.sel.add(k); else J.sel.delete(k);
    cb.checked = J.sel.has(k);
    card.classList.toggle("on", J.sel.has(k));
    jCuenta();
  };
  cb.addEventListener("change", () => marcar(cb.checked));

  const img = el("img", { loading: "lazy", src: `/api/juego/portada/${it.id}`, alt: "" });
  img.addEventListener("error", () => img.replaceWith(el("div", { class: "j-sin" }, "♪")));
  const play = el("button", { class: "j-play", title: "Escuchar un trozo de cada pista" }, "▶");
  play.addEventListener("click", () => jEscuchar(it, card));
  const player = el("div", { class: "j-player", hidden: "" },
    el("div", { class: "j-ctrl" },
      el("button", { title: "Pista anterior", onclick: () => jPista(P.idx - 1) }, "⏮"),
      el("span", { class: "j-pista" }),
      el("button", { title: "Siguiente pista", onclick: () => jPista(P.idx + 1) }, "⏭")),
    el("ol", { class: "j-lista" }));

  const nombre = it.album || it.title;
  const pistas = it.n_tracks > 1 ? ` · ${it.n_tracks} pistas` : "";

  // géneros de este disco
  const chips = el("ol", { class: "chips j-chips" });
  const prev = el("div", { class: "preview j-prev" });
  const pintar = () => {
    const gs = J.gen[k];
    chips.innerHTML = "";
    gs.forEach((id, i) => {
      const g = BYID[id];
      if (!g) return;
      const li = chipGenero(g);
      li.append(el("span", { class: "x", title: "quitar", onclick: () => { gs.splice(i, 1); pintar(); } }, "×"));
      chips.append(li);
    });
    const ok = valido(gs);
    prev.textContent = gs.length ? gs.map((g) => (BYID[g] || {}).name).join(SEP) : "sin géneros";
    prev.className = "preview j-prev" + (ok ? "" : " malo");
    guardar.disabled = !ok;
    if (!ok && J.sel.has(k)) marcar(false); else jCuenta();
  };
  const anadir = el("input", { list: "dl-generos", placeholder: "+ género…", class: "j-add" });
  anadir.addEventListener("change", () => {
    const g = BYNAME[anadir.value.trim().toLowerCase()];
    anadir.value = "";
    if (!g) return;
    const gs = J.gen[k];
    if (gs.includes(g.id)) return;
    if (g.parent_id == null) {
      if (gs.length && BYID[gs[0]] && BYID[gs[0]].parent_id == null) gs.splice(0, 1, g.id);   // cambia el supergénero
      else gs.unshift(g.id);
    } else {
      if (!valido(gs)) gs.unshift(g.parent_id);
      gs.push(g.id);
    }
    pintar();
    marcar(true);
  });
  const alts = Object.entries(it.alts || {}).map(([fuente, gs]) =>
    el("a", { href: "#", class: "j-alt", title: "usar esta propuesta",
      onclick: (ev) => { ev.preventDefault(); J.gen[k] = [...gs]; pintar(); marcar(true); } },
      `${fuente}: ${gs.map((g) => (BYID[g] || {}).name).join(SEP)}`));

  const guardar = el("button", { class: "prim", title: "Guardar los géneros de este disco" }, "✔ Guardar");
  guardar.addEventListener("click", () => jGuardar([it], false));

  card.append(
    el("div", { class: "j-img" }, img, play, el("span", { class: "j-root", title: it.roots.length > 1 ? "está en varias raíces: se guardan todas" : "" }, it.roots.join(" + "))),
    player,
    el("div", { class: "j-txt" },
      el("label", { class: "j-cab" }, cb, el("b", {}, it.artist || "¿?")),
      el("div", {}, nombre || "¿?", el("span", { class: "dim" }, (it.year ? ` · ${it.year}` : "") + pistas)),
      el("div", { class: "j-score", title: "lo probable que es" }, el("i", { style: `width:${Math.round(100 * it.score)}%` })),
      el("div", { class: "dim j-why" }, it.reasons.join(" · ")),
      it.genre_raw ? el("div", { class: "dim j-raw" }, "fichero: ", el("code", {}, it.genre_raw)) : null,
      chips, prev, anadir,
      alts.length ? el("div", { class: "j-alts" }, ...alts) : null,
      el("div", { class: "j-ops" },
        guardar,
        el("button", { onclick: () => jNoEs(it, card), title: "No encaja en este lote: no volverá a salir con esta semilla" }, "No es"),
        el("button", { onclick: () => jEditar(it.id), title: "Abrir en el editor completo, con todas las propuestas" }, "Editar"))),
  );
  pintar();
  marcar(J.sel.has(k));
  return card;
}

/* ---------- reproductor: un trozo de cada pista, saltando entre ellas ---------- */

function jParar() {
  const a = $("#j-audio");
  a.pause();
  a.removeAttribute("src");
  clearTimeout(P.timer);
  if (P.card) {
    P.card.classList.remove("sonando");
    const box = P.card.querySelector(".j-player");
    if (box) box.hidden = true;
    P.card.querySelector(".j-play").textContent = "▶";
  }
  P.card = null;
}

async function jEscuchar(it, card) {
  if (P.card === card) { jParar(); return; }
  jParar();
  P.card = card;
  P.it = it;
  card.classList.add("sonando");
  card.querySelector(".j-play").textContent = "■";
  const box = card.querySelector(".j-player");
  box.hidden = false;
  box.querySelector(".j-pista").textContent = "cargando pistas…";
  const k = key(it);
  if (!J.pistas[k]) {
    try { J.pistas[k] = await api("/api/juego/pistas?ids=" + it.ids.join(",")); }
    catch (e) { box.querySelector(".j-pista").textContent = "no encuentro las pistas"; return; }
  }
  if (P.card !== card) return;                 // pulsó otro mientras cargaba
  P.list = J.pistas[k];
  const ul = box.querySelector(".j-lista");
  ul.innerHTML = "";
  P.list.forEach((t, i) => ul.append(el("li", { onclick: () => jPista(i) }, `${i + 1}. ${t.title}`)));
  jPista(0);
}

function jPista(i) {
  if (!P.card || !P.list.length) return;
  P.idx = (i + P.list.length) % P.list.length;
  const t = P.list[P.idx];
  const box = P.card.querySelector(".j-player");
  box.querySelector(".j-pista").textContent = `${P.idx + 1}/${P.list.length} · ${t.title}`;
  box.querySelectorAll(".j-lista li").forEach((li, j) => li.classList.toggle("on", j === P.idx));
  const cur = box.querySelectorAll(".j-lista li")[P.idx];
  if (cur) cur.scrollIntoView({ block: "nearest" });
  clearTimeout(P.timer);
  const a = $("#j-audio");
  let saltado = false;
  a.onloadedmetadata = () => {
    if (!saltado && isFinite(a.duration) && a.duration > TROZO + 20) a.currentTime = a.duration * (0.3 + Math.random() * 0.25);
    saltado = true;
  };
  a.onplaying = () => { clearTimeout(P.timer); P.timer = setTimeout(() => jPista(P.idx + 1), TROZO * 1000); };
  a.onended = () => jPista(P.idx + 1);
  a.onerror = () => {
    box.querySelector(".j-pista").textContent = `${P.idx + 1}/${P.list.length} · ✖ no se puede reproducir ${t.title}`;
    clearTimeout(P.timer);
    P.timer = setTimeout(() => jPista(P.idx + 1), 1500);
  };
  a.src = `/api/juego/audio/${t.id}?n=${t.n}`;
  a.play().catch((e) => {     // dentro del clic, para que el navegador lo permita
    if (e.name === "NotAllowedError") box.querySelector(".j-pista").textContent = "el navegador bloqueó el audio: pulsa ▶ otra vez";
  });
}

async function jNoEs(it, card) {
  await api("/api/juego/no", { method: "POST", body: { seed: J.lote.seed, ids: it.ids } });
  J.sel.delete(key(it));
  J.lote.items = J.lote.items.filter((x) => key(x) !== key(it));
  if (P.card === card) jParar();
  card.classList.add("fuera");
  setTimeout(() => card.remove(), 300);
  jCuenta();
}

function jEditar(id) {
  document.querySelector('nav button[data-view="discos"]').click();
  abrir(id);
}

/* ---------- guardar ---------- */

async function jGuardar(discos, escribir) {
  discos = discos.filter((it) => valido(J.gen[key(it)] || []));
  if (!discos.length) return;
  const items = discos.map((it) => ({ ids: it.ids, genres: J.gen[key(it)] }));
  let r;
  try {
    r = await api("/api/juego/hecho", { method: "POST", body: { items, mode: $("#j-modo").value, seed: J.lote.seed } });
  } catch (e) { alert(e.message); return; }
  if (escribir) {
    const ids = discos.flatMap((it) => it.ids);
    const w = await api("/api/aplicar", { method: "POST", body: { ids } });
    const err = w.results.filter((x) => x.error || (x.errors && x.errors.length));
    if (err.length) alert(`${err.length} con errores al escribir:\n` + err.map((x) => x.id + ": " + (x.error || x.errors.join("; "))).join("\n"));
  }
  if (r.xp) jToast(`<b>+${r.xp}</b> puntos${r.bonus ? ` (incluye +${r.bonus} por 10 o más de golpe)` : ""}`);
  if (r.subida) jToast(`⬆️ ¡Nivel ${r.stats.nivel}! <b>${r.stats.titulo}</b>`, "grande");
  for (const l of r.logros) jToast(`🏅 Logro: <b>${l.titulo}</b><br><span class="dim">${l.desc}</span>`, "grande");
  for (const a of r.artistas_completos) jToast(`✅ ${a}: sin pendientes`);
  const fuera = new Set(discos.map(key));
  for (const k of fuera) {
    const c = document.querySelector(`.j-card[data-k="${k}"]`);
    if (c && P.card === c) jParar();
    if (c) { c.classList.add("hecho"); setTimeout(() => c.remove(), 400); }
    J.sel.delete(k);
  }
  J.lote.items = J.lote.items.filter((x) => !fuera.has(key(x)));
  jCuenta();
  jStats(r.stats);
  cargarStats();
  if (!J.lote.items.length) $("#j-info").textContent = "¡Lote terminado! Pide otro con la misma semilla o cambia de género/artista.";
}

const marcados = () => J.lote.items.filter((it) => J.sel.has(key(it)));

/* ---------- arranque ---------- */

async function jEntrar() {
  if (!TAX.length) await cargarTax();
  jPintarSemillas();
  jStats();
}

$("#j-modo").addEventListener("change", jModo);
$("#j-artista").addEventListener("input", jArtistas);
$("#j-dame").addEventListener("click", jDame);
$("#j-guardar").addEventListener("click", () => jGuardar(marcados(), false));
$("#j-escribir").addEventListener("click", () => {
  const n = marcados().length;
  if (confirm(`¿Guardar y escribir GENRE en los ficheros de ${n} discos marcados?`)) jGuardar(marcados(), true);
});
$("#j-todos").addEventListener("click", () => document.querySelectorAll(".j-card input[type=checkbox]").forEach((cb) => {
  const k = +cb.closest(".j-card").dataset.k;
  if (!cb.checked && valido(J.gen[k] || [])) { cb.checked = true; cb.dispatchEvent(new Event("change")); }
}));
$("#j-ninguno").addEventListener("click", () => document.querySelectorAll(".j-card input[type=checkbox]").forEach((cb) => {
  if (cb.checked) { cb.checked = false; cb.dispatchEvent(new Event("change")); }
}));
jModo();

/* enlace directo: #jugar, #jugar&genero=ID o #jugar&artista=Nombre */
(async () => {
  if (!location.hash.startsWith("#jugar")) return;
  const p = new URLSearchParams(location.hash.slice(1).replace(/^jugar&?/, ""));
  document.querySelector('nav button[data-view="jugar"]').click();
  await jEntrar();
  if (p.get("genero")) { $("#j-modo").value = "genero"; jModo(); $("#j-genero").value = p.get("genero"); jDame(); }
  else if (p.get("artista")) { $("#j-modo").value = "artista"; jModo(); $("#j-artista").value = p.get("artista"); jDame(); }
})();
