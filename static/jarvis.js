// J.A.R.V.I.S.: chatten (het antwoord komt woord voor woord binnen), voorstellen,
// spraak (inspreken, wekwoord, voorlezen) en de animatie van de "arc reactor".

const messages = $("#messages");
const input = $("#chatInput");
const sendBtn = $("#sendBtn");
const micBtn = $("#micBtn");
const orb = $("#orb");

let bezig = false;          // wacht J.A.R.V.I.S. nog op een antwoord?
let stopper = null;         // AbortController: hiermee stop je een antwoord halverwege
let gesprekGestart = false; // al iets gevraagd sinds de pagina geladen is?

// -- de orb: idle / listening / thinking / speaking ---------------------------------

const orbStaat = { denkt: false, luistert: false, spreekt: false };

function orbBijwerken() {
  const staat = orbStaat.luistert ? "listening"
    : orbStaat.spreekt ? "speaking"
      : orbStaat.denkt ? "thinking" : "idle";
  document.querySelectorAll(".orb").forEach((o) => { o.dataset.state = staat; });
}

seintjes.addEventListener("spreekt", (e) => {
  orbStaat.spreekt = e.detail;
  orbBijwerken();
});

// -- berichten tonen -----------------------------------------------------------------

function klok(tijd) {
  return new Date(tijd * 1000).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
}

// Alleen meescrollen als je onderaan was, zodat je rustig terug kunt lezen.
// "volgen" onthoudt of je onderaan stond toen je voor het laatst zelf scrolde.
let volgen = true;
messages.addEventListener("scroll", () => {
  volgen = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 60;
}, { passive: true });

function scrollNaarBeneden(altijd = false) {
  if (altijd) volgen = true;
  if (volgen) messages.scrollTop = messages.scrollHeight;
}

function kop(wie, tijd) {
  return el("div", { class: "msg-head" }, el("span", {}, wie), el("time", {}, klok(tijd)));
}

function gebruikerBericht(tekst, tijd = Date.now() / 1000) {
  messages.querySelector(".idle")?.remove();
  const node = el("div", { class: "msg user" }, kop("Jij", tijd), el("div", { class: "msg-body" }, tekst));
  messages.append(node);
  scrollNaarBeneden(true);
  return node;
}

// Een (oud of kort) antwoord in één keer tonen. Soort "busy-note" = "even geduld"-melding,
// "kort" = een begroeting zonder kopieerknop.
function jarvisBericht({ tekst, acties = [], bron = null, tijd = Date.now() / 1000 }, soort = "") {
  const body = el("div", { class: "msg-body" }, el("div", { class: "md" }, opmaak(tekst)));
  const node = el("div", { class: `msg jarvis ${soort}`.trim() },
    kop(soort === "busy-note" ? "Even geduld" : "J.A.R.V.I.S.", tijd), body);
  if (!soort) node.append(voetregel(tekst, acties, bron));
  messages.append(node);
  scrollNaarBeneden(true);
  return node;
}

// Onder een antwoord: uitgevoerde acties, waar het antwoord vandaan kwam, en kopiëren.
function voetregel(tekst, acties, bron, extra = null) {
  return el("div", { class: "msg-foot" },
    ...acties.map((a) => el("span", { class: "chip" }, icon("check"), a)),
    bron ? el("span", { class: "source-tag" }, bron === "claude" ? "via Claude" : "lokaal") : null,
    extra,
    el("button", {
      class: "icon-btn small", type: "button", title: "Antwoord kopiëren", "aria-label": "Antwoord kopiëren",
      onclick: async () => toast(await kopieer(tekst) ? "Antwoord gekopieerd." : "Kopiëren lukte niet."),
    }, icon("copy")));
}

function begroeting(tekst) {
  messages.replaceChildren();
  jarvisBericht({ tekst }, "kort");
  // Een grote orb in het midden zolang er nog niets gevraagd is (kopie van die bovenin).
  const groot = orb.cloneNode(true);
  groot.removeAttribute("id");
  groot.classList.add("orb-groot");
  groot.querySelector("defs")?.remove();  // de kleurverlopen staan al in de orb bovenin (unieke id's)
  const tip = !kanLuisteren ? "Typ je vraag hieronder."
    : spraak.wekwoord ? "Zeg \u201cJarvis, ...\u201d of typ je vraag." : "Typ je vraag of druk op de microfoon.";
  messages.append(el("div", { class: "idle", "aria-hidden": "true" }, groot,
    el("p", { class: "label" }, "Stand-by"), el("p", { class: "hint" }, tip)));
}

// -- een antwoord dat stukje voor stukje binnenkomt -----------------------------------

function nieuwAntwoord() {
  const status = el("div", { class: "msg-status", hidden: true }, el("i", { class: "spin" }), el("span"));
  const tekstVak = el("div", { class: "md" }, el("span", { class: "typing", "aria-label": "J.A.R.V.I.S. denkt na" },
    el("i"), el("i"), el("i")));
  const beelden = el("div");
  const bronnen = el("div", { class: "sources", hidden: true });
  const node = el("div", { class: "msg jarvis" }, kop("J.A.R.V.I.S.", Date.now() / 1000),
    el("div", { class: "msg-body" }, status, tekstVak, beelden, bronnen));
  messages.append(node);
  scrollNaarBeneden(true);

  const zinnen = new Zinnen();
  let tekst = "";
  let acties = [];
  const bronLinks = [];    // alle bronnen van dit antwoord ({titel, url})
  let gestreamd = false;   // kwam er tekst via de stroom binnen?
  let tekenGepland = false;
  let af = false;

  // Opnieuw opmaken; tijdens het binnenkomen met een knipperend blokje aan het eind.
  function teken(klaar) {
    tekstVak.replaceChildren(opmaak(tekst));
    if (!klaar) {
      let doel = tekstVak.lastElementChild || tekstVak;
      if (doel.tagName === "UL" || doel.tagName === "OL") doel = doel.lastElementChild || doel;
      doel.append(el("span", { class: "caret", "aria-hidden": "true" }));
    }
  }

  // Hoogstens één keer per beeldverversing tekenen, ook als er heel veel stukjes komen.
  function planTeken() {
    if (tekenGepland) return;
    tekenGepland = true;
    requestAnimationFrame(() => {
      tekenGepland = false;
      if (!af) teken(false);
      scrollNaarBeneden();
    });
  }

  function spreek(zinnenLijst) {
    if (!spraak.voorlezen) return;
    for (const zin of zinnenLijst) zeg(kaleTekst(zin));
  }

  function afronden(voet) {
    af = true;
    status.hidden = true;
    node.append(voet);
    scrollNaarBeneden();
    if (mobiel.matches && dash.dataset.view !== "jarvis") zetBadge("#badgeJarvis", 1);
  }

  return {
    get afgerond() { return af; },
    get tekst() { return tekst; },

    // Eén gebeurtenis uit de stroom verwerken (zie CONTRACT: status, tekst, actie, ...).
    gebeurtenis(naam, data) {
      if (af) return;
      if (naam === "status") {
        status.hidden = false;
        status.lastChild.textContent = data.tekst || "";
      } else if (naam === "tekst") {
        status.hidden = true;  // de stap is klaar, het antwoord komt
        gestreamd = true;
        tekst += data.tekst || "";
        spreek(zinnen.voegToe(data.tekst || ""));
        planTeken();
      } else if (naam === "actie") {
        if (data.actie && !acties.includes(data.actie)) acties.push(data.actie);
      } else if (naam === "beeld" && typeof data.foto === "string" && data.foto.startsWith("data:image/")) {
        const titel = data.titel || "Foto van de camera";
        beelden.append(el("figure", {},
          el("button", { type: "button", "aria-label": `${titel} (vergroten)`, onclick: () => toonFoto(data.foto, titel) },
            el("img", { src: data.foto, alt: titel, onload: () => scrollNaarBeneden() })),
          el("figcaption", {}, titel)));
        scrollNaarBeneden();
      } else if (naam === "bronnen" && Array.isArray(data.bronnen)) {
        this.bronnen(data.bronnen);
      } else if (naam === "klaar") {
        this.klaar(data);
      } else if (naam === "fout") {
        this.fout(data.fout || "Er ging iets mis.");
      }
    },

    // Bij elke zoekactie (of opgehaalde pagina) komen er nieuwe bronnen bij. We voegen ze
    // toe aan de bronnen die er al staan (dubbele links maar één keer), tot 8 in totaal.
    bronnen(lijst) {
      for (const b of lijst) {
        if (bronLinks.length >= 8) break;
        if (/^https?:\/\//i.test(b.url || "") && !bronLinks.some((x) => x.url === b.url)) bronLinks.push(b);
      }
      if (!bronLinks.length) return;
      bronnen.replaceChildren(el("span", { class: "label" }, "Bronnen"), el("ul", {}, ...bronLinks.map((b) => {
        let host = "";
        try { host = new URL(b.url).hostname.replace(/^www\./, ""); } catch {}
        return el("li", {}, el("a", { href: b.url, target: "_blank", rel: "noopener noreferrer" },
          icon("globe"), el("span", {}, b.titel || host), el("small", {}, host)));
      })));
      bronnen.hidden = false;
    },

    klaar(data) {
      if (af) return;
      tekst = data.antwoord || tekst || "Ik heb hier even geen antwoord op.";
      for (const a of data.acties || []) if (!acties.includes(a)) acties.push(a);
      teken(true);
      if (gestreamd) spreek([zinnen.rest()]);
      else spreek([...zinnen.voegToe(`${tekst}\n`), zinnen.rest()]);
      afronden(voetregel(tekst, acties, data.bron));
    },

    fout(bericht) {
      if (af) return;
      node.classList.add("error");
      node.querySelector(".msg-head span").textContent = "Fout";
      if (tekst) teken(true);
      tekstVak.append(el("p", {}, bericht));
      afronden(el("div"));
    },

    // Halverwege gestopt (stopknop) of de verbinding viel weg.
    onderbroken(reden) {
      if (af) return;
      teken(true);
      if (!tekst) tekstVak.replaceChildren(el("p", { class: "hint" }, reden));
      stilte();
      afronden(voetregel(tekst, acties, null, el("span", { class: "source-tag" }, reden)));
    },

    // De vraag is niet verstuurd (J.A.R.V.I.S. was nog bezig): het antwoordvak weer weghalen.
    weg() {
      af = true;
      node.remove();
    },
  };
}

// -- server-sent events lezen -----------------------------------------------------------
// De stroom bestaat uit blokjes, gescheiden door een lege regel:
//     event: tekst
//     data: {"tekst": "Ik zie "}
// Regels die met ":" beginnen zijn commentaar (de server houdt zo de verbinding open).

async function leesStroom(response, bijGebeurtenis) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Regeleinden gelijkmaken: "\r\n" en "\r" worden "\n". Eindigt het stukje op "\r",
    // dan bewaren we die nog even: de "\n" die erbij hoort kan in het volgende stukje zitten.
    let cr = "";
    if (buffer.endsWith("\r")) {
      cr = "\r";
      buffer = buffer.slice(0, -1);
    }
    buffer = buffer.replace(/\r\n?/g, "\n") + cr;
    let einde;
    while ((einde = buffer.indexOf("\n\n")) >= 0) {
      verwerkBlok(buffer.slice(0, einde), bijGebeurtenis);
      buffer = buffer.slice(einde + 2);
    }
  }
  // Wat er aan het eind nog over is (bijv. een laatste blok zonder lege regel erachter).
  buffer = (buffer + decoder.decode()).replace(/\r\n?/g, "\n");
  for (const blok of buffer.split("\n\n")) if (blok.trim()) verwerkBlok(blok, bijGebeurtenis);
}

function verwerkBlok(blok, bijGebeurtenis) {
  let naam = "message";
  const data = [];
  for (const regel of blok.split("\n")) {
    if (!regel || regel.startsWith(":")) continue;
    const dubbelepunt = regel.indexOf(":");
    const veld = dubbelepunt < 0 ? regel : regel.slice(0, dubbelepunt);
    let waarde = dubbelepunt < 0 ? "" : regel.slice(dubbelepunt + 1);
    if (waarde.startsWith(" ")) waarde = waarde.slice(1);
    if (veld === "event") naam = waarde;
    else if (veld === "data") data.push(waarde);
  }
  if (!data.length) return;
  try {
    bijGebeurtenis(naam, JSON.parse(data.join("\n")));
  } catch (err) {
    console.warn("Onleesbaar stukje uit de stroom overgeslagen", err);
  }
}

// -- een vraag stellen ------------------------------------------------------------------

const BEZIG_TEKST = "J.A.R.V.I.S. is nog bezig met je vorige vraag. Even geduld en probeer het zo nog eens.";

// Na de stopknop maakt de server het vorige antwoord nog af. Een nieuwe vraag krijgt dan
// "nog bezig" (HTTP 409). Dan wachten we even en proberen het opnieuw, hoogstens zo vaak.
const WACHT_POGINGEN = 15;  // 15 keer 2 seconden = een halve minuut

async function vraag(tekst) {
  tekst = tekst.trim();
  if (!tekst) return;
  if (bezig) {
    toast("J.A.R.V.I.S. is nog bezig met een antwoord. Wacht even tot hij klaar is.");
    return;
  }
  gesprekGestart = true;
  stilte();
  zetBezig(true);
  input.value = "";
  groei();
  const vraagNode = gebruikerBericht(tekst);
  const antwoord = nieuwAntwoord();
  stopper = new AbortController();
  try {
    for (let poging = 1; ; poging++) {
      try {
        await stuurVraag(tekst, antwoord);
        break;
      } catch (err) {
        if (err.status !== 409 || poging >= WACHT_POGINGEN) throw err;
        antwoord.gebeurtenis("status", { tekst: "Even wachten: J.A.R.V.I.S. maakt eerst zijn vorige antwoord af..." });
        await wacht(2000, stopper.signal);
      }
    }
  } catch (err) {
    if (err.name === "AbortError") antwoord.onderbroken("gestopt");
    else if (err.status === 409) bezigMelding(antwoord, vraagNode, err.message, tekst);
    else antwoord.fout(`Er ging iets mis: ${err.message}`);
  } finally {
    zetBezig(false);
    stopper = null;
  }
}

// De vraag één keer naar de server sturen (met streaming als de server dat kan).
// Bij "nog bezig" (409) komt er een fout met err.status = 409.
async function stuurVraag(tekst, antwoord) {
  let response = null;
  if (functies.stream !== false) {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { ...API_HEADERS, "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ bericht: tekst }),
      signal: stopper.signal,
    });
    if (response.status === 404 || response.status === 405) {
      functies.stream = false;  // oudere server: dan maar het hele antwoord in één keer
      response = null;
    }
  }
  if (response) {
    await verwerkAntwoord(response, antwoord);
  } else {
    const r = await api("/api/chat", { method: "POST", json: { bericht: tekst }, signal: stopper.signal });
    antwoord.klaar(r);
  }
}

async function verwerkAntwoord(response, antwoord) {
  if (response.status === 401) {
    location.href = "/login";
    return;
  }
  const soort = response.headers.get("Content-Type") || "";
  if (!response.ok || !soort.includes("text/event-stream")) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 409) {
      const err = new Error(data.fout || BEZIG_TEKST);
      err.status = 409;
      throw err;
    }
    if (response.ok && data.antwoord) return antwoord.klaar(data);
    return antwoord.fout(data.fout || `Er ging iets mis (fout ${response.status}).`);
  }
  await leesStroom(response, (naam, data) => antwoord.gebeurtenis(naam, data));
  if (!antwoord.afgerond) antwoord.onderbroken("verbinding verbroken");
}

// Even wachten; de stopknop (signal) breekt het wachten meteen af.
function wacht(ms, signal) {
  return new Promise((klaar, mislukt) => {
    const timer = setTimeout(klaar, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      mislukt(new DOMException("Gestopt", "AbortError"));
    }, { once: true });
  });
}

// J.A.R.V.I.S. bleef te lang bezig met een vorige vraag (HTTP 409): vriendelijk melden,
// de vraag uit de chat halen (hij is niet verstuurd) en terugzetten in het tekstvak,
// zodat je hem zo opnieuw kunt sturen.
function bezigMelding(antwoord, vraagNode, bericht, tekst) {
  antwoord.weg();
  vraagNode.remove();
  jarvisBericht({ tekst: bericht || BEZIG_TEKST }, "busy-note");
  input.value = tekst;
  groei();
}

function zetBezig(aan) {
  bezig = aan;
  orbStaat.denkt = aan;
  orbBijwerken();
  messages.setAttribute("aria-busy", String(aan));
  sendBtn.classList.toggle("stop", aan);
  sendBtn.setAttribute("aria-label", aan ? "Stop het antwoord" : "Versturen");
  sendBtn.title = aan ? "Stop het antwoord" : "Versturen (Enter)";
  sendBtn.querySelector("use").setAttribute("href", aan ? "#i-stop" : "#i-send");
}

// -- invoer -------------------------------------------------------------------------------

// Het tekstvak groeit mee met wat je typt (tot een maximum).
function groei() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight + 2, 160)}px`;
}
input.addEventListener("input", groei);

input.addEventListener("keydown", (event) => {
  // Enter = versturen, Shift+Enter = nieuwe regel (en niet tijdens het kiezen van tekens)
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!bezig) vraag(input.value);
  }
});

$("#chatForm").addEventListener("submit", (event) => {
  event.preventDefault();
  if (bezig) stopper?.abort();
  else vraag(input.value);
});

$("#chatReset").addEventListener("click", async () => {
  stopper?.abort();
  stilte();
  await api("/api/chat/reset", { method: "POST", json: {} }).catch(() => {});
  begroeting("Nieuw gesprek. Waarmee kan ik helpen?");
  input.focus();
});

// -- geschiedenis: het gesprek blijft staan na herladen -----------------------------------

let geschiedenisGeladen = false;

async function laadGeschiedenis() {
  if (geschiedenisGeladen || !functies.bekend) return;
  geschiedenisGeladen = true;
  if (!functies.stream) return;  // oudere server: geen geschiedenis
  try {
    const { berichten } = await api("/api/chat/geschiedenis");
    if (!berichten || !berichten.length || gesprekGestart) return;
    messages.replaceChildren();
    for (const b of berichten) {
      if (b.rol === "gebruiker") gebruikerBericht(b.tekst, b.tijd);
      else jarvisBericht({ tekst: b.tekst, acties: b.acties || [], tijd: b.tijd });
    }
    scrollNaarBeneden(true);
  } catch {
    // geen geschiedenis is niet erg: dan begint het gesprek gewoon opnieuw
  }
}

// -- voorstellen die passen bij wat de camera ziet ---------------------------------------

let voorstelSleutel = "";
let voorstelTijd = 0;

function voorstellen(s) {
  const lijst = ["Wat zie je?"];
  const onbekend = s.gezichten.some((f) => !f.naam);
  const bekend = s.gezichten.find((f) => f.naam);
  const persoon = s.objecten.some((o) => o.label === "person") || s.gezichten.length > 0;
  const ding = s.objecten.find((o) => o.label !== "person");
  if (s.zoom.actief) lijst.push("Zoom uit");
  if (onbekend) lijst.push("Wie is die onbekende persoon?");
  else if (bekend) lijst.push(`Wat doet ${bekend.naam}?`);
  if (!s.zoom.actief && persoon) lijst.push("Zoom in op de persoon");
  if (!s.zoom.actief && ding) lijst.push(`Zoom in op de ${ding.naam}`);
  if (functies.activiteit) lijst.push(persoon ? "Waarschuw me als er nog iemand komt" : "Waarschuw me als er iemand komt");
  if (!persoon && !ding) lijst.push("Wie is er in beeld?");
  return [...new Set(lijst)].slice(0, 4);
}

function toonVoorstellen(s) {
  const box = $("#suggestions");
  const lijst = voorstellen(s);
  const sleutel = lijst.join("|");
  // Niet te vaak wisselen, en niet terwijl je er met de muis of het toetsenbord op zit.
  if (sleutel === voorstelSleutel || Date.now() - voorstelTijd < 3000) return;
  if (inGebruik(box)) return;
  voorstelSleutel = sleutel;
  voorstelTijd = Date.now();
  box.replaceChildren(...lijst.map((t) => el("button", { type: "button" }, t)));
}

$("#suggestions").addEventListener("click", (event) => {
  const knop = event.target.closest("button");
  if (!knop) return;
  vraag(knop.textContent);
  // Met een muis meteen verder kunnen typen. (Op een telefoon niet: dan springt het toetsenbord open.)
  if (echteMuis.matches) input.focus();
});

// -- AI-modus en de knop "sleutel instellen" ----------------------------------------------

function toonModus(s) {
  const claude = s.ai === "claude";
  const mode = $("#aiMode");
  const model = s.ai_info && s.ai_info.model;
  mode.textContent = claude ? `Claude${model ? ` · ${model}` : ""}` : "Lokale modus";
  mode.className = `mode ${claude ? "claude" : "lokaal"}`;
  let weg = false;
  try { weg = sessionStorage.getItem("cta-weg") === "1"; } catch {}
  $("#aiCta").hidden = claude || weg;
  $("#ctaLink").hidden = !functies.instellingen;
  $("#ctaEnv").hidden = !!functies.instellingen;
}

$("#ctaClose").addEventListener("click", () => {
  $("#aiCta").hidden = true;
  try { sessionStorage.setItem("cta-weg", "1"); } catch {}
});

// Op de telefoon: bij het openen van het J.A.R.V.I.S.-tabblad naar het nieuwste bericht.
seintjes.addEventListener("view", (e) => {
  if (e.detail === "jarvis") scrollNaarBeneden();
});

seintjes.addEventListener("status", (e) => {
  laadGeschiedenis();
  toonVoorstellen(e.detail);
  toonModus(e.detail);
});

// -- spraak: microfoon, wekwoord, voorlezen ------------------------------------------------

const micNote = $("#micNote");

function toonMicUitleg(tekst) {
  micNote.textContent = tekst;
  micNote.hidden = false;
}

luisteraar.bij = {
  tussen(tekst) {
    input.value = tekst;
    groei();
  },
  vraag(tekst) {
    if (mobiel.matches) toonView("jarvis");
    vraag(tekst);
  },
  toestand(modus) {
    micBtn.classList.toggle("listening", modus === "knop");
    micBtn.setAttribute("aria-label", modus === "knop" ? "Stop met luisteren" : "Inspreken (m)");
    orbStaat.luistert = modus === "knop" || modus === "gehoord";
    orbBijwerken();
    if (modus === "knop") micNote.hidden = true;
  },
  fout(uitleg) {
    // Nog een keer klikken terwijl de uitleg er al staat = uitleg weer weg.
    if (!micNote.hidden && micNote.textContent === uitleg) micNote.hidden = true;
    else toonMicUitleg(uitleg);
    zetSpraakKnoppen();
  },
};

if (!kanLuisteren) {
  micBtn.classList.add("unavailable");
  micBtn.querySelector("use").setAttribute("href", "#i-mic-off");
  micBtn.title = "Inspreken kan hier niet (klik voor uitleg)";
  micBtn.setAttribute("aria-label", "Inspreken kan hier niet, klik voor uitleg");
}

// Kort klikken = luisteren tot je klaar bent met praten; ingedrukt houden = praten zolang je drukt.
let drukBegin = 0;
micBtn.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) return;
  const wasAan = luisteraar.modus === "knop";
  luisteraar.knop();
  drukBegin = !wasAan && luisteraar.modus === "knop" ? Date.now() : 0;
});
micBtn.addEventListener("pointerup", () => {
  if (drukBegin && Date.now() - drukBegin > 600 && luisteraar.modus === "knop") luisteraar.rec.stop();
  drukBegin = 0;
});
micBtn.addEventListener("click", (event) => {
  if (event.detail === 0) luisteraar.knop();  // met het toetsenbord (Enter/spatie) geklikt
});

function zetWekwoord(aan) {
  if (aan && !kanLuisteren) {
    toonMicUitleg(microfoonUitleg());
    aan = false;
  }
  zetSpraak("wekwoord", aan);
  luisteraar.wekwoord(aan);
}

$("#wakeToggle").addEventListener("click", () => zetWekwoord(!spraak.wekwoord));
$("#speakToggle").addEventListener("click", () => {
  zetSpraak("voorlezen", !spraak.voorlezen);
  if (!spraak.voorlezen) stilte();
});

// Het menu met spraakinstellingen.
const voiceMenu = $("#voiceMenu");
const voiceMenuBtn = $("#voiceMenuBtn");

function menuOpen(open) {
  voiceMenu.hidden = !open;
  voiceMenuBtn.setAttribute("aria-expanded", String(open));
  if (open) vulStemmen($("#vmVoice"));
}

voiceMenuBtn.addEventListener("click", () => menuOpen(voiceMenu.hidden));
document.addEventListener("click", (event) => {
  if (!voiceMenu.hidden && !voiceMenu.contains(event.target) && !voiceMenuBtn.contains(event.target)) menuOpen(false);
});
voiceMenu.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    menuOpen(false);
    voiceMenuBtn.focus();
  }
});

function vulStemmen(select) {
  const lijst = stemmen();
  const gekozen = gekozenStem();
  select.replaceChildren(...(lijst.length
    ? lijst.map((v) => el("option", { value: v.name, selected: gekozen && v.name === gekozen.name }, `${v.name} (${v.lang})`))
    : [el("option", { value: "" }, kanVoorlezen ? "Standaardstem" : "Voorlezen kan niet in deze browser")]));
  select.disabled = !lijst.length;
}

if (kanVoorlezen) speechSynthesis.addEventListener?.("voiceschanged", () => vulStemmen($("#vmVoice")));

$("#vmSpeak").addEventListener("change", (e) => {
  zetSpraak("voorlezen", e.target.checked);
  if (!e.target.checked) stilte();
});
$("#vmVoice").addEventListener("change", (e) => zetSpraak("stem", e.target.value));
$("#vmRate").addEventListener("input", (e) => {
  zetSpraak("tempo", Number(e.target.value));
  $("#vmRateOut").textContent = `${Number(e.target.value).toFixed(1)}×`;
});
$("#vmWake").addEventListener("change", (e) => zetWekwoord(e.target.checked));
$("#vmAlerts").addEventListener("change", (e) => zetSpraak("meldingen", e.target.checked));
$("#vmBeep").addEventListener("change", (e) => {
  zetSpraak("piep", e.target.checked);
  if (e.target.checked) piep();
});

// Knoppen en schakelaars gelijk laten lopen met de instellingen.
function zetSpraakKnoppen() {
  $("#speakToggle").setAttribute("aria-pressed", String(spraak.voorlezen));
  $("#speakToggle use").setAttribute("href", spraak.voorlezen ? "#i-speaker" : "#i-speaker-off");
  $("#wakeToggle").setAttribute("aria-pressed", String(spraak.wekwoord));
  $("#wakeToggle").classList.toggle("unavailable", !kanLuisteren);
  $("#vmSpeak").checked = spraak.voorlezen;
  $("#vmSpeak").disabled = !kanVoorlezen;
  $("#vmWake").checked = spraak.wekwoord;
  $("#vmAlerts").checked = spraak.meldingen;
  $("#vmBeep").checked = spraak.piep;
  $("#vmRate").value = spraak.tempo;
  $("#vmRateOut").textContent = `${Number(spraak.tempo).toFixed(1)}×`;
  const uitleg = microfoonUitleg();
  $("#vmMicInfo").hidden = !uitleg;
  $("#vmMicInfo").textContent = uitleg;
}
seintjes.addEventListener("spraak", zetSpraakKnoppen);
zetSpraakKnoppen();

// -- sneltoetsen ------------------------------------------------------------------------------

sneltoets("/", "Vraag typen aan J.A.R.V.I.S.", () => {
  if (mobiel.matches) toonView("jarvis");
  input.focus();
});
sneltoets("m", "Inspreken (microfoon aan/uit)", () => luisteraar.knop());

// -- start ------------------------------------------------------------------------------------

begroeting("Goedendag. Ik kijk mee via de dronecamera. Vraag me wat ik zie, laat me ergens op " +
  "inzoomen of stel gewoon een vraag.");
laadGeschiedenis();
if (spraak.wekwoord && kanLuisteren) luisteraar.wekwoord(true);
