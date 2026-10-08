// Instellingenpagina: de Claude-sleutel en -keuzes (bewaard op de Raspberry Pi),
// spraak en de HUD-overlay (alleen in deze browser), en informatie over het systeem.

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
    } else {
      $("#keyStatus").textContent = `Laden lukte niet: ${err.message}`;
    }
  }
}

function toonAi(ai) {
  const claude = ai.modus === "claude";
  const badge = $("#aiBadge");
  badge.textContent = claude ? "Claude actief" : "Lokale modus";
  badge.className = `status-tag ${claude ? "ok" : "warn"}`;

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
    if (gelukt) toast(gelukt(ai));
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

$("#keyRemove").addEventListener("click", () => {
  if (!confirm("De sleutel van de website verwijderen? Staat er een sleutel in .env, dan wordt die weer gebruikt.")) return;
  bewaarAi({ sleutel: "" }, () => "Sleutel verwijderd.");
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
    const s = await api("/api/status");
    leesStatus(s);
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
seintjes.addEventListener("spraak", toonSpraak);
laadAi();
laadSysteem();
