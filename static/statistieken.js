// Statistiekenpagina: tegels met de belangrijkste getallen, een grafiek van personen en
// objecten door de tijd, meldingen per uur en wat het langst in beeld was.
// Elke 10 seconden nieuwe cijfers (alleen als je de pagina ziet); de grafieken worden
// dus hoogstens één keer per 10 seconden opnieuw getekend (of als het venster verandert).

const VERVERS_MS = 10000;
const PERIODES = {
  uur: { venster: 3600, tekst: "in het afgelopen uur", top: "Minuten in beeld, afgelopen uur" },
  dag: { venster: 86400, tekst: "in de afgelopen 24 uur", top: "Minuten in beeld, afgelopen 24 uur" },
};
const REEKSEN = [
  { sleutel: "personen", naam: "Personen", klasse: "s1", vlak: true },
  { sleutel: "objecten", naam: "Objecten", klasse: "s2" },
];
// Van onder naar boven gestapeld. Deze volgorde is gecontroleerd op kleurenblindheid:
// rood en amber staan nooit naast elkaar.
const SOORTEN = [
  { sleutel: "onbekend", naam: "Onbekend", klasse: "seg-onbekend k-onbekend" },
  { sleutel: "bekend", naam: "Bekend", klasse: "seg-bekend k-bekend" },
  { sleutel: "wacht", naam: "Wachter", klasse: "seg-wacht k-wacht" },
  { sleutel: "systeem", naam: "Systeem", klasse: "seg-systeem k-systeem" },
];

let periode = bewaard("statistiek-periode", "uur");
if (!PERIODES[periode]) periode = "uur";
let data = null;
let timer = null;
let laadNummer = 0;

// -- ophalen -----------------------------------------------------------------------------

async function laad() {
  clearTimeout(timer);
  const nummer = ++laadNummer;
  const body = $("#statsBody");
  try {
    const nieuw = await api(`/api/statistiek?periode=${periode === "dag" ? "dag" : "uur"}`);
    if (nummer !== laadNummer) return;  // intussen een andere periode gekozen
    data = nieuw;
    $("#statsError").hidden = true;
    body.hidden = false;
    render();
    $("#statsUpdated").textContent = `Bijgewerkt om ${new Date().toLocaleTimeString("nl-NL")} · elke 10 seconden`;
  } catch (err) {
    if (nummer !== laadNummer) return;
    const box = $("#statsError");
    box.hidden = false;
    box.replaceChildren(icon("alert"), el("span", {}, err.status === 404
      ? "Deze versie van de server houdt nog geen statistieken bij. Werk de software op de Raspberry Pi bij."
      : `Laden lukte niet: ${err.message}. We proberen het zo opnieuw.`));
    if (err.status === 404) {
      body.hidden = true;
      return;
    }
  } finally {
    body.classList.remove("vernieuwen");
    body.setAttribute("aria-busy", "false");
  }
  plan();
}

function plan() {
  clearTimeout(timer);
  if (!document.hidden) timer = setTimeout(laad, VERVERS_MS);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else laad();
});

// -- tonen -------------------------------------------------------------------------------

function render() {
  if (!data) return;
  const p = PERIODES[periode];
  const sam = data.samenvatting || {};
  zetGetal("#kpiMax", sam.max_personen);
  zetGetal("#kpiAlerts", sam.meldingen);
  zetGetal("#kpiUnknown", sam.onbekend);
  $("#kpiUnknown").classList.toggle("danger", sam.onbekend > 0);
  const namen = Array.isArray(sam.bekende_personen) ? sam.bekende_personen : [];
  zetGetal("#kpiKnown", namen.length);
  $("#kpiKnownNames").textContent = namen.length ? namen.join(", ") : "nog niemand herkend";
  $("#kpiKnownNames").title = namen.join(", ");
  document.querySelectorAll("[data-periode-tekst]").forEach((n) => { n.textContent = p.tekst; });

  const stap = Number(data.stap) || (periode === "dag" ? 300 : 10);
  $("#trendSub").textContent = `Per ${stap >= 60 ? `${Math.round(stap / 60)} minuten` : `${stap} seconden`}, het hoogste aantal in dat blok`;
  $("#topSub").textContent = p.top;
  tekenGrafieken();
  vulTabellen(stap);
  vulTop(data.top_objecten || []);
}

function zetGetal(id, waarde) {
  $(id).textContent = typeof waarde === "number" ? waarde.toLocaleString("nl-NL") : "–";
}

function alertKolommen() {
  return (data.meldingen_per_uur || []).map((u) => ({
    tijd: u.uur,
    label: new Date(u.uur * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" }),
    titel: `${uurLabel(u.uur)}`,
    waarden: u,
  }));
}

function uurLabel(t) {
  const van = new Date(t * 1000), tot = new Date((t + 3600) * 1000);
  const f = (d) => d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
  return `${f(van)} – ${f(tot)}`;
}

function tekenGrafieken() {
  if (!data) return;
  const punten = (data.punten || []).slice().sort((a, b) => a.tijd - b.tijd);
  lijnGrafiek($("#trendChart"), {
    punten, reeksen: REEKSEN, stap: Number(data.stap) || 10, venster: PERIODES[periode].venster,
    hoogte: kleinScherm.matches ? 200 : 260,
  });
  staafGrafiek($("#alertChart"), {
    kolommen: alertKolommen(), soorten: SOORTEN, hoogte: kleinScherm.matches ? 180 : 220,
    leeg: "Geen meldingen in de afgelopen 24 uur.",
  });
}

// Dezelfde cijfers als tabel (voor wie de grafiek niet kan of wil lezen).
function vulTabellen(stap) {
  const punten = data.punten || [];
  const blok = periode === "dag" ? 3600 : 300;
  const blokken = new Map();
  for (const p of punten) {
    const b = Math.floor(p.tijd / blok) * blok;
    const r = blokken.get(b) || { personen: 0, objecten: 0 };
    r.personen = Math.max(r.personen, p.personen || 0);
    r.objecten = Math.max(r.objecten, p.objecten || 0);
    blokken.set(b, r);
  }
  const kop = (...namen) => el("thead", {}, el("tr", {}, ...namen.map((n, i) => el("th", { scope: "col", class: i ? "num" : null }, n))));
  $("#trendTable").replaceChildren(
    kop("Tijd", "Max. personen", "Max. objecten"),
    el("tbody", {}, ...[...blokken.entries()].sort((a, b) => b[0] - a[0]).map(([t, r]) => el("tr", {},
      el("td", {}, `${klok(t)} – ${klok(t + blok)}`), el("td", { class: "num" }, String(r.personen)),
      el("td", { class: "num" }, String(r.objecten))))));
  $("#alertTable").replaceChildren(
    kop("Uur", ...SOORTEN.map((z) => z.naam), "Totaal"),
    el("tbody", {}, ...alertKolommen().reverse().map((k) => el("tr", {},
      el("td", {}, uurLabel(k.tijd)),
      ...SOORTEN.map((z) => el("td", { class: "num" }, String(k.waarden[z.sleutel] || 0))),
      el("td", { class: "num" }, String(SOORTEN.reduce((s, z) => s + (k.waarden[z.sleutel] || 0), 0)))))));
}

function minuten(m) {
  if (m < 1) return "< 1 min";
  if (m < 60) return `${m.toLocaleString("nl-NL", { maximumFractionDigits: 1 })} min`;
  return `${Math.floor(m / 60)} u ${Math.round(m % 60)} min`;
}

function vulTop(lijst) {
  const top = lijst.slice().sort((a, b) => b.minuten - a.minuten).slice(0, 8);
  const max = Math.max(1, ...top.map((t) => t.minuten));
  $("#topList").replaceChildren(...top.map((t) => el("li", {},
    el("span", { class: "name", title: t.naam }, t.naam),
    el("span", { class: "track" }, el("i", { class: "fill", style: `width:${Math.max(1, (t.minuten / max) * 100).toFixed(1)}%` })),
    el("span", { class: "val" }, minuten(t.minuten)))));
  $("#topEmpty").hidden = top.length > 0;
}

// -- periode kiezen ----------------------------------------------------------------------

function zetPeriode(nieuw) {
  periode = nieuw;
  bewaar("statistiek-periode", nieuw);
  document.querySelectorAll("#periodSeg button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.periode === nieuw)));
  // De oude grafiek blijft even (wat lichter) staan tot de nieuwe cijfers er zijn.
  if (data) $("#statsBody").classList.add("vernieuwen");
  laad();
}

$("#periodSeg").addEventListener("click", (event) => {
  const knop = event.target.closest("button");
  if (knop && knop.dataset.periode !== periode) zetPeriode(knop.dataset.periode);
});

// Bij een andere breedte opnieuw tekenen (de cijfers blijven hetzelfde).
let tekenGepland = false;
if (window.ResizeObserver) {
  let vorige = 0;
  new ResizeObserver((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    if (w === vorige || tekenGepland) return;
    vorige = w;
    tekenGepland = true;
    requestAnimationFrame(() => { tekenGepland = false; tekenGrafieken(); });
  }).observe($("#statsBody"));
}

// Eén keer de status ophalen: voor de bovenbalk en welke onderdelen de server heeft.
api("/api/status?na=999999999").then(leesStatus).catch(() => {});
document.querySelectorAll("#periodSeg button").forEach((b) =>
  b.setAttribute("aria-pressed", String(b.dataset.periode === periode)));
laad();
