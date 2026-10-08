// Veilige opmaak voor de antwoorden van J.A.R.V.I.S. (een klein stukje "markdown").
//
// Ondersteund:  **vet**, *schuin*, `code`, lijstjes met "- " of "1. ", [tekst](https://...)
//               en losse links (https://...). Een regel met "# " wordt een vette kop.
//
// Belangrijk: we bouwen alles met createElement en tekst-knopen, NOOIT met innerHTML.
// Zo kan tekst uit een antwoord nooit als HTML of script uitgevoerd worden, wat er ook in staat.

// Herkent één stukje opmaak binnen een regel. De volgorde telt: code eerst,
// zodat ** of * binnen `code` gewoon tekst blijft.
const INLINE = new RegExp([
  "`([^`\\n]+)`",                                   // 1: `code`
  "\\*\\*([^\\n]+?)\\*\\*",                         // 2: **vet**
  "\\*([^*\\s](?:[^*\\n]*?[^*\\s])?)\\*",           // 3: *schuin*
  "\\[([^\\]\\n]+)\\]\\((https?:\\/\\/[^\\s)]+)\\)", // 4 + 5: [tekst](url)
  "(https?:\\/\\/[^\\s<>()\"']+[^\\s<>()\"'.,;:!?])", // 6: losse link
].join("|"), "g");

function maakLink(tekst, url) {
  // Alleen http(s): een "javascript:"-link wordt dus nooit klikbaar.
  if (!/^https?:\/\//i.test(url)) return document.createTextNode(tekst);
  const a = document.createElement("a");
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.textContent = tekst;
  return a;
}

// Eén regel tekst -> lijst met knopen (tekst, <strong>, <em>, <code>, <a>).
function opmaakRegel(tekst) {
  const nodes = [];
  const zoeker = new RegExp(INLINE.source, "g");  // eigen kopie: deze functie roept zichzelf soms aan
  let last = 0;
  for (let m; (m = zoeker.exec(tekst));) {
    if (m.index > last) nodes.push(document.createTextNode(tekst.slice(last, m.index)));
    if (m[1] !== undefined) {
      const code = document.createElement("code");
      code.textContent = m[1];
      nodes.push(code);
    } else if (m[2] !== undefined) {
      const strong = document.createElement("strong");
      strong.append(...opmaakRegel(m[2]));  // binnen vet mag ook schuin of een link
      nodes.push(strong);
    } else if (m[3] !== undefined) {
      const em = document.createElement("em");
      em.textContent = m[3];
      nodes.push(em);
    } else if (m[4] !== undefined) {
      nodes.push(maakLink(m[4], m[5]));
    } else if (m[6] !== undefined) {
      nodes.push(maakLink(m[6], m[6]));
    }
    last = m.index + m[0].length;
  }
  if (last < tekst.length) nodes.push(document.createTextNode(tekst.slice(last)));
  return nodes;
}

// Hele tekst -> fragment met alinea's (<p>) en lijstjes (<ul>/<ol>).
function opmaak(tekst) {
  const frag = document.createDocumentFragment();
  let alinea = null, lijst = null, lijstSoort = null;
  for (const regel of String(tekst).replace(/\r\n?/g, "\n").split("\n")) {
    const bullet = regel.match(/^\s*[-*•]\s+(.*)$/);
    const nummer = regel.match(/^\s*(\d{1,3})[.)]\s+(.*)$/);
    const kop = regel.match(/^\s*#{1,4}\s+(.*)$/);
    if (bullet || nummer) {
      const soort = bullet ? "ul" : "ol";
      if (!lijst || lijstSoort !== soort) {
        lijst = document.createElement(soort);
        if (nummer && nummer[1] !== "1") lijst.start = Number(nummer[1]);
        lijstSoort = soort;
        frag.append(lijst);
      }
      const li = document.createElement("li");
      li.append(...opmaakRegel(bullet ? bullet[1] : nummer[2]));
      lijst.append(li);
      alinea = null;
    } else if (!regel.trim()) {
      alinea = null;
      lijst = null;
    } else if (kop) {
      const p = document.createElement("p");
      p.className = "md-h";
      p.append(...opmaakRegel(kop[1]));
      frag.append(p);
      alinea = null;
      lijst = null;
    } else {
      lijst = null;
      if (alinea) alinea.append(document.createElement("br"));
      else {
        alinea = document.createElement("p");
        frag.append(alinea);
      }
      alinea.append(...opmaakRegel(regel));
    }
  }
  return frag;
}

// Dezelfde tekst zonder opmaaktekens, om voor te lezen.
function kaleTekst(tekst) {
  return String(tekst)
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/^\s*(?:[-*•]|\d{1,3}[.)]|#{1,4})\s+/gm, "")
    .replace(/[ \t]+/g, " ");
}
