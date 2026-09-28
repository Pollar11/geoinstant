"""Scene text → country likelihoods (script, letters, street words, TLDs, phone prefixes)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from ..models.ocr import TextLine
from .base import Observation

SPANISH_SPEAKING = ["ES", "MX", "AR", "CO", "PE", "CL", "VE", "EC", "BO", "UY"]
ARABIC_SCRIPT = ["SA", "AE", "EG", "MA", "DZ", "TN", "IQ", "JO", "IR", "PK"]


@dataclass(frozen=True)
class TextRule:
    key: str
    label: str
    countries: dict[str, float]
    other: float = 0.1


def _lr(codes: Iterable[str], lr: float) -> dict[str, float]:
    return {c: lr for c in codes}


# (unicode-name prefix, rule)
SCRIPT_RULES: list[tuple[str, TextRule]] = [
    ("CYRILLIC", TextRule("script:cyrillic", "Cyrillic script", _lr(["RU", "UA", "BY", "BG", "RS", "KZ", "MN", "UZ"], 10), 0.02)),
    ("GREEK", TextRule("script:greek", "Greek script", {"GR": 40}, 0.02)),
    ("HANGUL", TextRule("script:hangul", "Korean Hangul", {"KR": 60}, 0.01)),
    ("HIRAGANA", TextRule("script:kana", "Japanese kana", {"JP": 80}, 0.01)),
    ("KATAKANA", TextRule("script:kana", "Japanese kana", {"JP": 80}, 0.01)),
    (
        "CJK UNIFIED",
        TextRule("script:han", "Chinese characters", {"CN": 12, "TW": 10, "HK": 10, "JP": 4, "SG": 3, "MY": 1.5}, 0.03),
    ),
    ("THAI", TextRule("script:thai", "Thai script", {"TH": 80}, 0.01)),
    ("KHMER", TextRule("script:khmer", "Khmer script", {"KH": 80}, 0.01)),
    ("DEVANAGARI", TextRule("script:devanagari", "Devanagari script", {"IN": 15, "NP": 20}, 0.02)),
    ("BENGALI", TextRule("script:bengali", "Bengali script", {"BD": 20, "IN": 8}, 0.02)),
    ("TAMIL", TextRule("script:tamil", "Tamil script", {"IN": 10, "LK": 12, "SG": 3, "MY": 2}, 0.02)),
    ("SINHALA", TextRule("script:sinhala", "Sinhala script", {"LK": 80}, 0.01)),
    ("ARABIC", TextRule("script:arabic", "Arabic script", _lr(ARABIC_SCRIPT, 8), 0.03)),
    ("HEBREW", TextRule("script:hebrew", "Hebrew script", {"IL": 80}, 0.01)),
    ("ETHIOPIC", TextRule("script:ethiopic", "Ge'ez script", {"ET": 80}, 0.01)),
    ("MONGOLIAN", TextRule("script:mongolian", "Mongolian script", {"MN": 30, "CN": 5}, 0.01)),
]

# ---- Letters that single out a language within a script -----------------------------------
LETTER_RULES: list[tuple[str, TextRule]] = [
    ("іїєґ", TextRule("letter:uk", "Ukrainian letters (і, ї, є, ґ)", {"UA": 30}, 0.1)),
    ("ў", TextRule("letter:be", "Belarusian letter ў", {"BY": 40}, 0.1)),
    ("ђћљњџј", TextRule("letter:sr", "Serbian Cyrillic letters", {"RS": 40}, 0.1)),
    ("қңәғұһ", TextRule("letter:kk", "Kazakh letters", {"KZ": 40}, 0.1)),
    ("өү", TextRule("letter:mn", "Mongolian Cyrillic letters", {"MN": 20, "KZ": 3}, 0.2)),
    ("ыэ", TextRule("letter:ru", "Russian/Belarusian letters (ы, э)", {"RU": 4, "BY": 3, "KZ": 2}, 0.3)),
    ("ß", TextRule("letter:de", "German ß", {"DE": 10, "AT": 8}, 0.1)),
    ("ñ", TextRule("letter:es", "Spanish ñ", _lr(SPANISH_SPEAKING, 8), 0.1)),
    ("ãõ", TextRule("letter:pt", "Portuguese tilde vowels", {"PT": 12, "BR": 12}, 0.1)),
    ("øæ", TextRule("letter:no_da", "Danish/Norwegian ø/æ", {"NO": 15, "DK": 15}, 0.05)),
    ("å", TextRule("letter:nordic", "Nordic å", {"NO": 8, "DK": 6, "SE": 10, "FI": 3}, 0.1)),
    ("őű", TextRule("letter:hu", "Hungarian ő/ű", {"HU": 40}, 0.05)),
    ("ł", TextRule("letter:pl", "Polish ł", {"PL": 40}, 0.05)),
    ("řů", TextRule("letter:cs", "Czech ř/ů", {"CZ": 40}, 0.05)),
    ("șțţ", TextRule("letter:ro", "Romanian ș/ț", {"RO": 40}, 0.05)),
    ("ğış", TextRule("letter:tr", "Turkish ğ/ı/ş", {"TR": 30}, 0.05)),
    ("ėųį", TextRule("letter:lt", "Lithuanian ė/ų/į", {"LT": 40}, 0.05)),
    ("ķļņ", TextRule("letter:lv", "Latvian ķ/ļ/ņ", {"LV": 40}, 0.05)),
    ("āēīū", TextRule("letter:macron", "Vowels with macrons", {"LV": 6, "LT": 2, "NZ": 4}, 0.2)),
    ("ðþ", TextRule("letter:is", "Icelandic ð/þ", {"IS": 60}, 0.02)),
    ("ơưđ", TextRule("letter:vi", "Vietnamese ơ/ư/đ", {"VN": 60}, 0.02)),
    ("پچژگ", TextRule("letter:fa", "Persian letters", {"IR": 15, "PK": 4}, 0.1)),
    ("ٹڈڑے", TextRule("letter:ur", "Urdu letters", {"PK": 30}, 0.1)),
]

# ---- Words and patterns -------------------------------------------------------------------
WORD_RULES: list[tuple[re.Pattern[str], TextRule]] = [
    # Suffix patterns (no leading \b) match compounds such as "Hauptstraße" or "Drottninggatan".
    (
        re.compile(r"(?:stra(?:ss|ß)e|platz|gasse)\b|\bstr\.", re.I),
        TextRule("word:de_street", "German street name", {"DE": 8, "AT": 6, "CH": 5}, 0.2),
    ),
    (
        re.compile(r"\b(?:rue|allée|impasse)\b", re.I),
        TextRule("word:fr_street", "French street name", {"FR": 8, "BE": 4, "CH": 3, "CA": 2, "MA": 2, "SN": 2}, 0.3),
    ),
    (
        re.compile(r"\b(?:calle|avenida|carrera)\b", re.I),
        TextRule("word:es_street", "Spanish street name", _lr(SPANISH_SPEAKING, 6), 0.2),
    ),
    (
        re.compile(r"\b(?:rua|travessa|largo)\b", re.I),
        TextRule("word:pt_street", "Portuguese street name", {"PT": 10, "BR": 10}, 0.1),
    ),
    # "via" is also English ("via London"), so require the capitalised Italian form.
    (
        re.compile(r"\b(?:Via|VIA|Viale|VIALE|Piazza|PIAZZA|Corso|CORSO)\s+[A-Z]"),
        TextRule("word:it_street", "Italian street name", {"IT": 10, "CH": 2}, 0.2),
    ),
    (re.compile(r"\b(?:ulica|ul\.|aleja)", re.I), TextRule("word:pl_street", "Polish street name", {"PL": 20}, 0.1)),
    (re.compile(r"\butca\b", re.I), TextRule("word:hu_street", "Hungarian street name (utca)", {"HU": 40}, 0.05)),
    (re.compile(r"\w(?:gatan|vägen|gränd)\b", re.I), TextRule("word:se_street", "Swedish street name", {"SE": 30}, 0.05)),
    (re.compile(r"\w(?:veien|vegen)\b", re.I), TextRule("word:no_street", "Norwegian street name", {"NO": 20}, 0.1)),
    (re.compile(r"\w(?:katu|tie)\b", re.I), TextRule("word:fi_street", "Finnish street name", {"FI": 10}, 0.3)),
    (
        re.compile(r"\b(?:jalan|jl\.)", re.I),
        TextRule("word:ms_street", "Malay/Indonesian street name", {"ID": 15, "MY": 15, "SG": 3}, 0.05),
    ),
    (re.compile(r"\b(?:soi|thanon)\b", re.I), TextRule("word:th_street", "Thai street name", {"TH": 30}, 0.1)),
    (re.compile(r"\bcarrer\b", re.I), TextRule("word:ca_street", "Catalan street name", {"ES": 20}, 0.05)),
    (re.compile(r"(?:straat|gracht)\b", re.I), TextRule("word:nl_street", "Dutch street name", {"NL": 12, "BE": 6}, 0.2)),
    (
        re.compile(r"(?:sokak|sokağı|caddesi)\b|\bcad\.", re.I),
        TextRule("word:tr_street", "Turkish street name", {"TR": 40}, 0.05),
    ),
    (re.compile(r"\bPARE\b"), TextRule("word:pare", "PARE stop sign", {"BR": 20}, 0.05)),
    (
        re.compile(r"\bALTO\b"),
        TextRule(
            "word:alto", "ALTO stop sign", {"MX": 8, "AR": 6, "CL": 6, "PE": 6, "CO": 4, "BO": 6, "EC": 6, "VE": 6, "UY": 6}, 0.1
        ),
    ),
    (re.compile(r"\bARR[ÊE]T\b"), TextRule("word:arret", "ARRÊT stop sign", {"CA": 20, "FR": 1}, 0.05)),
]

TLD_COUNTRY = {
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "nl": "NL",
    "be": "BE",
    "ch": "CH",
    "at": "AT",
    "pl": "PL",
    "cz": "CZ",
    "sk": "SK",
    "hu": "HU",
    "ro": "RO",
    "bg": "BG",
    "gr": "GR",
    "se": "SE",
    "no": "NO",
    "dk": "DK",
    "fi": "FI",
    "ee": "EE",
    "lv": "LV",
    "lt": "LT",
    "ie": "IE",
    "uk": "GB",
    "ru": "RU",
    "ua": "UA",
    "by": "BY",
    "tr": "TR",
    "jp": "JP",
    "kr": "KR",
    "cn": "CN",
    "tw": "TW",
    "hk": "HK",
    "th": "TH",
    "vn": "VN",
    "my": "MY",
    "sg": "SG",
    "id": "ID",
    "ph": "PH",
    "in": "IN",
    "pk": "PK",
    "au": "AU",
    "nz": "NZ",
    "za": "ZA",
    "ke": "KE",
    "ng": "NG",
    "eg": "EG",
    "ma": "MA",
    "br": "BR",
    "ar": "AR",
    "cl": "CL",
    "mx": "MX",
    "co": "CO",
    "pe": "PE",
    "ca": "CA",
    "is": "IS",
    "hr": "HR",
    "rs": "RS",
    "il": "IL",
    "ae": "AE",
    "sa": "SA",
    "ir": "IR",
    "kz": "KZ",
}
_TLD = re.compile(r"\b[a-z0-9-]+\.(?:(?:com?|org|gov|net|ac|edu)\.)?([a-z]{2})\b", re.I)

PHONE_PREFIX = {
    "1": ["US", "CA"],
    "7": ["RU", "KZ"],
    "20": ["EG"],
    "27": ["ZA"],
    "30": ["GR"],
    "31": ["NL"],
    "32": ["BE"],
    "33": ["FR"],
    "34": ["ES"],
    "36": ["HU"],
    "39": ["IT"],
    "40": ["RO"],
    "41": ["CH"],
    "43": ["AT"],
    "44": ["GB"],
    "45": ["DK"],
    "46": ["SE"],
    "47": ["NO"],
    "48": ["PL"],
    "49": ["DE"],
    "51": ["PE"],
    "52": ["MX"],
    "54": ["AR"],
    "55": ["BR"],
    "56": ["CL"],
    "57": ["CO"],
    "60": ["MY"],
    "61": ["AU"],
    "62": ["ID"],
    "63": ["PH"],
    "64": ["NZ"],
    "65": ["SG"],
    "66": ["TH"],
    "81": ["JP"],
    "82": ["KR"],
    "84": ["VN"],
    "86": ["CN"],
    "90": ["TR"],
    "91": ["IN"],
    "92": ["PK"],
    "94": ["LK"],
    "98": ["IR"],
    "212": ["MA"],
    "234": ["NG"],
    "254": ["KE"],
    "351": ["PT"],
    "353": ["IE"],
    "354": ["IS"],
    "358": ["FI"],
    "380": ["UA"],
    "420": ["CZ"],
    "852": ["HK"],
    "886": ["TW"],
    "971": ["AE"],
    "972": ["IL"],
    "966": ["SA"],
}
_PHONE = re.compile(r"(?:\+|\b00)(\d{1,3})[\s.\-()]*\d")


def _script_of(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    for prefix, _ in SCRIPT_RULES:
        if name.startswith(prefix):
            return prefix
    return None


def analyse_text(lines: list[TextLine] | list[str], min_score: float = 0.5) -> list[Observation]:
    """Return one Observation per matched rule, scored by the best OCR confidence behind it."""
    items: list[tuple[str, float, tuple[float, float, float, float] | None]] = []
    for line in lines:
        if isinstance(line, TextLine):
            if line.score >= min_score:
                items.append((line.text, line.score, line.box))
        else:
            items.append((line, 0.9, None))

    found: dict[str, tuple[TextRule, float, tuple[float, float, float, float] | None, str]] = {}

    def hit(rule: TextRule, score: float, box: tuple[float, float, float, float] | None, text: str) -> None:
        if rule.key not in found or score > found[rule.key][1]:
            found[rule.key] = (rule, score, box, text)

    for text, score, box in items:
        scripts = {s for s in (_script_of(c) for c in text) if s}
        for prefix, rule in SCRIPT_RULES:
            if prefix in scripts:
                hit(rule, score, box, text)
        lowered = text.lower()
        for letters, rule in LETTER_RULES:
            if any(ch in lowered for ch in letters):
                hit(rule, score, box, text)
        for pattern, rule in WORD_RULES:
            if pattern.search(text):
                hit(rule, score, box, text)
        for m in _TLD.finditer(text):
            cc = TLD_COUNTRY.get(m.group(1).lower())
            if cc:
                hit(TextRule(f"tld:{cc}", f"Web address ending .{m.group(1).lower()}", {cc: 25}, 0.2), score, box, text)
        for m in _PHONE.finditer(text):
            digits = m.group(1)
            for n in (3, 2, 1):  # longest matching calling code wins
                if digits[:n] in PHONE_PREFIX:
                    ccs = PHONE_PREFIX[digits[:n]]
                    hit(TextRule(f"phone:{digits[:n]}", f"Phone number +{digits[:n]}", _lr(ccs, 20), 0.2), score, box, text)
                    break

    # Kana implies Japanese even though kanji also matched "han": drop the weaker Han rule.
    if "script:kana" in found:
        found.pop("script:han", None)

    return [
        Observation(
            key=rule.key,
            label=rule.label,
            score=score,
            countries=rule.countries,
            other=rule.other,
            box=box,
            detail=f'"{text[:60]}"',
        )
        for rule, score, box, text in found.values()
    ]
