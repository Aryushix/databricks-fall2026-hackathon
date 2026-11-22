"""Who is a claim about? Facility-name matching for the entity check.

A claim names an organisation when it contains a capitalised phrase ending in an organisation word
("Patna Dialysis Centre", "S L Raheja Fortis Hospital"). The phrase is matched to the facility by its
distinctive name tokens (generic words like "hospital", "multispeciality" and the city name don't count)
or by its acronym. A named organisation that follows a relation word ("referred to", "in association
with", "from") is a related party, not the subject of the claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GENERIC = set("""
hospital hospitals clinic clinics medical medicals centre center centres centers multispeciality
multispecialty multi speciality specialty specialities super superspeciality care health healthcare
institute institution research the and of for pvt ltd private limited nursing home dental eye heart
diagnostic diagnostics trust foundation maternity children child womens women city general district
civil government govt memorial sciences science college new shri sri smt dr doctor doctors poly
polyclinic lab labs laboratory laboratories hospitality unit wing branch campus group society mission
charitable rural urban primary community sub divisional state national all india indian
""".split())

SERVICE_WORDS = set("""
dialysis haemodialysis hemodialysis kidney renal cardiac cardiology heart cath catheterization
angiography angioplasty cancer oncology trauma emergency imaging radiology scan scans mri pathology
diagnostic diagnostics nephrology neurology neuro ortho orthopaedic orthopedic dental eye vision ivf
fertility maternity paediatric pediatric neonatal icu nicu iccu ccu burn burns plastic surgery surgical
physiotherapy rehabilitation blood bank pharmacy wellness speciality specialty advanced modern
state art day care tie esic cghs echs empanelled panel network partner partners referral
""".split())
ORG_WORDS = (r"Hospitals?|Clinics?|Institute|Institution|Medical College|Medical Cent(?:re|er)|Cent(?:re|er)|"
             r"Nursing Home|Healthcare|Health Care|Medicity|Polyclinic|Sanatorium|Dispensary|Diagnostics?|"
             r"Laborator(?:y|ies)|Labs?|Netralaya|Nethralaya|Chikitsalaya|Aspatal|Infirmary")
_ORG_END = re.compile(rf"\b(?:{ORG_WORDS})\b")
_CAP_TOKEN = re.compile(r"[A-Z][\w.&'’-]*|of|and|&|the|for|de")
_RELATION_BEFORE = re.compile(
    r"(?:\bwith|\bby|\bfrom|\bto|\bunder|\bvia|\bthrough|\bpartner(?:ed)?|\bassociation|\bcollaboration|"
    r"\btie[- ]?up|\breferred|\breferral|\baffiliated|\bempanell?ed|\bthan|\blike|\bincluding|\bunlike)"
    r"\s+(?:the\s+|a\s+|an\s+)?$", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower().replace("'", "").replace("’", "")) if len(t) >= 3}


def distinctive(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in GENERIC}


def acronym(name: str) -> str | None:
    words = [w for w in re.findall(r"[A-Za-z]+", name or "") if w.lower() not in {"of", "and", "the", "for"}]
    caps = "".join(w[0] for w in words if w[0].isupper())
    return caps.upper() if len(caps) >= 3 else None


@dataclass(frozen=True)
class FacilityProfile:
    unique_id: str
    name: str
    city: str | None
    distinctive: frozenset
    acronyms: frozenset
    non_city: frozenset = frozenset()

    @classmethod
    def build(cls, unique_id: str, name: str, city: str | None = None) -> FacilityProfile:
        name = name or ""
        city_tokens = tokens(city or "")
        dist = distinctive(name) - city_tokens
        if not dist:                         # "District Hospital Hapur": the place name is all there is
            dist = distinctive(name)
        acr = {a for a in [acronym(name)] if a}
        acr |= {m.upper() for m in re.findall(r"\(([A-Za-z]{3,10})\)", name)}
        acr |= {w for w in re.findall(r"\b[A-Z]{3,8}\b", name)}
        return cls(unique_id, name, city, frozenset(dist), frozenset(acr), frozenset(distinctive(name) - city_tokens))

    def matches(self, mention: str) -> bool:
        if not self.non_city:                   # "Patna Medical College & Hospital": the city alone is not enough
            full = " ".join(_WORD.findall(self.name.lower()))
            return bool(full) and full in " ".join(_WORD.findall(mention.lower()))
        if self.non_city & distinctive(mention):
            return True
        return bool(self.acronyms & set(re.findall(r"\b[A-Z]{3,8}\b", mention)))


@dataclass(frozen=True)
class EntityCheck:
    entity_match: str               # this | unnamed | other
    mentions: tuple[str, ...]       # every organisation named, verbatim
    related: tuple[str, ...]        # named after a relation word: a partner, referral target or employer


def find_mentions(text: str) -> list[tuple[str, int, int]]:
    """Capitalised organisation names in the text, verbatim, with their offsets."""
    out = []
    for m in _ORG_END.finditer(text):
        start = m.start()
        words = list(re.finditer(r"\S+", text[:start]))
        taken = []
        for w in reversed(words[-7:]):
            tok = w.group(0).strip(",;:()\"")
            if _CAP_TOKEN.fullmatch(tok) and w.group(0)[-1] not in ",;:":
                taken.append(w)
            else:
                break
        while taken and not taken[-1].group(0)[0].isupper():
            taken.pop()                      # don't start on "of" / "and"
        if not taken:
            continue
        s = taken[-1].start()
        name = text[s:m.end()]
        if tokens(name) - GENERIC - SERVICE_WORDS:   # "Dialysis Centre", "Cath Lab": a service, not an organisation
            out.append((name, s, m.end()))
    return out


def check_entity(text: str, profile: FacilityProfile) -> EntityCheck:
    mentions = find_mentions(text)
    names = tuple(m[0] for m in mentions)
    if any(profile.matches(n) for n in names):
        return EntityCheck("this", names, ())
    if profile.acronyms & set(re.findall(r"\b[A-Z]{3,8}\b", text)):
        return EntityCheck("this", names, ())
    related, subjects = [], []
    for name, s, _ in mentions:
        (related if _RELATION_BEFORE.search(text[max(0, s - 40):s]) else subjects).append(name)
    if subjects:
        return EntityCheck("other", names, tuple(related))
    return EntityCheck("unnamed", names, tuple(related))


def owns_site(profile: FacilityProfile, domain: str, page_text: str, known_cities: frozenset = frozenset()) -> bool:
    """Is this website the facility's own? The domain or the pages must name the facility.

    A domain that names another city never counts. A domain that contains a distinctive name word or
    the acronym counts. Otherwise the pages must
    contain the name's first two distinctive words as a phrase ("safe gastro"), so common words such as
    "safe" or "life" scattered over someone else's site don't count. A name made only of generic words
    and a city ("Medical College and Hospital, Kolkata") must appear in full.
    """
    compact = re.sub(r"[^a-z0-9]", "", (domain or "").lower())
    own_city = re.sub(r"[^a-z0-9]", "", (profile.city or "").lower())
    own_name = re.sub(r"[^a-z0-9]", "", profile.name.lower())
    if any(c in compact for c in known_cities if c not in own_city and c not in own_name):
        return False                            # "ashahospitalprayagraj" for an Asha Hospital in Firozabad
    if any(len(t) >= 4 and t in compact for t in profile.non_city):
        return True
    if any(a.lower() in compact for a in profile.acronyms if len(a) >= 3):
        return True
    text = " " + " ".join(_WORD.findall((page_text or "").lower())) + " "
    city = tokens(profile.city or "")
    words = [w for w in _WORD.findall(profile.name.lower()) if len(w) >= 3 and w not in GENERIC and w not in city]
    phrase = " ".join(words[:2]) if words else " ".join(_WORD.findall(profile.name.lower()))
    return bool(phrase) and f" {phrase} " in text
