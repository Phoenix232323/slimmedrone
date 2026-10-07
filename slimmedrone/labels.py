"""Nederlandse namen voor de 80 objectsoorten die YOLO (COCO-dataset) kent.

Per Engelse naam: (enkelvoud, meervoud, extra woorden die mensen gebruiken).
De assistent gebruikt dit om "zoom in op de tafel" te koppelen aan "dining table".
"""
import re

LABELS = {
    "person": ("persoon", "personen", ["mens", "mensen", "man", "vrouw", "iemand", "kind", "jongen", "meisje"]),
    "bicycle": ("fiets", "fietsen", []),
    "car": ("auto", "auto's", ["wagen"]),
    "motorcycle": ("motor", "motoren", ["motorfiets", "scooter"]),
    "airplane": ("vliegtuig", "vliegtuigen", []),
    "bus": ("bus", "bussen", []),
    "train": ("trein", "treinen", []),
    "truck": ("vrachtwagen", "vrachtwagens", ["truck"]),
    "boat": ("boot", "boten", ["schip"]),
    "traffic light": ("verkeerslicht", "verkeerslichten", ["stoplicht"]),
    "fire hydrant": ("brandkraan", "brandkranen", []),
    "stop sign": ("stopbord", "stopborden", []),
    "parking meter": ("parkeermeter", "parkeermeters", []),
    "bench": ("bankje", "bankjes", ["parkbank"]),
    "bird": ("vogel", "vogels", []),
    "cat": ("kat", "katten", ["poes"]),
    "dog": ("hond", "honden", ["hondje"]),
    "horse": ("paard", "paarden", []),
    "sheep": ("schaap", "schapen", []),
    "cow": ("koe", "koeien", []),
    "elephant": ("olifant", "olifanten", []),
    "bear": ("beer", "beren", []),
    "zebra": ("zebra", "zebra's", []),
    "giraffe": ("giraf", "giraffen", []),
    "backpack": ("rugzak", "rugzakken", ["rugtas"]),
    "umbrella": ("paraplu", "paraplu's", []),
    "handbag": ("handtas", "handtassen", ["tas"]),
    "tie": ("stropdas", "stropdassen", []),
    "suitcase": ("koffer", "koffers", []),
    "frisbee": ("frisbee", "frisbees", []),
    "skis": ("ski", "ski's", []),
    "snowboard": ("snowboard", "snowboards", []),
    "sports ball": ("bal", "ballen", ["voetbal"]),
    "kite": ("vlieger", "vliegers", []),
    "baseball bat": ("honkbalknuppel", "honkbalknuppels", []),
    "baseball glove": ("honkbalhandschoen", "honkbalhandschoenen", []),
    "skateboard": ("skateboard", "skateboards", []),
    "surfboard": ("surfplank", "surfplanken", []),
    "tennis racket": ("tennisracket", "tennisrackets", []),
    "bottle": ("fles", "flessen", ["flesje"]),
    "wine glass": ("wijnglas", "wijnglazen", ["glas"]),
    "cup": ("kopje", "kopjes", ["beker", "mok"]),
    "fork": ("vork", "vorken", []),
    "knife": ("mes", "messen", []),
    "spoon": ("lepel", "lepels", []),
    "bowl": ("kom", "kommen", []),
    "banana": ("banaan", "bananen", []),
    "apple": ("appel", "appels", []),
    "sandwich": ("broodje", "broodjes", []),
    "orange": ("sinaasappel", "sinaasappels", []),
    "broccoli": ("broccoli", "broccoli", []),
    "carrot": ("wortel", "wortels", []),
    "hot dog": ("hotdog", "hotdogs", []),
    "pizza": ("pizza", "pizza's", []),
    "donut": ("donut", "donuts", []),
    "cake": ("taart", "taarten", []),
    "chair": ("stoel", "stoelen", []),
    "couch": ("bank", "banken", ["sofa"]),
    "potted plant": ("plant", "planten", []),
    "bed": ("bed", "bedden", []),
    "dining table": ("tafel", "tafels", ["bureau"]),
    "toilet": ("toilet", "toiletten", ["wc"]),
    "tv": ("tv", "tv's", ["televisie", "scherm", "monitor"]),
    "laptop": ("laptop", "laptops", []),
    "mouse": ("muis", "muizen", []),
    "remote": ("afstandsbediening", "afstandsbedieningen", []),
    "keyboard": ("toetsenbord", "toetsenborden", []),
    "cell phone": ("telefoon", "telefoons", ["mobiel", "gsm", "smartphone"]),
    "microwave": ("magnetron", "magnetrons", []),
    "oven": ("oven", "ovens", []),
    "toaster": ("broodrooster", "broodroosters", []),
    "sink": ("gootsteen", "gootstenen", ["wasbak"]),
    "refrigerator": ("koelkast", "koelkasten", []),
    "book": ("boek", "boeken", []),
    "clock": ("klok", "klokken", []),
    "vase": ("vaas", "vazen", []),
    "scissors": ("schaar", "scharen", []),
    "teddy bear": ("knuffelbeer", "knuffelberen", ["knuffel"]),
    "hair drier": ("haardroger", "haardrogers", ["fohn", "föhn"]),
    "toothbrush": ("tandenborstel", "tandenborstels", []),
}

# Elk woord (Nederlands, meervoud, synoniem of Engels) -> Engelse COCO-naam.
_LOOKUP = {}
for _english, (_single, _plural, _extra) in LABELS.items():
    for _word in [_english, _single, _plural, *_extra]:
        _LOOKUP.setdefault(_word.lower(), _english)


def dutch(label: str) -> str:
    """'dining table' -> 'tafel'. Onbekende namen blijven zoals ze zijn."""
    return LABELS.get(label, (label,))[0]


def dutch_count(label: str, count: int) -> str:
    """('dog', 2) -> '2 honden'."""
    single, plural, _ = LABELS.get(label, (label, label, []))
    return f"{count} {single if count == 1 else plural}"


def find_label(text: str):
    """Zoek in een zin naar een objectsoort. 'zoom in op de tafel' -> 'dining table'."""
    words = re.findall(r"[\w']+", text.lower())
    # Eerst woordparen ("dining table", "cell phone"), daarna losse woorden.
    for size in (2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase in _LOOKUP:
                return _LOOKUP[phrase]
    return None
