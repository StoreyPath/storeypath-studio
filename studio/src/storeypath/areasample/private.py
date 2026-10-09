"""What of a text is private, for an area sample: found by rule, shown to the person
making the sample (each finding switchable), and replaced by a placeholder wherever it
is written, in the drawing's part, the JSON files and the pictures alike.

The drawing Studio keeps was cleaned when it was added (privacy.py), unless a person
kept something; a sample is looked through again, more strictly, because it leaves the
organization:

- people's names with a title (Mr, Dr, Eng, Sheikh, السيد…), as privacy.py finds them;
- phone numbers (a country code with + or 00, eight digits in two fours), extensions, emails, web
  addresses, permit, plot, licence and ID numbers;
- words Studio does not know as a room's or a plan's word (a name without a title, a
  company, a place): *maybe a name*, taken out unless the person keeps it;
- the project's, site's, building's and floor's names and codes, and the site's
  address: always.

Room words stay (OFFICE, MAJLIS, SALAH, WC…), and so do room numbers and codes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..privacy import PRIVATE_TEXT, ROOM_SHORT, TITLED_NAME

PLACEHOLDERS = {
    "name": "[NAME]", "maybe a name": "[NAME?]", "phone": "[PHONE]", "email": "[EMAIL]", "extension": "[EXT]",
    "web": "[WEB]", "id number": "[ID-NO]", "contact": "[CONTACT]", "chosen": "[TEXT]",
    "project": "[PROJECT]", "site": "[SITE]", "building": "[BUILDING]", "floor": "[FLOOR]", "address": "[ADDRESS]",
}
ALWAYS = ("project", "site", "building", "floor", "address")  # never kept, whatever is chosen
NAMED = ("name", "phone", "email", "web", "extension", "id number", "contact")  # taken out of layers' names too
# what the JSON files hold of Studio's own words (types, who decided, rules, units, local IDs)
# or of names already scrubbed as names (layers): never scrubbed as texts
STUDIO_KEYS = {"type", "kind", "decided_by", "type_source", "found_as", "outline", "a", "b", "source", "category",
               "units", "header_units", "units_chosen", "profile", "method", "rule", "id", "parent", "zones", "in",
               "label_of", "room", "connects", "layer", "roles", "read_as", "rooms_only", "asked", "model",
               "origin", "replayed_with"}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
WEB = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|net|org|gov|edu|info|biz|qa|ae|sa|kw|om|bh|uk|eu)"
                 r"(?:\.[a-z]{2})?\b")
PHONE = re.compile(r"(?i)(?:\b(?:tel|phone|mob(?:ile)?|fax|cell)\b\.?\s*[:.]?\s*)?(?:(?:\+|\b00)\d[\d\s().-]{6,}\d"
                   r"|\b\d{4}[\s-]?\d{4}\b|\b(?:tel|phone|mob(?:ile)?|fax|cell)\b\.?\s*[:.]?\s*\d[\d\s().-]{4,}\d)")
EXTENSION = re.compile(r"(?i)\b(?:ext|extn|extension)\b\.?\s*[:.#]?\s*\d{2,6}\b")
ID_NUMBER = re.compile(r"(?i)\b(?:permit|licen[cs]e|plot|parcel|makani|reg(?:istration)?|qid|c\.?r\.?)\b\.?\s*"
                       r"(?:no|number|#)?\.?\s*[:.]?\s*[\w/-]*\d[\w/-]*")
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
NAME_LINKS = {"al", "el", "bin", "bint", "ibn", "abu", "abd", "bu", "de", "da", "di", "van", "von", "le", "la", "ال"}
MIN_UNKNOWN = 3  # letters: shorter words (tags, abbreviations) are never taken for names
TITLES = {"mr", "mrs", "ms", "miss", "dr", "eng", "engr", "sheikh", "shaikh", "السيد", "السيدة", "الشيخ", "المهندس",
          "الدكتور"}

# Words of plans and of rooms that are not names (with the words Studio's room types are
# described by, privacy._names_a_room): what is left of a text after them may be a name.
PLAN_WORDS = set("""
office offices room rooms rm area areas zone hall halls lobby reception foyer entrance entry exit corridor corr passage
passageway hallway lift lifts elevator elevators elev escalator escalators stair stairs staircase stairwell ramp ramps
landing toilet toilets wc washroom washrooms restroom restrooms lavatory lav bathroom bath shower showers ablution
ablutions wudu wudhu kitchen kitchenette pantry cafe cafeteria canteen dining dinning majlis majles salah sala saleh
salon living sitting family lounge guest guests visitor visitors waiting meeting meetings conference conf board boardroom
huddle training classroom class lecture auditorium theatre theater library study studio gym fitness clinic medical first
aid nurse prayer musalla mosque store stores storage storeroom archive archives records file files copy print printing
printer server servers data comms communication communications telecom tel elec electrical mech mechanical plumbing
plant pump pumps generator transformer substation switch switchgear ups battery batteries fire fighting sprinkler
control security guard guards cctv bms janitor cleaner cleaners cleaning housekeeping laundry ironing dress dressing
locker lockers changing change maid maids driver drivers staff employees employee manager managers director directors
general executive executives assistant secretary admin administration hr finance accounts legal it ict procurement
marketing sales operations open plan workstation workstations workspace work desk desks hot cubicle cubicles pod pods
focus quiet phone booth booths call huddle collaboration break breakout coffee tea water vending service services
utility utilities shaft shafts riser risers duct ducts void voids below above open double height mezzanine roof
terrace balcony balconies patio courtyard yard garden garage parking car cars park bay bays loading dock delivery
ramp chiller boiler ahu fcu cdu vrf vrv hvac ac a/c split unit units sac package condenser condensing outdoor indoor
duct mep mdb smdb db sdb lv mv panel panels room no number level floor ground first second third fourth fifth sixth
seventh eighth ninth tenth basement upper lower mezz ffl fgl ssl lvl tos top north south east west up down dn plan
plans layout scale section elevation detail typical type existing new proposed demolish demolition future not in
use used only staff public private vip ladies gents men women male female boys girls kids children child baby
nursing mother mothers disabled accessible handicap shoes shoe bag bags luggage mail post room reprographics
mailroom pantry vending sink sinks basin basins urinal urinals shower bathtub tub bed beds bedroom bedrooms master
suite closet wardrobe walk in hand wash powder guest dining salah sitting tv media cinema game games play playroom
majlis diwan diwaniya hosh entrance porch veranda kitchen dirty clean store warehouse workshop lab laboratory
research test testing exam examination consultation consulting treatment dental x ray xray radiology pharmacy
emergency ward wards nurse station isolation operating theatre recovery sterile prep preparation counter counters
bar cashier cash teller atm vault safe strong deposit retail shop shops kiosk food court lounge smoking
technical tech it server network patch cabinet rack racks hub ups battery generator fuel tank tanks water
cistern pump pumps fire extinguisher hose reel alarm panel sprinkler riser dry wet gas meter meters electric
electricity telephone phone lv hv bms ict telecom cctv access door doors window windows wall walls column columns
glass glazing curtain partition partitions sliding swing double single leaf fixed louvre louvers shutter
stair handrail railing balustrade ramp slope steps step treads tread landing core cores block wing tower annex
annexe building podium plaza atrium entrance hall concourse gallery exhibition display showroom museum archive
mosque musalla wudu ablution prayer imam qibla
head chief ceo cfo coo cto cio vp chairman president vice deputy officer supervisor lead team section department dept
division committee council secretariat reception receptionist support help helpdesk desk centre center hub
the and for with from into onto off out per via all any each see note notes typ sim refer ref drawing drawings
project site campus complex headquarters
detail details sheet dwg dim dims dimension dimensions mm cm metre meter metres meters total sqm approx min max
""".split())
ARABIC_PLAN_WORDS = set("""
مكتب مكاتب غرفة غرف قاعة صالة مجلس مجالس مصلى مسجد وضوء دورة مياه حمام حمامات مطبخ مخزن مستودع ممر مدخل مخرج
استقبال انتظار اجتماعات اجتماع درج سلم مصعد مصاعد كهرباء ميكانيك تكييف خادمة سائق نوم طعام ملابس غسيل شرفة فناء
سطح موقف مواقف إدارة ادارة مدير سكرتارية أرشيف ارشيف خدمات أمن امن حراسة نساء رجال سيدات
""".split())


def _room_words() -> set[str]:
    from ..privacy import _names_a_room  # makes privacy._ROOM_WORDS
    from .. import privacy

    _names_a_room("")
    return set(privacy._ROOM_WORDS or ()) | ROOM_SHORT


_KNOWN: set[str] | None = None


def known_words() -> set[str]:
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = {w.lower() for w in PLAN_WORDS | ARABIC_PLAN_WORDS} | _room_words()
    return _KNOWN


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


def _id(prefix: str, text: str) -> str:
    return prefix + hashlib.sha1(_key(text).encode()).hexdigest()[:10]


@dataclass
class Finding:
    """A private part of the texts (a name, a phone number…), wherever it is written."""

    part: str
    kind: str
    id: str = ""
    where: list[str] = field(default_factory=list)  # the texts it is in (a few)
    count: int = 0
    removed: bool = True

    @property
    def placeholder(self) -> str:
        return PLACEHOLDERS[self.kind]

    def view(self) -> dict:
        return {"id": self.id, "text": self.part, "kind": self.kind, "placeholder": self.placeholder,
                "removed": self.removed, "always": self.kind in ALWAYS, "where": self.where[:4], "count": self.count}


def parts_of(text: str, known=None) -> list[tuple[str, str]]:
    """The private parts of one text: (part as written, kind), in the order they stand."""
    known = known_words() if known is None else known
    found: list[tuple[int, int, str]] = []

    def take(pattern, kind):
        for m in pattern.finditer(text):
            a, b = m.span()
            if b - a >= 2 and not any(a < y and x < b for x, y, _ in found):
                found.append((a, b, kind))

    take(EMAIL, "email")
    take(WEB, "web")
    take(EXTENSION, "extension")
    take(PHONE, "phone")
    take(ID_NUMBER, "id number")
    for m in TITLED_NAME.finditer(text):  # the name, up to the first room word after it (MR. JOHN SMITH OFFICE)
        a, b = m.span()
        words = list(WORD.finditer(text, a, b))
        for i, w in enumerate(words[2:], start=2):
            if w.group(0).lower() in known:
                b = w.start()
                break
        b = a + len(text[a:b].rstrip(" -,."))
        if b - a >= 2 and not any(a < y and x < b for x, y, _ in found):
            found.append((a, b, "name"))
    if not found and PRIVATE_TEXT.search(text):  # a contact privacy.py knows, of no kind above (رخصة, هاتف…)
        stripped = text.strip()
        a = text.find(stripped)
        found.append((a, a + len(stripped), "contact"))
    # words Studio does not know: maybe a name (consecutive ones, and the links between
    # them, AL, BIN…, as one)
    run: list[tuple[int, int]] = []
    pending: list[tuple[int, int]] = []

    def close():
        if run:
            a, b = run[0][0], run[-1][1]
            if not any(a < y and x < b for x, y, _ in found):
                found.append((a, b, "maybe a name"))
        run.clear()
        pending.clear()

    for m in WORD.finditer(text):
        a, b = m.span()
        if any(x <= a < y for x, y, _ in found):
            close()
            continue
        w = m.group(0).lower()
        bare = w.replace("’", "'")
        pieces = re.split(r"['-]", bare)
        unknown = (len(bare) >= MIN_UNKNOWN and bare not in known
                   and not all(p in known or len(p) < MIN_UNKNOWN for p in pieces))
        if unknown:
            run.extend(pending)
            pending.clear()
            run.append((a, b))
        elif run and (w in NAME_LINKS or w.startswith(("al-", "el-"))):
            pending.append((a, b))
        else:
            close()
    close()
    return [(text[a:b].strip(), kind) for a, b, kind in sorted(found)]


def names_and_codes(ws, floor_id: str) -> list[tuple[str, str]]:
    """The project's, site's, building's and floor's names and codes (and the site's
    address), as (text, kind): never in a sample. Codes shorter than three symbols
    and a floor's plain name (Ground floor, Floor 2) say nothing and are left; so are
    words of names Studio knows as plan words."""
    from ..ids import parse_id

    p = parse_id(floor_id)
    loc = ws.location(p.prefix("location"))
    b = ws.building(p.prefix("building"))
    f = ws.floor(floor_id)
    out = [(ws.project.code, "project"), (ws.project.name, "project"), (loc.code, "site"), (loc.name, "site"),
           (b.code, "building"), (b.name, "building")]
    if loc.address:
        out.append((loc.address, "address"))
    if not PLAIN_FLOOR.fullmatch(f.name.strip()):
        out.append((f.name, "floor"))
    words = []
    for text, kind in out:  # each uncommon word of a name, alone too: ACME of "Acme Headquarters"
        if kind in ("project", "site", "building", "address") and " " in text.strip():
            words += [(w, kind) for w in WORD.findall(text) if len(w) >= 4 and w.lower() not in known_words()]
    seen, kept = set(), []
    known = known_words()
    for text, kind in out + words:
        text = " ".join(str(text).split())
        generic = not re.search(r"\d", text) and all(w.lower() in known for w in WORD.findall(text))
        if len(text) >= 3 and not generic and _key(text) not in seen:  # "Main Building" says nothing
            seen.add(_key(text))
            kept.append((text, kind))
    return kept


PLAIN_FLOOR = re.compile(r"(?i)((ground|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(st|nd|rd|th)?"
                         r"|basement|roof|mezzanine|lower|upper|typical|g|gf|b\d*|l\d+|m)\s*)*"
                         r"(floor|level|storey|story|plan)?\s*-?\s*\d*")


class Privacy:
    """What is taken out of one sample: the findings in its texts (``texts``, every text
    of the drawing's part and of its rooms), the names and codes that always go, and the
    person's choices (``keep``, ``remove``: ids of findings to keep, and of other texts
    to take out whole). ``scrub(text)`` is any text as the sample has it."""

    def __init__(self, texts: list[str], always: list[tuple[str, str]], keep=(), remove=(), known=None):
        keep, remove = set(keep or ()), set(remove or ())
        found: dict[str, Finding] = {}
        others: dict[str, dict] = {}
        for text in texts:
            text = " ".join(str(text).split())
            if not text:
                continue
            parts = parts_of(text, known)
            for part, kind in parts:
                f = found.setdefault(_id("p", kind + "|" + part), Finding(part=part, kind=kind))
                f.count += 1
                if text not in f.where:
                    f.where.append(text)
            if not parts and re.search(r"[^\W\d_]", text):
                o = others.setdefault(_id("t", text), {"id": _id("t", text), "text": text, "count": 0})
                o["count"] += 1
        for f in found.values():
            f.id = _id("p", f.kind + "|" + f.part)
            f.removed = f.id not in keep
        for o in others.values():
            o["removed"] = o["id"] in remove
            if o["removed"]:
                f = Finding(part=o["text"], kind="chosen", id=o["id"], where=[o["text"]], count=o["count"])
                found[o["id"]] = f
        for text, kind in always:
            f = found.setdefault(_id("a", text), Finding(part=text, kind=kind, id=_id("a", text)))
            f.removed = True
        self.findings = sorted(found.values(), key=lambda f: (f.kind in ALWAYS, f.kind, f.part.lower()))
        self.others = sorted((o for o in others.values() if not o["removed"]), key=lambda o: o["text"].lower())
        gone = [(f.part, f.placeholder, f.kind) for f in found.values() if f.removed]
        known = known_words() if known is None else known
        kept_words = {w.lower() for f in found.values() if not f.removed for w in WORD.findall(f.part)}
        for f in [f for f in found.values() if f.removed and f.kind in ("name", "maybe a name")]:
            # each word of a name, alone too (KHALID of DR. KHALID AL-ALI, in a note or a correction),
            # but a word of a name the person keeps
            gone += [(w, f.placeholder, f.kind) for w in WORD.findall(f.part)
                     if len(w) >= 4 and w.lower() not in known and w.lower() not in TITLES
                     and w.lower() not in kept_words]
        self._patterns = [(_pattern(part), placeholder, kind)
                          for part, placeholder, kind in sorted(set(gone), key=lambda g: -len(g[0]))]

    def scrub(self, text):
        """``text`` with every part that goes replaced by its placeholder."""
        if not isinstance(text, str) or not text:
            return text
        for pattern, placeholder, _ in self._patterns:
            text = pattern.sub(lambda _m, p=placeholder: p, text)
        return text

    def scrub_note(self, note: str) -> str:
        """The person's note without names, contacts and the project's names: a drawing
        text they chose to take out is not taken out of their own words."""
        for pattern, placeholder, kind in self._patterns:
            if kind != "chosen":
                note = pattern.sub(lambda _m, p=placeholder: p, note)
        return note

    def scrub_name(self, name: str) -> str:
        """A layer's or block's name without the project's names and codes, a person's
        name or a contact in it (placeholders without brackets, as names allow). Words
        that may be names are left: layer names are terse (``jun wall``, ``ELE4``)."""
        for pattern, placeholder, kind in self._patterns:
            if kind in ALWAYS or kind in NAMED:
                name = pattern.sub(lambda _m, p=placeholder.strip("[]?"): p, name)
        return name

    def leftovers(self, strings) -> list[str]:
        """The parts that go still found in ``strings`` (none, once scrubbed)."""
        return sorted({p.pattern for p, _, _ in self._patterns for s in strings if isinstance(s, str) and p.search(s)})

    def scrub_all(self, value):
        """Every string in a JSON value scrubbed (the keys too), but Studio's own words:
        a type, who decided it, a rule… (STUDIO_KEYS), which a text taken out (CORRIDOR)
        must not change, and names already scrubbed as names (layers)."""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, list):
            return [self.scrub_all(v) for v in value]
        if isinstance(value, dict):
            return {self.scrub(k) if isinstance(k, str) else k: v if k in STUDIO_KEYS else self.scrub_all(v)
                    for k, v in value.items()}
        return value

    def view(self) -> dict:
        """For the preview: what is found (switchable, but the names and codes), and the
        other texts (each may be taken out)."""
        return {"found": [f.view() for f in self.findings if f.kind != "chosen"],
                "chosen": [f.view() for f in self.findings if f.kind == "chosen"],
                "others": self.others}

    def summary(self) -> dict:
        """What the manifest says was taken out: kinds and counts, never the texts."""
        removed: dict[str, int] = {}
        kept: dict[str, int] = {}
        for f in self.findings:
            (removed if f.removed else kept)[f.kind] = (removed if f.removed else kept).get(f.kind, 0) + 1
        return {"removed": removed, "kept_by_person": kept,
                "removed_by_person": sum(1 for f in self.findings if f.kind == "chosen")}


def _pattern(part: str) -> re.Pattern:
    words = [re.escape(w) for w in part.split()]
    body = r"\s+".join(words)
    head = r"(?<![\w])" if re.match(r"\w", part) else ""
    tail = r"(?![\w])" if re.search(r"\w$", part) else ""
    return re.compile(head + body + tail, re.IGNORECASE)
