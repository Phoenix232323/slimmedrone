// Mediapagina: foto's en opnames die op de Raspberry Pi bewaard zijn. Bekijken
// (foto's groot, opnames afspelen), downloaden en verwijderen.
//
// Opnames speelt de server af als MJPEG-stroom (net als het livebeeld), zodat een
// gewone <img> ze kan tonen. Een <video> kan het .avi-bestand namelijk niet afspelen.

const grid = $("#mediaGrid");
const lightbox = $("#lightbox");
const lbImg = $("#lbImg");

let media = { fotos: [], opnames: [] };
let soort = location.hash === "#opnames" ? "opnames" : "fotos";
let geopend = -1;        // welk item in de viewer staat (index in de lijst van deze soort)
let terugFocus = null;   // waar de focus heen gaat als de viewer dichtgaat
let afspeelTimer = null;

const TEKST = {
  fotos: { leeg: "Nog geen foto's", uitleg: "Maak een foto met de knop Foto op het Live-scherm, of vraag J.A.R.V.I.S. “Maak een foto”.",
    een: "foto", meer: "foto's", icoon: "image" },
  opnames: { leeg: "Nog geen opnames", uitleg: "Start een opname met de knop Opnemen op het Live-scherm, of vraag J.A.R.V.I.S. “Start een opname”.",
    een: "opname", meer: "opnames", icoon: "film" },
};

// -- laden ---------------------------------------------------------------------------

async function laad() {
  grid.setAttribute("aria-busy", "true");
  try {
    const data = await api("/api/media");
    media = { fotos: data.fotos || [], opnames: data.opnames || [] };
    $("#mediaError").hidden = true;
    toonVrij(data.vrij_mb);
    render();
  } catch (err) {
    grid.replaceChildren();
    $("#mediaEmpty").hidden = true;
    const box = $("#mediaError");
    box.hidden = false;
    box.replaceChildren(icon("alert"), el("span", {}, err.status === 404
      ? "Deze versie van de server bewaart nog geen foto's en opnames. Werk de software op de Raspberry Pi bij."
      : `Laden lukte niet: ${err.message}`));
  } finally {
    grid.setAttribute("aria-busy", "false");
  }
}

function toonVrij(mb) {
  const badge = $("#diskFree");
  if (typeof mb !== "number") {
    badge.hidden = true;
    return;
  }
  badge.hidden = false;
  badge.querySelector("span").textContent = `${formatGrootte(mb * 1024 * 1024)} vrij`;
  badge.classList.toggle("warn", mb < 1000);
}

// -- tonen ---------------------------------------------------------------------------

function render() {
  for (const s of ["fotos", "opnames"]) {
    $(`#count${s === "fotos" ? "Fotos" : "Opnames"}`).textContent = media[s].length;
  }
  document.querySelectorAll("#mediaTabs [role=tab]").forEach((tab) => {
    const actief = tab.dataset.soort === soort;
    tab.setAttribute("aria-selected", String(actief));
    tab.tabIndex = actief ? 0 : -1;
    if (actief) $("#mediaPanel").setAttribute("aria-labelledby", tab.id);
  });

  const lijst = media[soort];
  const t = TEKST[soort];
  const totaal = lijst.reduce((som, m) => som + (m.grootte || 0), 0);
  $("#mediaSummary").textContent = lijst.length
    ? `${lijst.length} ${lijst.length === 1 ? t.een : t.meer} · ${formatGrootte(totaal)}` : "";

  grid.replaceChildren(...lijst.map((m, i) => kaart(m, i)));
  const leeg = $("#mediaEmpty");
  leeg.hidden = lijst.length > 0;
  $("#mediaEmptyTitle").textContent = t.leeg;
  $("#mediaEmptyText").textContent = t.uitleg;
  leeg.querySelector("svg use").setAttribute("href", `#i-${t.icoon}`);
}

function kaart(m, index) {
  const video = soort === "opnames";
  const mini = el("img", { src: m.mini, alt: "", loading: "lazy", decoding: "async" });
  mini.addEventListener("error", () => mini.replaceWith(el("span", { class: "thumb-missing" }, icon(video ? "film" : "image"))));
  const wanneer = formatDatum(m.tijd);
  const details = [formatGrootte(m.grootte)];
  if (video && typeof m.duur === "number") details.unshift(formatDuur(m.duur));
  return el("li", { class: `media-card${video ? " video" : ""}` },
    el("button", {
      class: "media-thumb", type: "button",
      "aria-label": `${video ? "Opname afspelen" : "Foto bekijken"}: ${wanneer}`,
      onclick: (e) => openViewer(index, e.currentTarget),
    }, mini,
    video ? el("span", { class: "play", "aria-hidden": "true" }, icon("play")) : null,
    video && typeof m.duur === "number" ? el("span", { class: "duration", "aria-hidden": "true" }, formatDuur(m.duur)) : null),
    el("div", { class: "media-info" },
      el("div", { class: "media-text" }, el("b", { title: m.naam }, wanneer), el("small", {}, details.join(" · "))),
      el("a", { class: "btn icon ghost sm", href: downloadUrl(m), download: m.naam,
        "aria-label": `${m.naam} downloaden`, title: "Downloaden" }, icon("download")),
      el("button", { class: "btn icon ghost danger sm", type: "button", "aria-label": `${m.naam} verwijderen`,
        title: "Verwijderen", onclick: () => verwijder(m) }, icon("trash"))));
}

function downloadUrl(m) {
  return `${m.url}${m.url.includes("?") ? "&" : "?"}download=1`;
}

// -- tabbladen Foto's / Opnames (pijltjes wisselen, zoals bij gewone tabbladen) ----------

function kiesSoort(nieuw, focus = false) {
  soort = nieuw;
  history.replaceState(null, "", soort === "opnames" ? "#opnames" : location.pathname);
  render();
  if (focus) document.querySelector(`#mediaTabs [data-soort="${soort}"]`).focus();
}

$("#mediaTabs").addEventListener("click", (event) => {
  const tab = event.target.closest("[role=tab]");
  if (tab) kiesSoort(tab.dataset.soort);
});
$("#mediaTabs").addEventListener("keydown", (event) => {
  // Pijltjes: naar het andere tabblad (er zijn er maar twee). Home = eerste, End = laatste.
  const nieuw = { ArrowLeft: soort === "fotos" ? "opnames" : "fotos", ArrowRight: soort === "fotos" ? "opnames" : "fotos",
    Home: "fotos", End: "opnames" }[event.key];
  if (!nieuw) return;
  event.preventDefault();
  kiesSoort(nieuw, true);
});
$("#mediaRefresh").addEventListener("click", laad);

// -- viewer: foto groot of opname afspelen -----------------------------------------------

function openViewer(index, bron) {
  terugFocus = bron || document.activeElement;
  toon(index);
  if (!lightbox.open) lightbox.showModal();
  $("#lbClose").focus();
}

function toon(index) {
  const lijst = media[soort];
  if (!lijst.length) return lightbox.close();
  geopend = (index + lijst.length) % lijst.length;
  const m = lijst[geopend];
  const video = soort === "opnames";
  stopAfspelen();
  $("#lbTitle").textContent = `${video ? "Opname" : "Foto"} · ${formatDatum(m.tijd)}`;
  $("#lbMeta").textContent = [m.naam, formatGrootte(m.grootte), `${geopend + 1} / ${lijst.length}`].join(" · ");
  $("#lbDownload").href = downloadUrl(m);
  $("#lbDownload").setAttribute("download", m.naam);
  lbImg.alt = video ? `Opname van ${formatDatum(m.tijd)}` : `Foto van ${formatDatum(m.tijd)}`;
  lbImg.hidden = false;
  $("#lbError").hidden = true;
  const meer = lijst.length > 1;
  $("#lbPrev").hidden = !meer;
  $("#lbNext").hidden = !meer;
  $("#playerBar").hidden = !video;
  if (video) speelAf(m);
  else lbImg.src = m.url;
}

function speelAf(m) {
  stopAfspelen();
  const duur = typeof m.duur === "number" && m.duur > 0 ? m.duur : null;
  lbImg.src = `${m.afspelen}${m.afspelen.includes("?") ? "&" : "?"}t=${Date.now()}`;
  $("#playerEnd").hidden = true;
  $("#playerDur").textContent = duur ? formatDuur(duur) : "--:--";
  const begin = Date.now();
  // De stroom zegt niet waar hij is; we tellen zelf mee met de bekende duur.
  afspeelTimer = setInterval(() => {
    const verstreken = (Date.now() - begin) / 1000;
    $("#playerTime").textContent = formatDuur(duur ? Math.min(verstreken, duur) : verstreken);
    $("#playerProgress").style.transform = `scaleX(${duur ? Math.min(1, verstreken / duur).toFixed(3) : 0})`;
    if (duur && verstreken > duur + 0.8) {
      clearInterval(afspeelTimer);
      afspeelTimer = null;
      $("#playerEnd").hidden = false;
    }
  }, 250);
}

function stopAfspelen() {
  if (afspeelTimer) clearInterval(afspeelTimer);
  afspeelTimer = null;
  $("#playerProgress").style.transform = "scaleX(0)";
  $("#playerTime").textContent = "0:00";
  $("#playerEnd").hidden = true;
}

// Kan de foto of opname niet geladen worden (bestand weg of kapot)? Zeg dat, in plaats van
// een kapot plaatje en een speler die eindeloos blijft doortellen.
lbImg.addEventListener("error", () => {
  if (geopend < 0 || !lbImg.getAttribute("src")) return;  // viewer is dicht
  const video = soort === "opnames";
  stopAfspelen();
  lbImg.removeAttribute("src");
  lbImg.hidden = true;
  $("#playerBar").hidden = true;
  const fout = $("#lbError");
  fout.replaceChildren(icon("alert"), el("span", {}, video
    ? "Deze opname kan niet worden afgespeeld. Misschien is het bestand kapot of verwijderd; probeer hem te downloaden."
    : "Deze foto kan niet worden geladen. Misschien is het bestand verwijderd."));
  fout.hidden = false;
});

// De focus op een logische plek zetten als de knop die hem had verdwenen is (na verwijderen):
// de kaart op dezelfde plek (of de laatste), en als de lijst leeg is het tabblad.
function focusOpKaart(index) {
  const knoppen = grid.querySelectorAll(".media-thumb");
  const doel = knoppen[Math.min(Math.max(0, index), knoppen.length - 1)]
    || document.querySelector(`#mediaTabs [data-soort="${soort}"]`);
  doel.focus();
}

lightbox.addEventListener("close", () => {
  stopAfspelen();
  lbImg.removeAttribute("src");  // stopt ook de stroom van een opname
  const index = geopend;
  geopend = -1;
  if (terugFocus && document.contains(terugFocus)) terugFocus.focus();
  else focusOpKaart(index);
});
lightbox.addEventListener("keydown", (event) => {
  if (event.target.closest("input, textarea, select")) return;
  if (event.key === "ArrowLeft") { event.preventDefault(); toon(geopend - 1); }
  if (event.key === "ArrowRight") { event.preventDefault(); toon(geopend + 1); }
});
$("#lbPrev").addEventListener("click", () => toon(geopend - 1));
$("#lbNext").addEventListener("click", () => toon(geopend + 1));
$("#lbClose").addEventListener("click", () => lightbox.close());
$("#playerAgain").addEventListener("click", () => speelAf(media[soort][geopend]));
$("#lbDelete").addEventListener("click", () => {
  const m = media[soort][geopend];
  if (m) verwijder(m);
});

// -- verwijderen ----------------------------------------------------------------------

async function verwijder(m) {
  const video = soort === "opnames";
  const ok = await bevestig({
    titel: video ? "Opname verwijderen?" : "Foto verwijderen?",
    tekst: `${m.naam} (${formatDatum(m.tijd)}) wordt van de Raspberry Pi verwijderd. Dit kun je niet ongedaan maken.`,
  });
  if (!ok) return;
  try {
    await api(`/api/media/${soort}/${encodeURIComponent(m.naam)}`, { method: "DELETE" });
    toast(video ? "Opname verwijderd." : "Foto verwijderd.", { soort: "ok" });
    const lijst = media[soort];
    const plek = lijst.indexOf(m);
    if (plek >= 0) lijst.splice(plek, 1);
    render();
    if (lightbox.open) {
      if (lijst.length) toon(Math.min(plek, lijst.length - 1));
      else lightbox.close();  // de focus gaat dan naar het tabblad (zie "close")
    } else {
      focusOpKaart(plek);  // de knop die je gebruikte is weg
    }
  } catch (err) {
    toast(`Verwijderen lukte niet: ${err.message}`, { soort: "fout" });
    if (err.status === 404) laad();
  }
}

// Eén keer de status ophalen: voor de bovenbalk en welke onderdelen de server heeft.
api("/api/status?na=999999999").then(leesStatus).catch(() => {});
laad();
