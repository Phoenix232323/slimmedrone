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
    err.status = res.status;  // zodat je bijv. 409 ("nog bezig") apart kunt afhandelen
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
// activiteit, ...). We kijken in /api/status wat er is, zodat de website ook met een
// oudere server werkt: wat ontbreekt verbergen we gewoon.

const functies = bewaardeFuncties();

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
  const nieuw = {
    bekend: true,
    instellingen: "ai_info" in s,
    stream: "ai_info" in s,
    activiteit: Array.isArray(s.wachters),
    weergave: !!s.weergave && typeof s.weergave === "object",
    systeem: !!s.systeem,
    prestaties: !!s.prestaties,
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
    cam.querySelector("b").textContent = s.camera.verbonden ? "Online" : "Los";
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

// -- meldingen rechtsboven/onder (toasts) -------------------------------------

const kleinScherm = window.matchMedia("(max-width: 720px)");

function toast(tekst, { soort = "info", foto = null, duur = 6000 } = {}) {
  const box = $("#toasts");
  if (!box) return;
  const pictogram = { onbekend: "alert", wacht: "eye", fout: "alert", bekend: "face" }[soort] || "info";
  const node = el("div", { class: `toast ${soort}` },
    foto ? el("img", { src: foto, alt: "" }) : icon(pictogram), el("span", {}, tekst));
  // Op een telefoon is er weinig ruimte: één melding tegelijk, en korter. Anders hoogstens drie.
  if (kleinScherm.matches) {
    box.replaceChildren();
    duur = Math.min(duur, 4000);
  }
  while (box.children.length >= 3) box.firstChild.remove();
  box.append(node);
  setTimeout(() => node.remove(), duur);
}

// -- vensters ------------------------------------------------------------------

function venster(id, titel, ...inhoud) {
  let dialog = document.getElementById(id);
  if (!dialog) {
    dialog = el("dialog", { class: "dialog", id, "aria-label": titel });
    // Klik naast het venster (op de donkere achtergrond) = sluiten.
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    document.body.append(dialog);
  }
  dialog.replaceChildren(
    el("div", { class: "dialog-head" }, el("h2", {}, titel),
      el("button", { class: "icon-btn", type: "button", "aria-label": "Sluiten", title: "Sluiten (Esc)",
        onclick: () => dialog.close() }, icon("x"))),
    ...inhoud);
  dialog.showModal();
  return dialog;
}

// Een foto groot bekijken (bijv. van een melding of uit de chat).
function toonFoto(src, titel = "Foto") {
  venster("fotoVenster", titel, el("img", { src, alt: titel }));
}

// -- kopiëren -------------------------------------------------------------------

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
