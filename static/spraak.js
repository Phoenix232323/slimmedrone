// Spraak: voorlezen (tekst naar spraak), inspreken (spraak naar tekst), het wekwoord
// "Jarvis" en een piepje bij meldingen. Alles gebeurt in de browser zelf; de keuzes
// worden alleen in deze browser bewaard (localStorage).

const SPRAAK_SLEUTELS = {
  voorlezen: "jarvis-spreekt",
  stem: "jarvis-stem",
  tempo: "jarvis-tempo",
  wekwoord: "jarvis-wekwoord",
  meldingen: "jarvis-meldingen",
  piep: "jarvis-piep",
};

const spraak = {
  voorlezen: !!bewaard(SPRAAK_SLEUTELS.voorlezen, false),
  stem: bewaard(SPRAAK_SLEUTELS.stem, ""),
  tempo: Number(bewaard(SPRAAK_SLEUTELS.tempo, 1)) || 1,
  wekwoord: !!bewaard(SPRAAK_SLEUTELS.wekwoord, false),
  meldingen: !!bewaard(SPRAAK_SLEUTELS.meldingen, false),
  piep: !!bewaard(SPRAAK_SLEUTELS.piep, false),
};

// Een spraakinstelling veranderen, bewaren en de rest van de pagina laten weten.
function zetSpraak(naam, waarde) {
  spraak[naam] = waarde;
  bewaar(SPRAAK_SLEUTELS[naam], waarde);
  seintjes.dispatchEvent(new CustomEvent("spraak", { detail: { naam, waarde } }));
}

// -- voorlezen -------------------------------------------------------------------

const kanVoorlezen = "speechSynthesis" in window;

// Alle stemmen, Nederlandse bovenaan. De lijst komt soms pas na even laden binnen.
function stemmen() {
  if (!kanVoorlezen) return [];
  return speechSynthesis.getVoices().slice().sort((a, b) =>
    (b.lang.startsWith("nl") - a.lang.startsWith("nl")) || a.name.localeCompare(b.name));
}

function gekozenStem() {
  const lijst = stemmen();
  return lijst.find((v) => v.name === spraak.stem) || lijst.find((v) => v.lang.startsWith("nl")) || null;
}

let zinnenInRij = 0;  // hoeveel zinnen er nog uitgesproken worden

function zeg(tekst) {
  tekst = String(tekst || "").replace(/\s+/g, " ").trim();
  if (!kanVoorlezen || !tekst) return;
  const zin = new SpeechSynthesisUtterance(tekst);
  const stem = gekozenStem();
  zin.lang = stem ? stem.lang : "nl-NL";
  if (stem) zin.voice = stem;
  zin.rate = spraak.tempo;
  zinnenInRij++;
  zin.onstart = () => seintjes.dispatchEvent(new CustomEvent("spreekt", { detail: true }));
  zin.onend = zin.onerror = () => {
    zinnenInRij = Math.max(0, zinnenInRij - 1);
    if (!zinnenInRij) seintjes.dispatchEvent(new CustomEvent("spreekt", { detail: false }));
  };
  speechSynthesis.speak(zin);  // de browser zet zinnen zelf achter elkaar in een rij
}

function stilte() {
  if (!kanVoorlezen) return;
  zinnenInRij = 0;
  speechSynthesis.cancel();
  seintjes.dispatchEvent(new CustomEvent("spreekt", { detail: false }));
}

function spreektNu() {
  return zinnenInRij > 0;
}

// Knipt binnenkomende tekst in hele zinnen, zodat J.A.R.V.I.S. al begint te praten
// terwijl de rest van het antwoord nog binnenkomt.
class Zinnen {
  constructor() { this.buffer = ""; }

  // Voeg een stukje tekst toe; geeft de zinnen terug die nu af zijn.
  voegToe(stukje) {
    this.buffer += stukje;
    const klaar = [];
    const einde = /[.!?…]+(?=\s)|\n/g;
    let begin = 0;
    for (let m; (m = einde.exec(this.buffer));) {
      const zin = this.buffer.slice(begin, m.index + m[0].length);
      // "1." of "bijv." alleen is nog geen zin: wacht op de rest.
      if (/[\p{L}\d]{3,}/u.test(zin) && !/^\s*\d{1,3}[.)]\s*$/.test(zin)) {
        klaar.push(zin.trim());
        begin = m.index + m[0].length;
      }
    }
    this.buffer = this.buffer.slice(begin);
    return klaar.filter(Boolean);
  }

  // Wat er nog over is (aan het eind van het antwoord).
  rest() {
    const rest = this.buffer.trim();
    this.buffer = "";
    return rest;
  }
}

// -- piepje (WebAudio) --------------------------------------------------------------
// Browsers laten pas geluid toe na een klik of toets; daarom maken we het
// geluidsapparaat pas bij de eerste klik op de pagina.

let geluid = null;

function maakGeluid() {
  if (geluid) return;
  const Audio = window.AudioContext || window.webkitAudioContext;
  if (Audio) geluid = new Audio();
}
document.addEventListener("pointerdown", maakGeluid, { once: true });
document.addEventListener("keydown", maakGeluid, { once: true });

function piep(soort = "info") {
  if (!geluid) return;
  if (geluid.state === "suspended") geluid.resume();
  const tonen = soort === "onbekend" ? [880, 880] : soort === "wacht" ? [660, 990] : [740];
  tonen.forEach((hoogte, i) => {
    const toon = geluid.createOscillator();
    const volume = geluid.createGain();
    const start = geluid.currentTime + i * 0.17;
    toon.type = "sine";
    toon.frequency.value = hoogte;
    volume.gain.setValueAtTime(0.0001, start);
    volume.gain.exponentialRampToValueAtTime(0.16, start + 0.01);
    volume.gain.exponentialRampToValueAtTime(0.0001, start + 0.12);
    toon.connect(volume).connect(geluid.destination);
    toon.start(start);
    toon.stop(start + 0.14);
  });
}

// -- inspreken (spraak naar tekst) -----------------------------------------------------
// Browsers staan de microfoon alleen toe via https of op de computer zelf (localhost).

const Herkenning = window.SpeechRecognition || window.webkitSpeechRecognition;

function microfoonUitleg() {
  if (!Herkenning) {
    return "Deze browser kan geen spraak herkennen. Gebruik Chrome of Edge op een laptop of Android-telefoon.";
  }
  if (!window.isSecureContext) {
    return "De microfoon werkt alleen via https of op de Pi zelf (http://localhost). Zet HTTPS=aan in het " +
      "bestand .env, start de app opnieuw en open de website via https://.";
  }
  return "";
}

const kanLuisteren = !microfoonUitleg();

const FOUTEN = {
  "not-allowed": "De browser mag de microfoon niet gebruiken. Klik op het slotje naast het adres en sta de microfoon toe.",
  "service-not-allowed": "De browser mag de microfoon niet gebruiken. Klik op het slotje naast het adres en sta de microfoon toe.",
  "audio-capture": "Er is geen microfoon gevonden.",
  "network": "De spraakherkenning van deze browser werkt niet (hij heeft internet en een spraakdienst nodig). " +
    "Chromium op de Raspberry Pi kan dit vaak niet; probeer Chrome op een laptop of telefoon.",
};

// "Jarvis, wat zie je?" -> "wat zie je?". Browsers verstaan de naam soms net anders.
const WEKWOORD = /\b(?:jarvis|jervis|jarviss|djarvis|tsjarvis|j\.?a\.?r\.?v\.?i\.?s\.?)\b[\s,.!?:]*(.*)$/i;

// Eén luisteraar voor de hele pagina: de browser kan maar één herkenning tegelijk.
// jarvis.js vult de functies in "bij" in (wat er moet gebeuren met wat je zegt).
const luisteraar = {
  modus: null,          // null, "knop" (je drukte op de microfoon) of "wekwoord"
  rec: null,
  wachtOpVraag: 0,      // tijdstip waarop "Jarvis" zonder vraag werd gehoord
  bij: { tussen() {}, vraag() {}, toestand() {}, fout() {} },

  // Microfoonknop: begin met luisteren (of stop als hij al luistert).
  knop() {
    if (!kanLuisteren) return this.bij.fout(microfoonUitleg());
    if (this.modus === "knop") return this.rec.stop();
    if (this.rec) this.rec.abort();  // het wekwoord even onderbreken
    stilte();  // J.A.R.V.I.S. houdt op met voorlezen: jij wilt iets zeggen
    this.start("knop");
  },

  // Het wekwoord aan- of uitzetten.
  wekwoord(aan) {
    if (aan && !kanLuisteren) return this.bij.fout(microfoonUitleg());
    if (aan && !this.modus) this.start("wekwoord");
    if (!aan && this.modus === "wekwoord") this.rec.abort();
  },

  start(modus) {
    const rec = new Herkenning();
    rec.lang = "nl-NL";
    rec.interimResults = true;
    rec.continuous = modus === "wekwoord";
    this.rec = rec;
    this.modus = modus;
    this.bij.toestand(modus);

    rec.onresult = (event) => {
      // Bij het wekwoord niet naar onze eigen stem luisteren. (Met de knop wil je zelf
      // iets zeggen; het voorlezen is dan al gestopt.)
      if (modus === "wekwoord" && spreektNu()) return;
      let tekst = "", af = false;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        tekst += event.results[i][0].transcript;
        af = af || event.results[i].isFinal;
      }
      tekst = tekst.trim();
      if (modus === "knop") {
        this.bij.tussen(tekst);
        if (af && tekst) this.bij.vraag(tekst);
      } else if (af) {
        this.verwerkWekwoord(tekst);
      } else if (WEKWOORD.test(tekst)) {
        this.bij.toestand("gehoord");
      }
    };
    rec.onerror = (event) => {
      if (FOUTEN[event.error]) {
        zetSpraak("wekwoord", false);
        this.bij.fout(FOUTEN[event.error]);
      }
    };
    rec.onend = () => {
      if (this.rec !== rec) return;  // er is al een nieuwe herkenning gestart
      this.rec = null;
      this.modus = null;
      this.bij.toestand(null);
      // Chrome stopt na een tijdje stilte vanzelf; met het wekwoord aan beginnen we opnieuw.
      if (spraak.wekwoord) setTimeout(() => { if (!this.modus && spraak.wekwoord) this.start("wekwoord"); }, 400);
    };
    try {
      rec.start();
    } catch {
      this.rec = null;
      this.modus = null;
      this.bij.toestand(null);
    }
  },

  verwerkWekwoord(tekst) {
    if (this.wachtOpVraag && Date.now() - this.wachtOpVraag < 8000) {
      this.wachtOpVraag = 0;
      this.bij.vraag(tekst);
      this.bij.toestand("wekwoord");
      return;
    }
    const m = tekst.match(WEKWOORD);
    if (!m) return;
    const vraag = m[1].trim();
    if (vraag.length > 1) {
      this.bij.vraag(vraag);
      this.bij.toestand("wekwoord");
    } else {
      this.wachtOpVraag = Date.now();  // alleen "Jarvis": de vraag komt zo
      this.bij.toestand("gehoord");
    }
  },
};
