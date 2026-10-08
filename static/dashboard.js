// Dashboard: status bijwerken (elke seconde), HUD over het beeld, telemetrie,
// zoomen, panelen (objecten, gezichten, meldingen, activiteit, wachters) en de
// tabbladen onderaan op de telefoon. De chat met J.A.R.V.I.S. staat in jarvis.js.

const dash = $("#inhoud");
const video = $("#video");
const videoWrap = $("#videoWrap");
const hud = $("#hud");
const mobiel = window.matchMedia("(max-width: 720px)");

let lastAlertId = 0;
let firstStatus = true;

// -- status (elke seconde) -------------------------------------------------

async function refreshStatus() {
  try {
    const s = await api(`/api/status?na=${lastAlertId}`);
    leesStatus(s);  // app.js: welke onderdelen heeft de server + bovenbalk
    renderStatus(s);
    seintjes.dispatchEvent(new CustomEvent("status", { detail: s }));
  } catch (err) {
    geenVerbinding();
    setLive(false, "GEEN VERBINDING");
  }
  // Staat het tabblad op de achtergrond? Dan rustiger aan, dat spaart de Pi.
  setTimeout(refreshStatus, document.hidden ? 5000 : 1000);
}

function renderStatus(s) {
  const p = s.prestaties || null;
  const beeldFps = p ? p.beeld_fps : s.fps;
  const unknown = s.gezichten.filter((f) => !f.naam).length;

  // HUD over het beeld
  setLive(s.camera.verbonden, s.camera.verbonden ? "LIVE" : "GEEN SIGNAAL");
  $("#hudFps").textContent = fmt(beeldFps);
  $("#hudZoom").textContent = s.zoom.actief ? `${fmt(s.zoom.factor)}×` : "1.0×";
  $("#hudObj").textContent = s.objecten.length;
  $("#hudFaces").textContent = s.gezichten.length;
  $("#hudUnknown").textContent = unknown;
  $("#hudUnknown").parentElement.classList.toggle("zero", unknown === 0);
  // Tekent de server zelf al de tijd in het beeld? Dan laten wij onze klok weg.
  $("#hudClock").hidden = !s.weergave || s.weergave.hud !== false;
  fitHud();

  renderTelemetry(s, beeldFps);
  renderObjects(s);
  renderFaces(s);
  renderZoom(s.zoom);
  renderWeergave(s.weergave);
  if (Array.isArray(s.wachters)) renderWatchers(s.wachters);

  for (const alert of s.meldingen) {
    addAlert(alert, !firstStatus);
    lastAlertId = Math.max(lastAlertId, alert.id);
  }
  if (firstStatus) {
    firstStatus = false;
    if (functies.activiteit) refreshActivity();
  }
}

// Getal met één decimaal, of "--" als het er niet is.
function fmt(value) {
  return typeof value === "number" && isFinite(value) ? value.toFixed(1) : "--";
}

// -- HUD over het beeld ----------------------------------------------------------

function setLive(ok, tekst) {
  const live = $("#hudLive");
  live.classList.toggle("lost", !ok);
  live.querySelector("b").textContent = tekst;
}

// Het beeld staat in "object-fit: contain": er kunnen zwarte randen zijn.
// Reken uit waar het beeld echt staat (in pixels binnen videoWrap).
function beeldVlak() {
  const box = videoWrap.getBoundingClientRect();
  const natW = video.naturalWidth || 16, natH = video.naturalHeight || 9;
  const scale = Math.min(box.width / natW, box.height / natH);
  const width = natW * scale, height = natH * scale;
  return { box, left: (box.width - width) / 2, top: (box.height - height) / 2, width, height };
}

// Leg de HUD precies over het beeld (niet over de zwarte randen).
function fitHud() {
  const v = beeldVlak();
  if (!v.width) return;
  hud.style.left = `${v.left}px`;
  hud.style.top = `${v.top}px`;
  hud.style.width = `${v.width}px`;
  hud.style.height = `${v.height}px`;
}

if (window.ResizeObserver) new ResizeObserver(fitHud).observe(videoWrap);
video.addEventListener("load", fitHud);

// De klok in de HUD.
function tick() {
  $("#hudClock").textContent = new Date().toLocaleTimeString("nl-NL");
}
tick();
setInterval(tick, 1000);

// -- telemetrie ------------------------------------------------------------------

const fpsHistory = [];

function renderTelemetry(s, beeldFps) {
  const p = s.prestaties || {}, sys = s.systeem || {};
  setTele("beeld", beeldFps, "tBeeld", { warn: (v) => v < 12, bad: (v) => v < 5 });
  setTele("camera", p.camera_fps, "tCamera");
  setTele("objecten", p.objecten_fps, "tObjecten");
  setTele("temp", sys.cpu_temp, "tTemp", { warn: (v) => v >= 70, bad: (v) => v >= 80, meter: "mTemp", schaal: (v) => (v - 30) / 55 });
  setTele("cpu", sys.cpu_belasting, "tCpu", { warn: (v) => v >= 85, meter: "mCpu", schaal: (v) => v / 100, heel: true });
  setTele("geheugen", sys.geheugen, "tMem", { warn: (v) => v >= 85, bad: (v) => v >= 95, meter: "mMem", schaal: (v) => v / 100, heel: true });

  if (typeof beeldFps === "number") {
    fpsHistory.push(beeldFps);
    if (fpsHistory.length > 60) fpsHistory.shift();
  }
  drawSparkline($("#fpsChart"), fpsHistory);
}

// Eén vakje van de telemetrie. Ontbreekt de waarde (oudere server)? Dan verbergen.
function setTele(naam, value, id, opties = {}) {
  const cell = document.querySelector(`[data-tele="${naam}"]`);
  const heeft = typeof value === "number" && isFinite(value);
  cell.hidden = !heeft;
  if (!heeft) return;
  $(`#${id}`).textContent = opties.heel ? Math.round(value) : value.toFixed(1);
  const bad = opties.bad ? opties.bad(value) : false;
  cell.classList.toggle("bad", bad);
  cell.classList.toggle("warn", !bad && (opties.warn ? opties.warn(value) : false));
  if (opties.meter) {
    const deel = Math.min(1, Math.max(0, opties.schaal(value)));
    $(`#${opties.meter}`).style.transform = `scaleX(${deel.toFixed(3)})`;
  }
}

// Kleine lijngrafiek van de fps (één keer per seconde opnieuw getekend).
function drawSparkline(canvas, values) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr);
  if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const max = Math.max(31, ...values);
  const y = (v) => h - 2 - (v / max) * (h - 4);
  // Stippellijn op 30 fps
  ctx.strokeStyle = "rgba(143, 163, 188, .3)";
  ctx.setLineDash([2, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(y(30)) + .5);
  ctx.lineTo(w, Math.round(y(30)) + .5);
  ctx.stroke();
  ctx.setLineDash([]);
  if (values.length < 2) return;
  // Eerst vullen de punten de breedte (minstens 12 plaatsen), daarna schuift de lijn mee.
  const step = w / (Math.max(values.length, 12) - 1);
  const x0 = w - (values.length - 1) * step;
  ctx.beginPath();
  values.forEach((v, i) => (i ? ctx.lineTo(x0 + i * step, y(v)) : ctx.moveTo(x0, y(v))));
  ctx.strokeStyle = "#22d3ee";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.lineTo(w, h);
  ctx.lineTo(x0, h);
  ctx.closePath();
  ctx.fillStyle = "rgba(34, 211, 238, .1)";
  ctx.fill();
}

// -- objecten en gezichten ---------------------------------------------------------

function renderObjects(s) {
  // Klik op een object om in te zoomen. Niet verversen terwijl de muis erboven
  // hangt, anders verdwijnt de knop net onder je klik.
  const list = $("#objects");
  if (!list.matches(":hover") && !list.contains(document.activeElement)) {
    list.replaceChildren(...s.objecten.map((o) => el("li", {},
      el("button", {
        type: "button",
        title: `Zoom in op ${o.naam}`,
        onclick: () => zoom({ actie: "object", label: o.label, naam: o.naam, kader: o.kader }),
      },
      el("span", {}, o.naam),
      el("small", {}, `${Math.round(o.zekerheid * 100)}%`),
      el("span", { class: "bar" }, el("i", { style: `transform: scaleX(${o.zekerheid})` }))),
    )));
  }
  $("#objectCount").textContent = s.objecten.length;
  $("#objectsEmpty").hidden = s.objecten.length > 0;
  $("#detectorInfo").textContent = `Herkenning: ${s.objectherkenning}`;
}

function renderFaces(s) {
  $("#faces").replaceChildren(...s.gezichten.map((f) => el("li", { class: f.naam ? "" : "unknown" },
    f.naam || "Onbekend", el("small", {}, f.overeenkomst.toFixed(2)))));
  $("#faceCount").textContent = s.gezichten.length;
  $("#facesEmpty").hidden = s.gezichten.length > 0;
}

// -- zoomen ----------------------------------------------------------------------

async function zoom(body) {
  try {
    await api("/api/zoom", { method: "POST", json: body });
  } catch (err) {
    toast(`Zoomen lukte niet: ${err.message}`, { soort: "fout" });
  }
}

function renderZoom(z) {
  const state = $("#zoomState");
  const tekst = z.actief ? `Zoom ${fmt(z.factor)}× · ${z.naam}` : "Volledig beeld";
  if (state.textContent !== tekst) state.textContent = tekst;
  state.classList.toggle("active", z.actief);
  $("#zoomOut").disabled = !z.actief;
}

$("#zoomOut").addEventListener("click", () => zoom({ actie: "uit" }));
$("#zoomIn").addEventListener("click", () => zoom({ actie: "punt", x: 0.5, y: 0.5 }));

// Klik in het beeld: zoom 2x in rond dat punt. De HUD laat klikken door,
// en we houden rekening met de zwarte randen van "object-fit: contain".
video.addEventListener("click", (event) => {
  const v = beeldVlak();
  const px = event.clientX - v.box.left, py = event.clientY - v.box.top;
  const x = (px - v.left) / v.width;
  const y = (py - v.top) / v.height;
  if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) return;
  const ping = el("i", { class: "zoom-ping", style: `left:${px}px;top:${py}px` });
  videoWrap.append(ping);
  setTimeout(() => ping.remove(), 600);
  zoom({ actie: "punt", x: Number(x.toFixed(4)), y: Number(y.toFixed(4)) });
});

// -- knoppen onder het beeld ---------------------------------------------------------

const fullscreenBtn = $("#fullscreenBtn");
fullscreenBtn.hidden = !document.fullscreenEnabled;
function volledigScherm() {
  if (document.fullscreenElement) document.exitFullscreen();
  else videoWrap.requestFullscreen?.();
}
fullscreenBtn.addEventListener("click", volledigScherm);

// Kaders en tekst in het camerabeeld: die tekent de server (geldt voor iedereen).
let weergaveBezig = false;

function renderWeergave(w) {
  if (!w || weergaveBezig) return;
  $("#toggleKaders").setAttribute("aria-pressed", String(!!w.kaders));
  $("#toggleTekst").setAttribute("aria-pressed", String(!!w.hud));
}

async function zetWeergave(naam, knop) {
  const aan = knop.getAttribute("aria-pressed") !== "true";
  weergaveBezig = true;
  knop.setAttribute("aria-pressed", String(aan));
  try {
    renderWeergave(await api("/api/weergave", { method: "POST", json: { [naam]: aan } }));
  } catch (err) {
    knop.setAttribute("aria-pressed", String(!aan));
    toast(`Dat lukte niet: ${err.message}`, { soort: "fout" });
  } finally {
    weergaveBezig = false;
  }
}

$("#toggleKaders").addEventListener("click", (e) => zetWeergave("kaders", e.currentTarget));
$("#toggleTekst").addEventListener("click", (e) => zetWeergave("hud", e.currentTarget));

// De HUD-overlay zelf staat in de browser aan/uit (alleen voor jou).
function zetOverlay(aan) {
  hud.classList.toggle("off", !aan);
  $("#toggleOverlay").setAttribute("aria-pressed", String(aan));
}
zetOverlay(bewaard("hud-overlay", true));
$("#toggleOverlay").addEventListener("click", () => {
  const aan = hud.classList.contains("off");
  bewaar("hud-overlay", aan);
  zetOverlay(aan);
});

// -- meldingen ----------------------------------------------------------------------

const meldingen = [];  // nieuwste eerst, hoogstens 50
let filter = bewaard("meldingen-filter", "alle");
let ongelezen = 0;

function past(alert) {
  return filter === "alle" || alert.soort === filter;
}

function alertItem(a) {
  const symbool = { wacht: "eye", systeem: "info" }[a.soort] || "face";
  const foto = a.foto
    ? el("button", { class: "thumb", type: "button", "aria-label": `Foto bij "${a.bericht}" vergroten`,
      onclick: () => toonFoto(a.foto, a.bericht) }, el("img", { src: a.foto, alt: "" }))
    : el("span", { class: "thumb" }, icon(symbool));
  return el("li", { class: a.soort }, foto,
    el("div", { class: "text" }, el("b", {}, a.bericht),
      el("time", { datetime: new Date(a.tijd * 1000).toISOString() }, formatTime(a.tijd))));
}

function renderAlerts() {
  const zichtbaar = meldingen.filter(past);
  $("#alerts").replaceChildren(...zichtbaar.map(alertItem));
  $("#alertCount").textContent = meldingen.length;
  const empty = $("#alertsEmpty");
  empty.hidden = zichtbaar.length > 0;
  empty.textContent = meldingen.length ? "Geen meldingen in deze categorie." : "Nog geen meldingen.";
}

function addAlert(alert, nieuw) {
  meldingen.unshift(alert);
  if (meldingen.length > 50) meldingen.pop();
  if (past(alert)) {
    $("#alerts").prepend(alertItem(alert));
    while ($("#alerts").children.length > 50) $("#alerts").lastChild.remove();
  }
  $("#alertCount").textContent = meldingen.length;
  $("#alertsEmpty").hidden = $("#alerts").children.length > 0;
  if (!nieuw) return;
  // Kijk je op de telefoon al naar de meldingen? Dan geen extra melding bovenin.
  if (!(mobiel.matches && dash.dataset.view === "meldingen")) toast(alert.bericht, { soort: alert.soort, foto: alert.foto });
  if (spraak.piep) piep(alert.soort);
  if (spraak.meldingen) zeg(alert.bericht);
  if (mobiel.matches && dash.dataset.view !== "meldingen") {
    ongelezen++;
    zetBadge("#badgeMeldingen", ongelezen);
  }
}

function zetFilter(nieuw) {
  filter = nieuw;
  bewaar("meldingen-filter", nieuw);
  document.querySelectorAll("#alertFilter button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.filter === nieuw)));
  renderAlerts();
}

$("#alertFilter").addEventListener("click", (event) => {
  const knop = event.target.closest("button");
  if (knop) zetFilter(knop.dataset.filter);
});
zetFilter(["alle", "bekend", "onbekend", "wacht"].includes(filter) ? filter : "alle");

function zetBadge(selector, aantal) {
  const badge = $(selector);
  badge.hidden = !aantal;
  badge.textContent = aantal > 9 ? "9+" : String(aantal);
}

// -- activiteit (wat verscheen of verdween) ------------------------------------------

let lastActivityId = 0;

async function refreshActivity() {
  try {
    const { gebeurtenissen } = await api(`/api/activiteit?na=${lastActivityId}`);
    const list = $("#activity");
    for (const g of gebeurtenissen) {
      lastActivityId = Math.max(lastActivityId, g.id);
      const wat = g.aantal > 1 ? `${g.aantal}× ${g.naam}` : g.naam;
      list.prepend(el("li", { class: g.soort },
        icon(g.soort === "verschenen" ? "plus" : "minus"),
        el("span", {}, `${wat} ${g.soort}`),
        el("time", {}, formatTime(g.tijd))));
    }
    while (list.children.length > 50) list.lastChild.remove();
    $("#activityEmpty").hidden = list.children.length > 0;
  } catch (err) {
    if (err.status === 404) return;  // oudere server: geen activiteit
  }
  setTimeout(refreshActivity, document.hidden ? 10000 : 3000);
}

// -- wachters ("waarschuw me als er een hond komt") -------------------------------------

let watcherKey = "";

function renderWatchers(wachters) {
  const list = $("#watchers");
  const key = wachters.map((w) => w.id).join(",");
  // Zelfde wachters en je zit er met de muis of toetsenbord op? Dan niet verversen.
  if (key === watcherKey && (list.matches(":hover") || list.contains(document.activeElement))) return;
  watcherKey = key;
  list.replaceChildren(...wachters.map((w) => el("li", {},
    icon("eye"),
    el("span", { class: "what" }, w.omschrijving, el("small", {}, nogTijd(w.tot))),
    el("button", {
      class: "icon-btn small", type: "button", title: "Wachter stoppen",
      "aria-label": `Wachter "${w.omschrijving}" stoppen`, onclick: () => stopWatcher(w),
    }, icon("x")),
  )));
  $("#watcherCount").textContent = wachters.length;
  $("#watchersEmpty").hidden = wachters.length > 0;
}

function nogTijd(tot) {
  if (!tot) return "tot je hem stopt";
  const minuten = Math.max(0, Math.round((tot - Date.now() / 1000) / 60));
  if (minuten < 1) return "nog minder dan een minuut";
  if (minuten < 120) return `nog ${minuten} min`;
  return `nog ${Math.round(minuten / 60)} uur`;
}

async function stopWatcher(w) {
  try {
    await api(`/api/wachters/${encodeURIComponent(w.id)}`, { method: "DELETE" });
    toast(`Wachter "${w.omschrijving}" gestopt.`);
    watcherKey = "";
    renderWatchers((await api("/api/wachters")).wachters);
  } catch (err) {
    toast(`Stoppen lukte niet: ${err.message}`, { soort: "fout" });
  }
}

// -- tabbladen op de telefoon ----------------------------------------------------------

const VIEWS = ["live", "jarvis", "meldingen", "meer"];

function toonView(view) {
  if (!VIEWS.includes(view)) view = "live";
  dash.dataset.view = view;
  document.querySelectorAll("#tabbar a").forEach((a) => {
    if (a.dataset.view === view) a.setAttribute("aria-current", "true");
    else a.removeAttribute("aria-current");
  });
  if (view === "meldingen") {
    ongelezen = 0;
    zetBadge("#badgeMeldingen", 0);
  }
  if (view === "jarvis") zetBadge("#badgeJarvis", 0);
  history.replaceState(null, "", view === "live" ? location.pathname : `#${view}`);
  streamBijwerken();
  fitHud();
  seintjes.dispatchEvent(new CustomEvent("view", { detail: view }));
}

// Is dit onderdeel nu te zien? (op een groot scherm is alles te zien)
function zichtbaar(view) {
  return !mobiel.matches || dash.dataset.view === view;
}

$("#tabbar").addEventListener("click", (event) => {
  const link = event.target.closest("a[data-view]");
  if (!link) return;
  event.preventDefault();  // geen nieuwe pagina laden, alleen wisselen
  toonView(link.dataset.view);
  window.scrollTo(0, 0);
});

// -- livestream pauzeren als je hem niet ziet (spaart de Pi en je wifi) -----------------

function streamBijwerken() {
  const nodig = !document.hidden && zichtbaar("live");
  const aan = !!video.getAttribute("src");
  if (nodig && !aan) video.src = `/video_feed?t=${Date.now()}`;
  if (!nodig && aan) video.removeAttribute("src");
}

document.addEventListener("visibilitychange", streamBijwerken);
mobiel.addEventListener?.("change", () => { streamBijwerken(); fitHud(); });

// -- sneltoetsen -----------------------------------------------------------------------

sneltoets("f", "Camerabeeld volledig scherm", volledigScherm);
sneltoets("s", "Foto opslaan", () => $("#snapshotBtn").click());
sneltoets("z", "Zoom uit (volledig beeld)", () => zoom({ actie: "uit" }));

toonView(location.hash.slice(1) || "live");
refreshStatus();
