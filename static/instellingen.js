// Instellingenpagina: de Claude-sleutel en -keuzes (bewaard op de Raspberry Pi),
// spraak, browsermeldingen en de overlay (alleen in deze browser), gebruikers en je
// eigen wachtwoord, en informatie over het systeem.

// -- slimme AI (Claude) ---------------------------------------------------------------

const BRON = { website: "opgeslagen via deze website", env: "uit het bestand .env" };

async function laadAi() {
  try {
    toonAi((await api("/api/instellingen")).ai);
  } catch (err) {
    if (err.status === 404) {
      $("#keyStatus").textContent = "Deze versie van de server kan de sleutel nog niet via de website instellen. " +
        "Zet ANTHROPIC_API_KEY in het bestand .env en start de app opnieuw.";
      for (const id of ["#keyForm", "#effortSeg", "#webToggle"]) $(id).closest(".setting, form").hidden = true;
      $("#aiBadge").hidden = true;
    } else {
      $("#keyStatus").textContent = `Laden lukte niet: ${err.message}`;
    }
  }
}

function toonAi(ai) {
  const claude = ai.modus === "claude";
  const badge = $("#aiBadge");
  badge.textContent = claude ? "Claude actief" : "Lokale modus";
  badge.className = `badge ${claude ? "accent" : "warn"}`;

  const status = $("#keyStatus");
  status.textContent = ai.sleutel_ingesteld
    ? `Sleutel ingesteld (${ai.sleutel_einde || "…"}), ${BRON[ai.bron_sleutel] || "bron onbekend"}.`
    : "Nog geen sleutel ingesteld. Plak hieronder je sleutel van Anthropic.";
  status.className = ai.sleutel_ingesteld ? "ok" : "";
  $("#keyRemove").hidden = ai.bron_sleutel !== "website";
  $("#keyInput").placeholder = ai.sleutel_ingesteld ? "Nieuwe sleutel (sk-ant-...)" : "sk-ant-...";

  document.querySelectorAll("#effortSeg button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.effort === ai.effort)));
  $("#webToggle").checked = !!ai.web;
  $("#modelName").textContent = ai.model || "—";

  // Werkt de sleutel niet? Dan vertelt de server (soms) waarom.
  const problem = $("#aiProblem");
  problem.hidden = !ai.melding;
  problem.textContent = ai.melding || "";
}

async function bewaarAi(wijziging, gelukt) {
  try {
    const { ai } = await api("/api/instellingen", { method: "POST", json: wijziging });
    toonAi(ai);
    if (gelukt) toast(gelukt(ai), { soort: "ok" });
    return true;
  } catch (err) {
    toast(err.message, { soort: "fout", duur: 9000 });
    return false;
  }
}

$("#keyForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("#keyInput").value.trim();
  if (!key) {
    toast("Plak eerst je sleutel in het vak.", { soort: "fout" });
    $("#keyInput").focus();
    return;
  }
  $("#keySave").disabled = true;
  const ok = await bewaarAi({ sleutel: key }, (ai) => ai.modus === "claude"
    ? "Sleutel opgeslagen. J.A.R.V.I.S. gebruikt nu Claude."
    : "Sleutel opgeslagen, maar Claude werkt nog niet. Kijk bij de melding hierboven.");
  $("#keySave").disabled = false;
  if (ok) $("#keyInput").value = "";
});

$("#keyRemove").addEventListener("click", async () => {
  const ok = await bevestig({
    titel: "Sleutel verwijderen?",
    tekst: "De sleutel die via de website is opgeslagen wordt verwijderd. Staat er een sleutel in .env, dan wordt die weer gebruikt.",
  });
  if (ok) bewaarAi({ sleutel: "" }, () => "Sleutel verwijderd.");
});

$("#effortSeg").addEventListener("click", (event) => {
  const knop = event.target.closest("button");
  if (knop) bewaarAi({ effort: knop.dataset.effort }, () => `Denkniveau: ${knop.textContent}.`);
});

$("#webToggle").addEventListener("change", (event) => {
  bewaarAi({ web: event.target.checked }, (ai) => ai.web ? "Internet zoeken staat aan." : "Internet zoeken staat uit.");
});

// -- spraak (alleen in deze browser) ---------------------------------------------------

function vulStemKeuze() {
  const select = $("#sVoice");
  const lijst = stemmen();
  const gekozen = gekozenStem();
  select.replaceChildren(...(lijst.length
    ? lijst.map((v) => el("option", { value: v.name, selected: gekozen && v.name === gekozen.name }, `${v.name} (${v.lang})`))
    : [el("option", { value: "" }, kanVoorlezen ? "Standaardstem" : "Voorlezen kan niet in deze browser")]));
  select.disabled = !lijst.length;
}

function toonSpraak() {
  $("#sSpeak").checked = spraak.voorlezen;
  $("#sSpeak").disabled = !kanVoorlezen;
  $("#sTest").disabled = !kanVoorlezen;
  $("#sRate").value = spraak.tempo;
  $("#sRateOut").textContent = `${Number(spraak.tempo).toFixed(1)}×`;
  $("#sWake").checked = spraak.wekwoord && kanLuisteren;
  $("#sWake").disabled = !kanLuisteren;
  $("#sAlerts").checked = spraak.meldingen;
  $("#sBeep").checked = spraak.piep;
  const uitleg = microfoonUitleg();
  const info = $("#micInfo");
  info.className = uitleg ? "notice warn" : "notice";
  info.replaceChildren(icon(uitleg ? "mic-off" : "mic"), el("span", {}, uitleg ||
    "Inspreken werkt in deze browser: gebruik de microfoonknop naast het tekstvak van J.A.R.V.I.S."));
}

$("#sSpeak").addEventListener("change", (e) => zetSpraak("voorlezen", e.target.checked));
$("#sVoice").addEventListener("change", (e) => zetSpraak("stem", e.target.value));
$("#sRate").addEventListener("input", (e) => {
  zetSpraak("tempo", Number(e.target.value));
  $("#sRateOut").textContent = `${Number(e.target.value).toFixed(1)}×`;
});
$("#sTest").addEventListener("click", () => {
  stilte();
  zeg("Goedendag. Ik ben J.A.R.V.I.S., en zo klink ik.");
});
$("#sWake").addEventListener("change", (e) => zetSpraak("wekwoord", e.target.checked));
$("#sAlerts").addEventListener("change", (e) => zetSpraak("meldingen", e.target.checked));
$("#sBeep").addEventListener("change", (e) => zetSpraak("piep", e.target.checked));
$("#sBeepTest").addEventListener("click", () => piep("onbekend"));

if (kanVoorlezen) speechSynthesis.addEventListener?.("voiceschanged", vulStemKeuze);

// -- browsermeldingen (alleen in deze browser) ------------------------------------------

function toonBrowserMeldingen() {
  const uitleg = browserMeldingUitleg();  // app.js: waarom het hier niet kan, of ""
  const info = $("#notifyInfo");
  info.hidden = !uitleg;
  info.textContent = uitleg;
  const toggle = $("#notifyToggle");
  toggle.disabled = !!uitleg;
  toggle.checked = browserMeldingenAan();
  $("#notifyTest").disabled = !toggle.checked;
}

$("#notifyToggle").addEventListener("change", async (event) => {
  if (!event.target.checked) {
    bewaar("browsermeldingen", false);
    toonBrowserMeldingen();
    return;
  }
  // De browser vraagt nu om toestemming (alleen na een klik mag dat).
  let toestemming = Notification.permission;
  if (toestemming === "default") {
    try { toestemming = await Notification.requestPermission(); } catch { toestemming = "denied"; }
  }
  bewaar("browsermeldingen", toestemming === "granted");
  if (toestemming !== "granted") toast("Zonder toestemming van de browser kunnen er geen meldingen komen.", { soort: "fout" });
  toonBrowserMeldingen();
});

$("#notifyTest").addEventListener("click", () => {
  try {
    new Notification("SlimmeDrone · Test", { body: "Zo ziet een melding eruit.", icon: "/static/icon.svg" });
  } catch {
    toast("Deze browser kan hier geen melding tonen.", { soort: "fout" });
  }
});

// -- weergave ---------------------------------------------------------------------------

let weergaveBezig = false;

async function zetWeergave(naam, aan) {
  weergaveBezig = true;
  try {
    const w = await api("/api/weergave", { method: "POST", json: { [naam]: aan } });
    $("#dKaders").checked = w.kaders;
    $("#dTekst").checked = w.hud;
  } catch (err) {
    toast(`Dat lukte niet: ${err.message}`, { soort: "fout" });
  } finally {
    weergaveBezig = false;
  }
}

$("#dKaders").addEventListener("change", (e) => zetWeergave("kaders", e.target.checked));
$("#dTekst").addEventListener("change", (e) => zetWeergave("hud", e.target.checked));
$("#dOverlay").checked = bewaard("hud-overlay", true);
$("#dOverlay").addEventListener("change", (e) => bewaar("hud-overlay", e.target.checked));

// -- gebruikers ---------------------------------------------------------------------------
// Alleen als de server het kan. Dat weten we na de eerste status (veld "opname" = nieuwe
// server); zo vraagt de website een oudere server niet om iets wat hij niet heeft.

let ik = "";
let gebruikersGevraagd = false;

function toonGebruikersDelen(aan) {
  for (const id of ["secUsers", "secPassword"]) {
    $(`#${id}`).hidden = !aan;
    document.querySelector(`[data-toc="${id}"]`).hidden = !aan;
  }
}

async function laadGebruikers() {
  let data;
  try {
    data = await api("/api/gebruikers");
  } catch (err) {
    if (err.status === 404) return toonGebruikersDelen(false);
    toonGebruikersDelen(true);
    $("#userRows").replaceChildren(el("tr", {}, el("td", { colspan: 2, class: "muted" }, `Laden lukte niet: ${err.message}`)));
    return;
  }
  toonGebruikersDelen(true);
  ik = data.ik || "";
  const lijst = data.gebruikers || [];
  $("#userCount").textContent = lijst.length;
  $("#userRows").replaceChildren(...lijst.map((g) => {
    const jij = g.naam === ik;
    const alleen = lijst.length <= 1;
    return el("tr", {},
      el("td", {}, g.naam, jij ? el("span", { class: "badge accent you" }, "jij") : null),
      el("td", { class: "actions" }, jij || alleen
        ? el("span", { class: "hint" }, jij ? "Dat ben jij" : "Laatste gebruiker")
        : el("button", {
          class: "btn sm ghost danger", type: "button", "aria-label": `${g.naam} verwijderen`,
          onclick: () => verwijderGebruiker(g.naam),
        }, icon("trash"), "Verwijderen")));
  }));
}

async function verwijderGebruiker(naam) {
  const ok = await bevestig({
    titel: `${naam} verwijderen?`,
    tekst: `${naam} kan daarna niet meer inloggen op deze website. Dit kun je niet ongedaan maken (maak eventueel opnieuw een account).`,
  });
  if (!ok) return;
  try {
    await api(`/api/gebruikers/${encodeURIComponent(naam)}`, { method: "DELETE" });
    toast(`${naam} is verwijderd.`, { soort: "ok" });
  } catch (err) {
    toast(err.message, { soort: "fout", duur: 9000 });
  }
  laadGebruikers();
}

// Fout bij een veld tonen (rode rand + tekst onder het formulier).
function formulierFout(vak, tekst, veld = null) {
  vak.hidden = !tekst;
  vak.textContent = tekst || "";
  vak.closest("form").querySelectorAll(".input").forEach((i) => i.removeAttribute("aria-invalid"));
  if (veld) {
    veld.setAttribute("aria-invalid", "true");
    veld.focus();
  }
}

$("#userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const naam = $("#newUser").value.trim().toLowerCase();
  const wachtwoord = $("#newUserPass").value;
  const fout = $("#userError");
  if (!/^[a-z0-9_.-]{2,32}$/.test(naam)) {
    return formulierFout(fout, "Gebruikersnaam: 2-32 tekens, alleen kleine letters, cijfers, punt, _ of -.", $("#newUser"));
  }
  if (wachtwoord.length < 8) return formulierFout(fout, "Het wachtwoord moet minstens 8 tekens zijn.", $("#newUserPass"));
  formulierFout(fout, "");
  $("#userAdd").disabled = true;
  try {
    await api("/api/gebruikers", { method: "POST", json: { naam, wachtwoord } });
    toast(`${naam} kan nu inloggen.`, { soort: "ok" });
    event.target.reset();
    laadGebruikers();
  } catch (err) {
    formulierFout(fout, err.message);
  } finally {
    $("#userAdd").disabled = false;
  }
});

$("#passForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const oud = $("#passOld").value, nieuw = $("#passNew").value, nogEens = $("#passNew2").value;
  const fout = $("#passError");
  if (!oud) return formulierFout(fout, "Vul je huidige wachtwoord in.", $("#passOld"));
  if (nieuw.length < 8) return formulierFout(fout, "Het nieuwe wachtwoord moet minstens 8 tekens zijn.", $("#passNew"));
  if (nieuw !== nogEens) return formulierFout(fout, "De twee nieuwe wachtwoorden zijn niet hetzelfde.", $("#passNew2"));
  formulierFout(fout, "");
  $("#passSave").disabled = true;
  try {
    await api("/api/wachtwoord", { method: "POST", json: { oud, nieuw } });
    toast("Je wachtwoord is gewijzigd.", { soort: "ok" });
    event.target.reset();
  } catch (err) {
    formulierFout(fout, err.message, err.status === 400 && /huidig|oud/i.test(err.message) ? $("#passOld") : null);
  } finally {
    $("#passSave").disabled = false;
  }
});

// -- systeem ----------------------------------------------------------------------------

function duur(seconden) {
  const min = Math.floor(seconden / 60), uur = Math.floor(min / 60), dagen = Math.floor(uur / 24);
  if (dagen) return `${dagen} d ${uur % 24} u`;
  if (uur) return `${uur} u ${min % 60} min`;
  return `${min} min`;
}

function rij(naam, waarde, soort = "") {
  if (waarde === null || waarde === undefined || waarde === "") return [];
  return [el("dt", {}, naam), el("dd", { class: soort }, String(waarde))];
}

// "/home/pi/video/test.avi" -> "test.avi" (het hele pad past niet in het vakje).
function kortePad(bron) {
  const tekst = String(bron ?? "");
  return tekst.includes("/") ? tekst.split("/").filter(Boolean).pop() : tekst;
}

function getal(v, eenheid, decimalen = 1) {
  return typeof v === "number" ? `${v.toFixed(decimalen)}${eenheid}` : null;
}

async function laadSysteem() {
  try {
    // De meldingen (elk met een fotootje) gebruiken we hier niet. Met een heel hoge
    // "na" stuurt de server er geen mee; dat scheelt de Pi en de wifi veel werk.
    const s = await api("/api/status?na=999999999");
    leesStatus(s);
    if (!gebruikersGevraagd) {
      gebruikersGevraagd = true;
      if (functies.opname) laadGebruikers();
    }
    const p = s.prestaties || {}, sys = s.systeem || {}, ai = s.ai_info || {};
    $("#sysList").replaceChildren(
      ...rij("Camera", s.camera.verbonden ? "verbonden" : `los: ${s.camera.fout || "geen beeld"}`, s.camera.verbonden ? "ok" : "bad"),
      ...rij("Bron", kortePad(s.camera.bron)),
      ...rij("Beeldgrootte", p.breedte ? `${p.breedte}×${p.hoogte}` : null),
      ...rij("Beeld (website)", getal(p.beeld_fps ?? s.fps, " fps")),
      ...rij("Camera", getal(p.camera_fps, " fps")),
      ...rij("Objecten", getal(p.objecten_fps, " /s")),
      ...rij("Gezichten", getal(p.gezichten_fps, " /s")),
      ...rij("Objectherkenning", s.objectherkenning),
      ...rij("CPU-temperatuur", getal(sys.cpu_temp, " °C"), sys.cpu_temp >= 80 ? "bad" : sys.cpu_temp >= 70 ? "warn" : ""),
      ...rij("CPU-belasting", getal(sys.cpu_belasting, " %", 0), sys.cpu_belasting >= 85 ? "warn" : ""),
      ...rij("Geheugen", getal(sys.geheugen, " %", 0), sys.geheugen >= 85 ? "warn" : ""),
      ...rij("Draait al", typeof sys.uptime === "number" ? duur(sys.uptime) : null),
      ...rij("Opname", s.opname ? (s.opname.actief ? `bezig (${formatDuur(s.opname.duur)})` : "uit") : null,
        s.opname && s.opname.actief ? "bad" : ""),
      ...rij("J.A.R.V.I.S.", s.ai === "claude" ? "Claude" : "lokale modus", s.ai === "claude" ? "ok" : "warn"),
      ...rij("Model", ai.model),
      ...rij("Denkniveau", ai.effort),
      ...rij("Internet zoeken", typeof ai.web === "boolean" ? (ai.web ? "aan" : "uit") : null),
    );
    $("#displayUnavailable").hidden = functies.weergave;
    if (s.weergave && !weergaveBezig) {
      $("#dKaders").checked = s.weergave.kaders;
      $("#dTekst").checked = s.weergave.hud;
    }
  } catch {
    geenVerbinding();
  }
  setTimeout(laadSysteem, document.hidden ? 10000 : 2000);
}

toonSpraak();
vulStemKeuze();
toonBrowserMeldingen();
seintjes.addEventListener("spraak", toonSpraak);
laadAi();
laadSysteem();
