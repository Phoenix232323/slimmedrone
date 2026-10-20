// Gedeelde hulpfuncties voor alle pagina's.

const $ = (selector) => document.querySelector(selector);

// Elke API-aanroep stuurt deze header mee. Andere websites kunnen dat niet,
// zo voorkomt de server dat een vreemde site namens jou iets doet (CSRF).
const API_HEADERS = { "X-SlimmeDrone": "1" };

async function api(path, { method = "GET", json, form, signal } = {}) {
  const options = { method, headers: { ...API_HEADERS }, signal };
  if (json !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(json);
  } else if (form) {
    options.body = form;
  }
  const res = await fetch(path, options);
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Niet ingelogd");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.fout || `Fout ${res.status}`);
    err.status = res.status;  // zodat je bijv. 409 ("nog bezig") of 404 ("bestaat niet") apart kunt afhandelen
    throw err;
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

// Een pictogram uit templates/_icons.html, bijv. icon("mic").
function icon(name, extraClass = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", `i ${extraClass}`.trim());
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function formatTime(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString("nl-NL");
}

// "8 okt. 2026, 10:04" (vandaag: "vandaag 10:04", gisteren: "gisteren 10:04").
function formatDatum(seconds) {
  const d = new Date(seconds * 1000);
  const tijd = d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
  const nu = new Date();
  const dagen = Math.round((new Date(nu.toDateString()) - new Date(d.toDateString())) / 86400000);
  if (dagen === 0) return `vandaag ${tijd}`;
  if (dagen === 1) return `gisteren ${tijd}`;
  return `${d.toLocaleDateString("nl-NL", { day: "numeric", month: "short", year: "numeric" })}, ${tijd}`;
}

// 123456 -> "121 kB", 12345678 -> "11,8 MB".
function formatGrootte(bytes) {
  if (typeof bytes !== "number" || !isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toLocaleString("nl-NL", { maximumFractionDigits: 1 })} MB`;
  return `${(bytes / 1024 ** 3).toLocaleString("nl-NL", { maximumFractionDigits: 1 })} GB`;
}

// 75 -> "1:15", 3725 -> "1:02:05".
function formatDuur(seconden) {
  const s = Math.max(0, Math.floor(seconden || 0));
  const uur = Math.floor(s / 3600), min = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return uur ? `${uur}:${String(min).padStart(2, "0")}:${sec}` : `${min}:${sec}`;
}

// Kleine instellingen die alleen in deze browser bewaard worden (localStorage).
// In een privévenster kan dat mislukken; dan gebruiken we gewoon de standaardwaarde.
function bewaard(key, standaard) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? standaard : JSON.parse(value);
  } catch {
    return standaard;
  }
}

function bewaar(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

// Onderdelen van de pagina sturen elkaar "seintjes", bijv. als er een nieuwe status is.
const seintjes = new EventTarget();

// Is iemand nu met een lijst knoppen bezig? Dan verversen we die lijst even niet,
// anders verdwijnt de knop net onder je klik. Dat is zo als de muis erboven hangt,
// of als je er met het toetsenbord (Tab) op staat. Na een muisklik of tik houdt een knop
// ook "focus", maar dan is :focus-visible niet waar en mag de lijst gewoon verversen.
// Op een telefoon blijft :hover na een tik "plakken"; daarom tellen we :hover alleen
// mee bij een echte muis.
const echteMuis = window.matchMedia("(hover: hover)");

function inGebruik(box) {
  if (echteMuis.matches && box.matches(":hover")) return true;
  const focus = document.activeElement;
  if (!focus || !box.contains(focus)) return false;
  try {
    return focus.matches(":focus-visible");
  } catch {
    return false;  // heel oude browser zonder :focus-visible
  }
}

// -- livestream -----------------------------------------------------------

// De livestream (MJPEG) opnieuw starten als de verbinding wegvalt.
// Staat de stream bewust op pauze (geen src), dan doen we niets.
function keepStreamAlive(img) {
  img.addEventListener("error", () => {
    setTimeout(() => {
      if (img.getAttribute("src")) img.src = `/video_feed?t=${Date.now()}`;
    }, 2000);
  });
}

document.querySelectorAll('img[src^="/video_feed"]').forEach(keepStreamAlive);

// -- welke onderdelen heeft de server? -------------------------------------
// Nieuwere versies van de server hebben extra onderdelen (instellingen, streaming chat,
// activiteit, opnemen, media, statistieken, ...). We kijken in /api/status wat er is,
// zodat de website ook met een oudere server werkt: wat ontbreekt verbergen we gewoon.

const functies = bewaardeFuncties();

// Staat de statistiek op de server uit (STATISTIEK=uit in .env)? Dat merkt pas de pagina
// Statistieken (statistieken.js). Die onthoudt het hier, zodat de link verdwijnt.
function statistiekUit(uit) {
  try {
    if (uit !== undefined) sessionStorage.setItem("statistiek-uit", uit ? "1" : "");
    return sessionStorage.getItem("statistiek-uit") === "1";
  } catch {
    return false;
  }
}

function bewaardeFuncties() {
  try {
    return { bekend: false, ...JSON.parse(sessionStorage.getItem("functies") || "{}") };
  } catch {
    return { bekend: false };
  }
}

function toonFuncties() {
  document.querySelectorAll("[data-functie]").forEach((node) => {
    // Op de pagina zelf (aria-current) blijft de link altijd zichtbaar.
    if (node.getAttribute("aria-current") === "page") return;
    node.hidden = !functies[node.dataset.functie];
  });
  const aiLink = $("#roAi");
  if (aiLink) {
    if (functies.instellingen) aiLink.setAttribute("href", "/instellingen");
    else aiLink.removeAttribute("href");
  }
}

// Verwerk een antwoord van /api/status: functies bepalen en de bovenbalk bijwerken.
function leesStatus(s) {
  // Opnemen, foto's op de server, media en statistieken komen samen in één serverversie.
  // Het veld "opname" in de status zegt dat die versie er is.
  const fase2 = !!s.opname && typeof s.opname === "object";
  const nieuw = {
    bekend: true,
    instellingen: "ai_info" in s,
    stream: "ai_info" in s,
    activiteit: Array.isArray(s.wachters),
    weergave: !!s.weergave && typeof s.weergave === "object",
    systeem: !!s.systeem,
    prestaties: !!s.prestaties,
    opname: fase2,
    fotos: fase2,
    media: fase2,
    statistieken: fase2 && !statistiekUit(),
  };
  Object.assign(functies, nieuw);
  try { sessionStorage.setItem("functies", JSON.stringify(nieuw)); } catch {}
  toonFuncties();
  toonKopStatus(s);
}

function toonKopStatus(s) {
  const cam = $("#roCamera");
  if (cam && s.camera) {
    cam.classList.toggle("ok", !!s.camera.verbonden);
    cam.classList.toggle("bad", !s.camera.verbonden);
    cam.querySelector("b").textContent = s.camera.verbonden ? "Online" : "Offline";
    cam.title = s.camera.verbonden ? `Camera: ${s.camera.bron}` : `Camera: ${s.camera.fout || "geen verbinding"}`;
  }
  const ai = $("#roAi");
  if (ai && s.ai) {
    const claude = s.ai === "claude";
    ai.classList.toggle("ok", claude);
    ai.classList.toggle("warn", !claude);
    ai.querySelector("b").textContent = claude ? "Claude" : "Lokaal";
    const model = s.ai_info && s.ai_info.model ? ` (${s.ai_info.model})` : "";
    ai.title = claude ? `J.A.R.V.I.S. gebruikt Claude${model}` : "J.A.R.V.I.S. draait in de lokale modus (zonder Claude)";
  }
}

function geenVerbinding() {
  const cam = $("#roCamera");
  if (!cam) return;
  cam.classList.remove("ok");
  cam.classList.add("bad");
  cam.querySelector("b").textContent = "Geen verbinding";
  cam.title = "De website kan de server niet bereiken";
}

toonFuncties();

// -- korte meldingen linksonder (toasts) ---------------------------------------

const kleinScherm = window.matchMedia("(max-width: 720px)");

// actie = { tekst: "Bekijk in Media", href: "/media" }: een link in de melding.
function toast(tekst, { soort = "info", foto = null, duur = 6000, actie = null } = {}) {
  const box = $("#toasts");
  if (!box) return;
  const pictogram = { onbekend: "alert", wacht: "eye", fout: "alert", bekend: "face", ok: "check" }[soort] || "info";
  const node = el("div", { class: `toast ${soort}` },
    foto ? el("img", { src: foto, alt: "" }) : icon(pictogram), el("span", {}, tekst),
    actie ? el("a", { class: "toast-action", href: actie.href }, actie.tekst) : null);
  // Op een telefoon is er weinig ruimte: één melding tegelijk, en korter. Anders hoogstens drie.
  if (kleinScherm.matches) {
    box.replaceChildren();
    duur = Math.min(duur, actie ? 6000 : 4000);
  }
  while (box.children.length >= 3) box.firstChild.remove();
  box.append(node);
  setTimeout(() => node.remove(), duur);
}

// -- vensters ------------------------------------------------------------------

function sluitKnop(dialog) {
  return el("button", { class: "btn icon ghost", type: "button", "aria-label": "Sluiten", title: "Sluiten (Esc)",
    onclick: () => dialog.close() }, icon("x"));
}

function venster(id, titel, ...inhoud) {
  let dialog = document.getElementById(id);
  if (!dialog) {
    dialog = el("dialog", { class: "dialog", id, "aria-label": titel });
    // Klik naast het venster (op de donkere achtergrond) = sluiten.
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    document.body.append(dialog);
  }
  dialog.replaceChildren(el("div", { class: "dialog-head" }, el("h2", {}, titel), sluitKnop(dialog)), ...inhoud);
  dialog.showModal();
  return dialog;
}

// Een foto groot bekijken (bijv. van een melding of uit de chat).
function toonFoto(src, titel = "Foto") {
  venster("fotoVenster", titel, el("img", { src, alt: titel }));
}

// Vragen of iets echt moet (bijv. verwijderen). Geeft een Promise met true of false.
// Netter dan confirm() van de browser, en met de knop "Annuleren" als standaard (veilig bij Enter).
function bevestig({ titel, tekst, knop = "Verwijderen", gevaarlijk = true }) {
  return new Promise((klaar) => {
    const dialog = el("dialog", { class: "dialog confirm", role: "alertdialog", "aria-labelledby": "bevestigTitel",
      "aria-describedby": "bevestigTekst" });
    const nee = el("button", { class: "btn", type: "button", onclick: () => dialog.close("nee") }, "Annuleren");
    const ja = el("button", { class: `btn primary${gevaarlijk ? " danger" : ""}`, type: "button",
      onclick: () => dialog.close("ja") }, knop);
    dialog.append(
      el("div", { class: "dialog-head" }, el("h2", { id: "bevestigTitel" }, titel), sluitKnop(dialog)),
      el("p", { class: "dialog-body", id: "bevestigTekst" }, tekst),
      el("div", { class: "dialog-foot" }, nee, ja));
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close("nee"); });
    dialog.addEventListener("close", () => {
      klaar(dialog.returnValue === "ja");
      dialog.remove();
    });
    document.body.append(dialog);
    dialog.showModal();
    nee.focus();
  });
}

// Tekst naar het klembord kopiëren (bijv. een antwoord van J.A.R.V.I.S.). Geeft true als het lukte.
async function kopieer(tekst) {
  // navigator.clipboard werkt alleen via https of localhost; anders de oude manier.
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(tekst);
      return true;
    }
  } catch {}
  const area = el("textarea", { style: "position:fixed;opacity:0", "aria-hidden": "true" });
  area.value = tekst;
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  area.remove();
  return ok;
}

// -- menu "Meer" (telefoon) --------------------------------------------------------

const meerMenu = $("#meerMenu");
if (meerMenu) {
  $("#meerBtn").addEventListener("click", () => meerMenu.showModal());
  meerMenu.addEventListener("click", (event) => {
    if (event.target === meerMenu || event.target.closest("[data-sluit]")) meerMenu.close();
  });
}

// -- browsermeldingen (Notification API) ---------------------------------------------
// Een melding van het besturingssysteem als het tabblad niet zichtbaar is. Werkt alleen
// via https of op localhost, en alleen als je het zelf aanzet (Instellingen).

function browserMeldingUitleg() {
  if (!("Notification" in window)) return "Deze browser kan geen meldingen tonen.";
  if (!window.isSecureContext) {
    return "Browsermeldingen werken alleen via https of op de Pi zelf (http://localhost). Zet HTTPS=aan in het " +
      "bestand .env, start de app opnieuw en open de website via https://.";
  }
  if (Notification.permission === "denied") {
    return "Je hebt meldingen voor deze website geblokkeerd. Klik op het slotje naast het adres om ze weer toe te staan.";
  }
  return "";
}

function browserMeldingenAan() {
  return !!bewaard("browsermeldingen", false) && !browserMeldingUitleg() && Notification.permission === "granted";
}

function browserMelding(alert) {
  if (!document.hidden || !browserMeldingenAan()) return;
  const titel = { onbekend: "Onbekend gezicht", bekend: "Bekend gezicht", wacht: "Wachter", systeem: "Systeem" }[alert.soort]
    || "SlimmeDrone";
  try {
    const n = new Notification(`SlimmeDrone · ${titel}`, {
      body: alert.bericht, tag: `slimmedrone-${alert.id}`, icon: "/static/icon.svg",
    });
    n.onclick = () => { window.focus(); n.close(); };
  } catch {
    // Sommige telefoons willen meldingen alleen via een "service worker"; dan slaan we hem over.
  }
}

// -- sneltoetsen ------------------------------------------------------------------

const sneltoetsen = [];

function sneltoets(toets, uitleg, actie) {
  sneltoetsen.push({ toets, uitleg, actie });
}

function toonSneltoetsen() {
  const lijst = el("dl", { class: "keys" });
  for (const s of [...sneltoetsen, { toets: "?", uitleg: "Deze hulp tonen" }, { toets: "Esc", uitleg: "Venster sluiten / typen stoppen" }]) {
    lijst.append(el("dt", {}, el("kbd", {}, s.toets)), el("dd", {}, s.uitleg));
  }
  venster("hulpVenster", "Sneltoetsen", lijst);
}

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
  const target = event.target;
  if (target.closest && target.closest("input, textarea, select, [contenteditable]")) {
    if (event.key === "Escape") target.blur();
    return;
  }
  if (document.querySelector("dialog[open]")) return;  // een venster is open
  // Toets ingedrukt houden herhaalt hem. Dat negeren we, anders maakt "s" vasthouden
  // tientallen foto's en gaat een opname met "r" steeds aan en uit.
  if (event.repeat) {
    if (event.key === "?" || sneltoetsen.some((x) => x.toets === event.key.toLowerCase())) event.preventDefault();
    return;
  }
  if (event.key === "?") {
    event.preventDefault();
    toonSneltoetsen();
    return;
  }
  const s = sneltoetsen.find((x) => x.toets === event.key.toLowerCase());
  if (s) {
    event.preventDefault();
    s.actie();
  }
});

document.querySelectorAll("[data-hulp]").forEach((btn) => btn.addEventListener("click", toonSneltoetsen));
