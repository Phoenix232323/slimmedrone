// Kleine grafiekjes in SVG, zonder bibliotheek (werkt dus ook zonder internet).
//
//   lijnGrafiek(box, opties)   personen/objecten door de tijd, met een verticale lijn
//                              die de muis (of de pijltjestoetsen) volgt en een uitleg-vakje
//   staafGrafiek(box, opties)  gestapelde staven (meldingen per uur, per soort)
//
// Kleuren komen uit style.css (klassen s1, s2, seg-...), zodat alles één geheel is.
// De vorm volgt vaste regels: dunne lijnen (2 px), dunne rasterlijnen, staven hoogstens
// 24 px breed met een ronde bovenkant en 2 px ruimte tussen de stukjes.

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

function tekst(x, y, inhoud, attrs = {}) {
  const t = svg("text", { x, y, ...attrs });
  t.textContent = inhoud;
  return t;
}

// Mooie verdeling van de y-as: 0, 1, 2, ... of 0, 2, 4, ... of 0, 5, 10, ... (hele getallen).
function mooieAs(max, stappen = 4) {
  const ruw = Math.max(1, max) / stappen;
  const macht = 10 ** Math.floor(Math.log10(ruw));
  const stap = Math.max(1, [1, 2, 5, 10].map((f) => f * macht).find((s) => s >= ruw));
  const top = Math.max(stap, Math.ceil(Math.max(1, max) / stap) * stap);
  const ticks = [];
  for (let v = 0; v <= top + 1e-9; v += stap) ticks.push(v);
  return { top, ticks };
}

function klok(t) {
  return new Date(t * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
}

// -- het uitleg-vakje (tooltip) -------------------------------------------------------
// Waarde eerst (vet), naam erachter; een kort lijntje in de kleur van de reeks.

const tip = () => document.getElementById("chartTip");

function toonTip(clientX, clientY, titel, rijen) {
  const box = tip();
  if (!box) return;
  box.replaceChildren(
    Object.assign(document.createElement("div"), { className: "tip-title", textContent: titel }),
    ...rijen.map((r) => {
      const rij = document.createElement("div");
      rij.className = "tip-row";
      const sleutel = document.createElement("i");
      sleutel.className = `key ${r.vorm || "line"} ${r.klasse}`;
      const waarde = document.createElement("b");
      waarde.textContent = r.waarde;
      const naam = document.createElement("span");
      naam.textContent = r.naam;
      rij.append(sleutel, waarde, naam);
      return rij;
    }));
  box.hidden = false;
  // Naast de muis, maar binnen het scherm.
  const w = box.offsetWidth, h = box.offsetHeight;
  let x = clientX + 16, y = clientY - h - 12;
  if (x + w > window.innerWidth - 8) x = clientX - w - 16;
  if (y < 8) y = clientY + 16;
  box.style.left = `${Math.max(8, x)}px`;
  box.style.top = `${Math.max(8, y)}px`;
}

function verbergTip() {
  const box = tip();
  if (box) box.hidden = true;
}

// Het vakje staat vast op het scherm. Bij scrollen hoort het niet meer bij de muis: weg ermee.
// Staat het toetsenbord op een grafiek, dan zetten we het vakje op de nieuwe plek.
window.addEventListener("scroll", () => {
  const actief = document.activeElement;
  if (actief && actief._toon && actief.dataset.index !== undefined) actief._toon(Number(actief.dataset.index), true);
  else verbergTip();
}, { passive: true });

// Het svg-vlak maken (zo breed als het vak, scherp op elk scherm).
function vlak(box, hoogte) {
  const breedte = Math.max(240, Math.floor(box.clientWidth - parseFloat(getComputedStyle(box).paddingLeft)
    - parseFloat(getComputedStyle(box).paddingRight)));
  const s = svg("svg", { width: breedte, height: hoogte, viewBox: `0 0 ${breedte} ${hoogte}`, "aria-hidden": "true" });
  box.querySelector("svg")?.remove();
  box.querySelector(".chart-empty")?.remove();
  box.append(s);
  return { s, breedte };
}

// Pijltjestoetsen op een grafiek: de verticale lijn verschuiven, Esc = weg.
function toetsenbord(box, aantal, toonIndex) {
  box.tabIndex = 0;
  box._toon = toonIndex;
  box.onkeydown = (event) => {
    const nu = Number(box.dataset.index ?? aantal - 1);
    let nieuw = null;
    if (event.key === "ArrowLeft") nieuw = Math.max(0, nu - 1);
    if (event.key === "ArrowRight") nieuw = Math.min(aantal - 1, nu + 1);
    if (event.key === "Home") nieuw = 0;
    if (event.key === "End") nieuw = aantal - 1;
    if (event.key === "Escape") { delete box.dataset.index; toonIndex(null); return; }
    if (nieuw === null) return;
    event.preventDefault();
    box.dataset.index = nieuw;
    toonIndex(nieuw, true);
  };
  box.onblur = () => {
    delete box.dataset.index;
    toonIndex(null);
  };
}

// -- lijngrafiek ------------------------------------------------------------------------

// Vanaf hoeveel seconden tussen twee punten is er een gat (de app stond uit)? De punten
// liggen niet altijd "stap" uit elkaar: na een herstart komt het deel van vóór de herstart
// uit statistiek.csv (één regel per minuut), daarna volgen blokken van 10 s. Daarom kijken
// we naar de gewone afstand tussen de punten (de mediaan), en nooit minder dan 90 s.
function gatGrens(punten, stap) {
  const afstanden = [];
  for (let i = 1; i < punten.length; i++) afstanden.push(punten[i].tijd - punten[i - 1].tijd);
  afstanden.sort((a, b) => a - b);
  const gewoon = afstanden.length ? afstanden[Math.floor(afstanden.length / 2)] : stap;
  return Math.max(stap * 3, gewoon * 2, 90);
}
// opties: punten [{tijd, ...}], reeksen [{sleutel, naam, klasse, vlak}], stap (s),
//         venster (s), hoogte, tijdLabel(t) voor de tooltip.

function lijnGrafiek(box, { punten, reeksen, stap, venster, hoogte = 240 }) {
  const { s, breedte } = vlak(box, hoogte);
  // Op een smal scherm geen namen aan het eind van de lijnen (de legenda staat erboven).
  const metNamen = breedte >= 560;
  const m = { links: 32, rechts: metNamen ? 84 : 12, boven: 8, onder: 24 };
  const pw = breedte - m.links - m.rechts, ph = hoogte - m.boven - m.onder;
  const eind = punten.length ? punten[punten.length - 1].tijd : Date.now() / 1000;
  const begin = eind - venster;
  const max = Math.max(0, ...punten.flatMap((p) => reeksen.map((r) => p[r.sleutel] || 0)));
  const as = mooieAs(Math.max(max, 3));
  const x = (t) => m.links + ((t - begin) / venster) * pw;
  const y = (v) => m.boven + ph - (v / as.top) * ph;

  // Raster en assen
  const raster = svg("g", { class: "grid" });
  for (const v of as.ticks) {
    raster.append(svg("line", { x1: m.links, x2: m.links + pw, y1: Math.round(y(v)) + .5, y2: Math.round(y(v)) + .5 }));
    s.append(tekst(m.links - 8, y(v) + 4, String(v), { "text-anchor": "end" }));
  }
  s.prepend(raster);
  s.append(svg("g", { class: "axis" }, svg("line", { x1: m.links, x2: m.links + pw, y1: Math.round(y(0)) + .5, y2: Math.round(y(0)) + .5 })));
  // Tijden onder de x-as: om de 10 minuten (uur) of om de 3 of 6 uur (dag).
  const tijdStap = venster <= 3600 ? (pw < 360 ? 1200 : 600) : (pw < 420 ? 6 * 3600 : 3 * 3600);
  for (let t = Math.ceil(begin / tijdStap) * tijdStap; t <= eind; t += tijdStap) {
    s.append(tekst(x(t), hoogte - 6, klok(t), { "text-anchor": "middle" }));
  }

  // Stukken lijn: een gat (de app stond uit) breekt de lijn.
  const zichtbaar = punten.filter((p) => p.tijd >= begin);
  const grens = gatGrens(zichtbaar, stap);
  const stukken = [];
  let stuk = [];
  for (const p of zichtbaar) {
    if (stuk.length && p.tijd - stuk[stuk.length - 1].tijd > grens) {
      stukken.push(stuk);
      stuk = [];
    }
    stuk.push(p);
  }
  if (stuk.length) stukken.push(stuk);

  // Trapjes: een telling geldt voor het hele blok, dus horizontaal tot het volgende punt.
  for (const r of reeksen) {
    for (const deel of stukken) {
      let d = "";
      deel.forEach((p, i) => {
        const px = x(p.tijd), py = y(p[r.sleutel] || 0);
        d += i ? `H${px.toFixed(1)}V${py.toFixed(1)}` : `M${px.toFixed(1)},${py.toFixed(1)}`;
      });
      const laatste = deel[deel.length - 1];
      d += `H${Math.min(x(laatste.tijd + stap), m.links + pw).toFixed(1)}`;
      if (r.vlak) {
        s.append(svg("path", { d: `${d}V${y(0)}H${x(deel[0].tijd).toFixed(1)}Z`, class: `area ${r.klasse}` }));
      }
      s.append(svg("path", { d, class: `line ${r.klasse}` }));
    }
  }

  // Namen aan het eind van de lijnen, alleen als ze niet over elkaar vallen.
  const laatste = punten[punten.length - 1];
  if (laatste && metNamen) {
    const posities = reeksen.map((r) => ({ r, y: y(laatste[r.sleutel] || 0) }));
    const vrij = posities.every((a, i) => posities.every((b, j) => i === j || Math.abs(a.y - b.y) >= 14));
    if (vrij) {
      for (const { r, y: py } of posities) {
        s.append(tekst(m.links + pw + 8, py + 4, `${r.naam} ${laatste[r.sleutel] || 0}`, { class: "end-label" }));
      }
    }
  }

  // Muis en toetsenbord: verticale lijn + stippen + uitleg-vakje.
  const kruis = svg("line", { class: "cross", y1: m.boven, y2: m.boven + ph, visibility: "hidden" });
  const stippen = reeksen.map((r) => svg("circle", { r: 4, class: `dot ${r.klasse}`, visibility: "hidden" }));
  s.append(kruis, ...stippen);

  function toonIndex(i, viaToets = false) {
    if (i === null || !zichtbaar[i]) {
      kruis.setAttribute("visibility", "hidden");
      stippen.forEach((d) => d.setAttribute("visibility", "hidden"));
      verbergTip();
      return;
    }
    const p = zichtbaar[i];
    const px = Math.round(x(p.tijd)) + .5;
    kruis.setAttribute("x1", px);
    kruis.setAttribute("x2", px);
    kruis.setAttribute("visibility", "visible");
    reeksen.forEach((r, n) => {
      stippen[n].setAttribute("cx", px);
      stippen[n].setAttribute("cy", y(p[r.sleutel] || 0));
      stippen[n].setAttribute("visibility", "visible");
    });
    const rect = s.getBoundingClientRect();
    const tot = new Date((p.tijd + stap) * 1000);
    const titel = stap >= 300 ? `${klok(p.tijd)} – ${klok(tot / 1000)}` : klok(p.tijd);
    const cy = viaToets ? rect.top + m.boven + 12 : null;
    toonTip(rect.left + px, cy ?? toonIndex.muisY ?? rect.top + m.boven, titel,
      reeksen.map((r) => ({ waarde: String(p[r.sleutel] || 0), naam: r.naam, klasse: r.klasse })));
  }

  const vang = svg("rect", { class: "hit", x: m.links, y: m.boven, width: pw, height: ph });
  s.append(vang);
  vang.addEventListener("pointermove", (event) => {
    if (!zichtbaar.length) return;
    const rect = s.getBoundingClientRect();
    const t = begin + ((event.clientX - rect.left - m.links) / pw) * venster;
    // Het punt waarvan het blok onder de muis ligt.
    let i = zichtbaar.findIndex((p) => p.tijd > t) - 1;
    if (i < 0) i = zichtbaar[0].tijd > t ? 0 : zichtbaar.length - 1;
    // Het blok loopt tot het volgende punt, behalve vóór een gat: dan is het één stap lang.
    const volgende = zichtbaar[i + 1];
    const blok = volgende && volgende.tijd - zichtbaar[i].tijd <= grens ? volgende.tijd - zichtbaar[i].tijd : stap;
    if (t - zichtbaar[i].tijd > Math.max(blok, stap)) return toonIndex(null);  // in een gat
    toonIndex.muisY = event.clientY;
    box.dataset.index = i;
    toonIndex(i);
  });
  vang.addEventListener("pointerleave", () => { delete box.dataset.index; toonIndex(null); });
  toetsenbord(box, zichtbaar.length, toonIndex);

  // Niets om te laten zien? Zeg dat er duidelijk bij (een platte lijn op 0 is makkelijk te missen).
  const leeg = !zichtbaar.length ? "Nog geen gegevens in deze periode." : !max ? "Niemand en niets in beeld in deze periode." : "";
  if (leeg) box.append(Object.assign(document.createElement("p"), { className: "chart-empty", textContent: leeg }));
}

// -- gestapelde staven ---------------------------------------------------------------------
// opties: kolommen [{tijd, label, waarden: {soort: aantal}}], soorten [{sleutel, naam, klasse}]
//         (van onder naar boven), hoogte, leeg (tekst als alles 0 is).

function staafGrafiek(box, { kolommen, soorten, hoogte = 220, leeg = "Geen meldingen." }) {
  const { s, breedte } = vlak(box, hoogte);
  const m = { links: 28, rechts: 8, boven: 8, onder: 24 };
  const pw = breedte - m.links - m.rechts, ph = hoogte - m.boven - m.onder;
  const totaal = (k) => soorten.reduce((som, z) => som + (k.waarden[z.sleutel] || 0), 0);
  const max = Math.max(0, ...kolommen.map(totaal));
  const as = mooieAs(Math.max(max, 3));
  const band = pw / Math.max(1, kolommen.length);
  const dik = Math.max(2, Math.min(24, band * 0.62));
  const y = (v) => m.boven + ph - (v / as.top) * ph;

  const raster = svg("g", { class: "grid" });
  for (const v of as.ticks) {
    raster.append(svg("line", { x1: m.links, x2: m.links + pw, y1: Math.round(y(v)) + .5, y2: Math.round(y(v)) + .5 }));
    s.append(tekst(m.links - 8, y(v) + 4, String(v), { "text-anchor": "end" }));
  }
  s.prepend(raster);

  const elkeN = band < 22 ? 6 : 3;  // labels om de 3 (of 6) uur
  const groepen = kolommen.map((k, i) => {
    const cx = m.links + band * i + band / 2;
    const g = svg("g", { class: "col" });
    let basis = 0;
    const delen = soorten.filter((z) => (k.waarden[z.sleutel] || 0) > 0);
    delen.forEach((z, n) => {
      const v = k.waarden[z.sleutel];
      const y0 = y(basis), y1 = y(basis + v);
      basis += v;
      const boven = n === delen.length - 1;
      // 2 px ruimte tussen de stukjes (de achtergrond schijnt erdoor).
      const top = y1, bodem = y0 - (n > 0 ? 2 : 0);
      const h = Math.max(1, bodem - top);
      const x0 = cx - dik / 2;
      const r = boven ? Math.min(4, h, dik / 2) : 0;
      // Ronde bovenkant alleen aan het einde van de staaf, recht op de basislijn.
      const d = `M${x0},${bodem}V${top + r}${r ? `Q${x0},${top} ${x0 + r},${top}` : ""}H${x0 + dik - r}` +
        `${r ? `Q${x0 + dik},${top} ${x0 + dik},${top + r}` : ""}V${bodem}Z`;
      g.append(svg("path", { d, class: `seg-rect ${z.klasse}` }));
    });
    s.append(g);
    if (i % elkeN === 0) s.append(tekst(cx, hoogte - 6, k.label, { "text-anchor": "middle" }));
    return g;
  });
  s.append(svg("g", { class: "axis" }, svg("line", { x1: m.links, x2: m.links + pw, y1: Math.round(y(0)) + .5, y2: Math.round(y(0)) + .5 })));

  const markering = svg("rect", { class: "col-hit", y: m.boven, height: ph, width: band, visibility: "hidden", rx: 4 });
  s.append(markering);

  function toonIndex(i, viaToets = false) {
    groepen.forEach((g, n) => g.classList.toggle("actief", n === i));
    if (i === null || !kolommen[i]) {
      markering.setAttribute("visibility", "hidden");
      verbergTip();
      return;
    }
    const k = kolommen[i];
    markering.setAttribute("x", m.links + band * i);
    markering.setAttribute("visibility", viaToets ? "visible" : "hidden");
    const rect = s.getBoundingClientRect();
    const cx = rect.left + m.links + band * i + band / 2;
    const rijen = [...soorten].reverse().map((z) => ({ waarde: String(k.waarden[z.sleutel] || 0), naam: z.naam, klasse: z.klasse, vorm: "box" }));
    toonTip(cx, viaToets ? rect.top + m.boven + 12 : toonIndex.muisY, `${k.titel} · ${totaal(k)} totaal`, rijen);
  }

  const vang = svg("rect", { class: "hit", x: m.links, y: m.boven, width: pw, height: ph });
  s.append(vang);
  vang.addEventListener("pointermove", (event) => {
    const rect = s.getBoundingClientRect();
    const i = Math.floor((event.clientX - rect.left - m.links) / band);
    if (i < 0 || i >= kolommen.length) return toonIndex(null);
    toonIndex.muisY = event.clientY;
    box.dataset.index = i;
    toonIndex(i);
  });
  vang.addEventListener("pointerleave", () => { delete box.dataset.index; toonIndex(null); });
  toetsenbord(box, kolommen.length, toonIndex);

  if (!max) box.append(Object.assign(document.createElement("p"), { className: "chart-empty", textContent: leeg }));
}
