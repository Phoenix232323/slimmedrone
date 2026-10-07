"""J.A.R.V.I.S. - de assistent in de chat rechts op het dashboard.

Met een ANTHROPIC_API_KEY in .env gebruikt hij Claude. Claude krijgt bij elke
vraag het actuele camerabeeld plus wat de objecten- en gezichtsherkenning
gevonden hebben, en kan zelf in- en uitzoomen via "tools" (functies die wij
hier uitvoeren als Claude erom vraagt).

Zonder sleutel (of als internet wegvalt) gebruikt hij een eenvoudige lokale
versie die commando's begrijpt als "wat zie je", "wie is er", "hoeveel
personen", "zoom in op de tafel" en "zoom uit".
"""
import base64
import logging
import re
import threading
import time
from collections import Counter

import cv2

from .labels import LABELS, dutch, dutch_count, find_label
from .zoom import crop, fit_rect

log = logging.getLogger(__name__)

HISTORY_TURNS = 6  # zoveel eerdere vragen en antwoorden onthoudt hij per gebruiker

SYSTEM_PROMPT = """Je bent J.A.R.V.I.S., de AI-assistent van de SlimmeDrone: een schoolproject \
(een prototype voor observatie) van Johan, Jaiden en Safouan. Je kijkt mee via de camera van \
de drone en helpt het team op het grondstation.

Bij elke vraag krijg je:
- afbeelding 1: het volledige, actuele camerabeeld;
- soms afbeelding 2: de huidige ingezoomde weergave;
- een lijst met objecten uit de objectdetectie (#1, #2, ...) en gezichten uit de lokale \
gezichtsherkenning (G1, G2, ...), met hun plek in het beeld.

Zo werk je:
- Antwoord altijd in het Nederlands, kort en duidelijk (meestal 1 tot 3 zinnen), beleefd en \
met een vleugje Jarvis-stijl.
- Beschrijf wat je echt ziet. Twijfel je, zeg dat dan. De detectielijst kan fouten bevatten; \
kijk zelf ook naar het beeld.
- Namen van personen haal je alleen uit de gezichtsherkenning. Herken zelf niemand aan het \
uiterlijk en raad geen identiteit. Een gezicht dat de herkenning onbekend noemt, is voor jou \
ook onbekend.
- Wil de gebruiker inzoomen: gebruik zoom_to_object als het object in de lijst staat, \
zoom_to_face voor een gezicht, en anders zoom_to_region met een kader op afbeelding 1. \
Uitzoomen doe je met zoom_out. Na het inzoomen krijg je het ingezoomde beeld te zien; \
vertel kort wat je daar ziet.
- Je kunt alleen kijken en zoomen. Je bestuurt de drone niet en het systeem grijpt nergens in. \
Vraagt iemand iets anders, leg dat dan vriendelijk uit."""


def _tool(name, description, properties, required):
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": properties,
                         "required": required, "additionalProperties": False},
    }


TOOLS = [
    _tool("zoom_to_object",
          "Zoom digitaal in op een object uit de objectdetectie-lijst. Het beeld volgt dat "
          "object daarna automatisch. Je krijgt het ingezoomde beeld terug.",
          {"object_id": {"type": "integer", "description": "Het nummer uit de objectlijst, bijv. 2 voor #2."}},
          ["object_id"]),
    _tool("zoom_to_face",
          "Zoom digitaal in op een gezicht uit de gezichtsherkenning-lijst. Je krijgt het "
          "ingezoomde beeld terug.",
          {"face_id": {"type": "integer", "description": "Het nummer uit de gezichtenlijst, bijv. 1 voor G1."}},
          ["face_id"]),
    _tool("zoom_to_region",
          "Zoom in op een stuk van afbeelding 1 dat niet in de lijsten staat. Geef het kader als "
          "fracties van de breedte en hoogte van afbeelding 1, van 0 (links/boven) tot 1 "
          "(rechts/onder). Je krijgt het ingezoomde beeld terug.",
          {"x1": {"type": "number", "description": "linkerkant, 0-1"},
           "y1": {"type": "number", "description": "bovenkant, 0-1"},
           "x2": {"type": "number", "description": "rechterkant, 0-1"},
           "y2": {"type": "number", "description": "onderkant, 0-1"}},
          ["x1", "y1", "x2", "y2"]),
    _tool("zoom_out", "Zoom uit naar het volledige camerabeeld.", {}, []),
]

HELP = ("Zonder Claude-sleutel begrijp ik deze commando's:\n"
        "- wat zie je?\n- wie is er?\n- hoeveel personen zie je?\n- zie je een hond?\n"
        "- zoom in op de tafel / zoom in op Johan\n- zoom uit")


class Assistant:
    def __init__(self, cfg, pipeline):
        self.pipeline = pipeline
        self.model = cfg.claude_model
        self.effort = cfg.claude_effort
        self.client = None
        self._anthropic = None
        self._histories = {}
        self._lock = threading.Lock()
        if cfg.ai_enabled:
            try:
                import anthropic

                self._anthropic = anthropic
                self.client = anthropic.Anthropic(timeout=90.0)
                log.info("Jarvis gebruikt Claude (%s)", self.model)
            except Exception as exc:
                log.warning("Claude niet beschikbaar (%s); Jarvis gebruikt de lokale versie", exc)

    @property
    def mode(self) -> str:
        return "claude" if self.client else "lokaal"

    def reset(self, user: str):
        with self._lock:
            self._histories.pop(user, None)

    def ask(self, user: str, question: str) -> dict:
        snap = self.pipeline.snapshot()
        if snap.frame is None:
            return {"antwoord": "Ik heb nog geen camerabeeld binnen. Controleer de camera.",
                    "acties": [], "bron": "lokaal"}
        source = "lokaal"
        if self.client:
            try:
                reply, actions = self._ask_claude(user, question, snap)
                source = "claude"
            except Exception as exc:
                problem = self._explain_error(exc)
                local_reply, actions = self._local_answer(question, snap)
                reply = f"{problem} Ik antwoord nu met mijn lokale versie:\n\n{local_reply}"
        else:
            reply, actions = self._local_answer(question, snap)
        with self._lock:
            history = self._histories.setdefault(user, [])
            remembered = reply + (f"\n(Uitgevoerd: {', '.join(actions)})" if actions else "")
            history.append((question, remembered))
            del history[:-HISTORY_TURNS]
        return {"antwoord": reply, "acties": actions, "bron": source}

    # -- Claude --------------------------------------------------------------

    def _ask_claude(self, user, question, snap):
        with self._lock:
            history = list(self._histories.get(user, []))

        # Eerdere vragen en antwoorden gaan als gewone tekst mee; alleen de
        # nieuwe vraag krijgt de camerabeelden erbij.
        messages = []
        for old_question, old_answer in history:
            messages.append({"role": "user", "content": old_question})
            messages.append({"role": "assistant", "content": old_answer})

        content = [{"type": "text", "text": "Afbeelding 1 - volledig camerabeeld:"},
                   _image_block(snap.frame)]
        if snap.zoom_rect:
            content += [{"type": "text", "text": "Afbeelding 2 - huidige ingezoomde weergave:"},
                        _image_block(crop(snap.frame, snap.zoom_rect))]
        content.append({"type": "text",
                        "text": f"{_scene_text(snap, self.pipeline.zoom.status())}\n\n"
                                f"Vraag van {user}: {question}"})
        messages.append({"role": "user", "content": content})

        actions = []
        for _ in range(4):  # hooguit een paar keer zoomen per vraag
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                # Weigert het model iets onterecht, dan probeert de API het
                # automatisch opnieuw met een ander passend model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                return "Daar kan ik je helaas niet mee helpen.", actions

            tool_calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_calls:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                return text or "Ik heb hier even geen antwoord op.", actions

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for call in tool_calls:
                result, is_error, action = self._run_tool(call.name, call.input, snap)
                if action:
                    actions.append(action)
                entry = {"type": "tool_result", "tool_use_id": call.id, "content": result}
                if is_error:
                    entry["is_error"] = True
                results.append(entry)
            messages.append({"role": "user", "content": results})
        return "Ik ben er even niet uitgekomen. Probeer het nog eens?", actions

    def _run_tool(self, name, args, snap):
        """Voert een tool uit. Geeft (inhoud voor Claude, is_fout, actie voor de website)."""
        zoom = self.pipeline.zoom
        h, w = snap.frame.shape[:2]

        def zoomed(text, box):
            image = crop(snap.frame, fit_rect(box, w, h))
            return [{"type": "text", "text": text}, _image_block(image)]

        if name == "zoom_to_object":
            i = args.get("object_id")
            if not isinstance(i, int) or not 1 <= i <= len(snap.detections):
                return f"Object #{i} bestaat niet.", True, None
            d = snap.detections[i - 1]
            zoom.follow(d.label, d.box, d.naam)
            return zoomed(f"Ingezoomd op #{i} {d.naam}. Dit is het ingezoomde beeld:", d.box), False, f"zoom: {d.naam}"

        if name == "zoom_to_face":
            i = args.get("face_id")
            if not isinstance(i, int) or not 1 <= i <= len(snap.faces):
                return f"Gezicht G{i} bestaat niet.", True, None
            f = snap.faces[i - 1]
            who = f.name or "onbekend gezicht"
            zoom.region(f.box, who)
            return zoomed(f"Ingezoomd op G{i} ({who}). Dit is het ingezoomde beeld:", f.box), False, f"zoom: {who}"

        if name == "zoom_to_region":
            try:
                xs = sorted(min(1.0, max(0.0, float(args[k]))) for k in ("x1", "x2"))
                ys = sorted(min(1.0, max(0.0, float(args[k]))) for k in ("y1", "y2"))
            except (KeyError, TypeError, ValueError):
                return "Ongeldig kader.", True, None
            if xs[1] - xs[0] < 0.02 or ys[1] - ys[0] < 0.02:
                return "Dat kader is te klein om op in te zoomen.", True, None
            box = (xs[0] * w, ys[0] * h, xs[1] * w, ys[1] * h)
            zoom.region(box)
            return zoomed("Ingezoomd. Dit is het ingezoomde beeld:", box), False, "zoom: gebied"

        if name == "zoom_out":
            zoom.reset()
            return "Uitgezoomd naar het volledige beeld.", False, "zoom uit"

        return f"Onbekende tool: {name}", True, None

    def _explain_error(self, exc) -> str:
        anthropic = self._anthropic
        log.warning("Claude-fout: %s", exc)
        if isinstance(exc, anthropic.AuthenticationError):
            return "Mijn API-sleutel klopt niet (controleer ANTHROPIC_API_KEY in .env)."
        if isinstance(exc, anthropic.PermissionDeniedError):
            return "Deze API-sleutel heeft geen toegang tot dit model."
        if isinstance(exc, anthropic.NotFoundError):
            return f"Het model '{self.model}' is niet gevonden (controleer CLAUDE_MODEL in .env)."
        if isinstance(exc, anthropic.RateLimitError):
            return "Ik krijg even te veel vragen tegelijk."
        if isinstance(exc, anthropic.BadRequestError):
            return f"Claude gaf een foutmelding: {exc.message}"
        if isinstance(exc, anthropic.APIStatusError):
            return f"Claude is even niet bereikbaar (fout {exc.status_code})."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Ik kan Claude niet bereiken. Is er internet?"
        log.exception("Onverwachte fout in de assistent")
        return "Er ging iets mis met Claude."

    # -- lokale versie (zonder internet) -------------------------------------

    def _local_answer(self, question, snap):
        q = question.lower()
        words = set(re.findall(r"[\w']+", q))
        zoom = self.pipeline.zoom

        if re.search(r"zoom\w*\s+(\w+\s+)?uit\b|uitzoomen|zoom out|reset|normaal beeld|volledig beeld", q):
            zoom.reset()
            return "Uitgezoomd naar het volledige beeld.", ["zoom uit"]

        if words & {"zoom", "inzoomen", "zoomen", "dichterbij", "vergroot", "vergroten"}:
            for f in snap.faces:
                if f.name and f.name.lower() in q:
                    zoom.region(f.box, f.name)
                    return f"Ingezoomd op {f.name}.", [f"zoom: {f.name}"]
            label = find_label(q)
            if label:
                matches = [d for d in snap.detections if d.label == label]
                if not matches:
                    return f"Ik zie op dit moment geen {dutch(label)}.", []
                d = matches[0]
                zoom.follow(d.label, d.box, d.naam)
                return f"Ingezoomd op {d.naam}. Ik blijf het volgen.", [f"zoom: {d.naam}"]
            zoom.zoom_at(0.5, 0.5)
            return "Ik zoom in op het midden van het beeld.", ["zoom: midden"]

        if "wie" in words:
            known = sorted({f.name for f in snap.faces if f.name})
            unknown = sum(1 for f in snap.faces if not f.name)
            if not snap.faces:
                return "Ik zie op dit moment geen gezichten.", []
            parts = []
            if known:
                parts.append(f"Ik herken {_join(known)}.")
            if unknown:
                parts.append(_unknown_text(unknown))
            return " ".join(parts), []

        label = find_label(q)
        if "hoeveel" in words:
            label = label or "person"
            n = sum(1 for d in snap.detections if d.label == label)
            if n == 0:
                return f"Ik zie geen {LABELS[label][1]}.", []
            return f"Ik zie {dutch_count(label, n)}.", []

        if label and ("zie" in words or "is er" in q or "zijn er" in q):
            n = sum(1 for d in snap.detections if d.label == label)
            if n:
                return f"Ja, ik zie {dutch_count(label, n)}.", []
            return f"Nee, ik zie geen {dutch(label)}.", []

        if any(p in q for p in ("wat zie", "wat is er", "beschrijf", "wat gebeurt", "zie je",
                                "wat staat", "overzicht", "status", "situatie")):
            return _summary(snap), []

        if words & {"help", "hulp", "commando", "commando's"} or "wat kun" in q:
            return HELP, []

        return "Dat begrijp ik nog niet. " + HELP, []


# -- hulpfuncties -------------------------------------------------------------

def _image_block(image):
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(buf).decode()}}


def _scene_text(snap, zoom_status) -> str:
    h, w = snap.frame.shape[:2]

    def where(b):
        return f"x {b[0] / w:.2f}-{b[2] / w:.2f}, y {b[1] / h:.2f}-{b[3] / h:.2f}"

    lines = [f"Tijd: {time.strftime('%H:%M:%S', time.localtime(snap.time))}", "", "Objectdetectie:"]
    if snap.detections:
        for i, d in enumerate(snap.detections, 1):
            lines.append(f"#{i} {d.naam} ({d.label}), {d.confidence:.0%}, kader {where(d.box)}")
    else:
        lines.append("(niets gedetecteerd)")
    lines += ["", "Gezichtsherkenning:"]
    if snap.faces:
        for i, f in enumerate(snap.faces, 1):
            who = f"{f.name} (overeenkomst {f.similarity:.2f})" if f.name else "onbekend gezicht"
            lines.append(f"G{i} {who}, kader {where(f.box)}")
    else:
        lines.append("(geen gezichten)")
    if zoom_status.get("actief"):
        lines += ["", f"Zoom: ingezoomd op {zoom_status['naam']} ({zoom_status['factor']}x)"]
    else:
        lines += ["", "Zoom: uit (volledig beeld)"]
    return "\n".join(lines)


def _join(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " en " + items[-1]


def _unknown_text(n):
    return "Er is 1 onbekend gezicht." if n == 1 else f"Er zijn {n} onbekende gezichten."


def _summary(snap) -> str:
    counts = Counter(d.label for d in snap.detections)
    if not counts and not snap.faces:
        return "Ik zie op dit moment niets bijzonders."
    parts = []
    if counts:
        parts.append(f"Ik zie {_join(dutch_count(label, n) for label, n in counts.most_common())}.")
    known = sorted({f.name for f in snap.faces if f.name})
    unknown = sum(1 for f in snap.faces if not f.name)
    if known:
        parts.append(f"Ik herken {_join(known)}.")
    if unknown:
        parts.append(_unknown_text(unknown))
    return " ".join(parts)
