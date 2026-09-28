"""Text normalization utilities for Ukrainian apostrophe variants, city phonetic variations, and entity matching."""

import re
from typing import Optional, List

# Unicode apostrophe characters:
# \u02bc: Modifier Letter Apostrophe (ʼ) - standard Ukrainian on iOS/Android keyboards
# \u2019: Right Single Quotation Mark (’) - iOS smart quote
# \u2018: Left Single Quotation Mark (‘)
# \u0060: Grave Accent (`) - backtick on EN keyboard
# \u00b4: Acute Accent (´)
# \u02bb: Modifier Letter Turned Comma (ʻ)
# \u02b9: Modifier Letter Prime (ʹ)
# \u2032: Prime (′)
# \u2033: Double Prime (″)
# \u201b: Single High-Reversed-9 Quotation Mark (‛)
APOSTROPHE_PATTERN = re.compile(r"[\u02bc\u2019\u2018\u0060\u00b4\u02bb\u02b9\u2032\u2033\u201b]")

# Settlement prefixes regex:
# Matches leading "м.", "м ", "місто ", "смт.", "смт ", "с.", "с ", "село ", "пос.", "пос ", "селище ", "г.", "г ", "город "
CITY_PREFIX_PATTERN = re.compile(
    r"^(?:(?:м|г|с|смт|пос)\.[\s\-]*|(?:м|г|с|смт|пос)\s+|(?:місто|город|село|селище)\s+)",
    re.IGNORECASE,
)

# Known toponym synonyms, historical/decommunized names, and common phonetic misspellings
CITY_TOPONYM_ALIASES = {
    # Phonetic and spelling variations (Russian transliterations, vowel confusions)
    "сінельникове": "Синельникове",
    "сінельниково": "Синельникове",
    "синельниково": "Синельникове",
    "винниця": "Вінниця",
    "пірятин": "Пирятин",
    "діканька": "Диканька",
    "міргород": "Миргород",
    "прілуки": "Прилуки",
    "лісічанськ": "Лисичанськ",
    "шішакі": "Шишаки",
    "нежин": "Ніжин",
    "нижин": "Ніжин",
    "ирпень": "Ірпінь",
    "ирпинь": "Ірпінь",
    "измаил": "Ізмаїл",
    "мукачеве": "Мукачево",
    "камянське": "Кам'янське",
    # Decommunized & recently renamed cities (where Nova Poshta database updated to new names)
    "новомосковськ": "Самар",
    "новомосковск": "Самар",
    "червоноград": "Шептицький",
    "червоноградський": "Шептицький",
    "новоград-волинський": "Звягель",
    "новоград-волынский": "Звягель",
    "кузнецовськ": "Вараш",
    "кузнецовск": "Вараш",
    "южноукраїнськ": "Південноукраїнськ",
    "южноукраинск": "Південноукраїнськ",
    "дніпродзержинськ": "Кам'янське",
    "днепродзержинск": "Кам'янське",
    "кіровоград": "Кропивницький",
    "кировоград": "Кропивницький",
    "іллічівськ": "Чорноморськ",
    "ильичевск": "Чорноморськ",
    "комсомольськ": "Горішні Плавні",
    "комсомольск": "Горішні Плавні",
    "артемівськ": "Бахмут",
    "артемовск": "Бахмут",
    "переяслав-хмельницький": "Переяслав",
    "володимир-волинський": "Володимир",
    "володимир-волинськ": "Володимир",
}


def normalize_apostrophes(text: Optional[str]) -> str:
    """Normalize all Unicode apostrophe variants to standard ASCII apostrophe (')."""
    if not text:
        return "" if text is None else text
    return APOSTROPHE_PATTERN.sub("'", text)


def clean_city_name(city_name: Optional[str]) -> str:
    """Clean city name by normalizing apostrophes, removing quotes and leading settlement prefixes (м., місто, г., etc.)."""
    if not city_name:
        return ""
    # Normalize apostrophes first
    cleaned = normalize_apostrophes(city_name).strip()
    # Remove outer quotes or braces
    cleaned = cleaned.strip("\"'`«»“”()[]{}")
    # Strip settlement prefixes (e.g. "м. Сінельникове" -> "Сінельникове", "г. Синельниково" -> "Синельниково")
    cleaned = CITY_PREFIX_PATTERN.sub("", cleaned).strip()
    # Strip trailing punctuation if any
    cleaned = cleaned.rstrip(" ,;.:")
    return cleaned


def get_city_search_variants(city_name: str) -> List[str]:
    """Generate search query variants for city names, normalizing apostrophes, phonetic i/y interchange,
    Russian endings, decommunized aliases, and missing apostrophes.
    
    Nova Poshta API 2.0 strictly requires exact spelling (e.g. 'Синельникове' instead of 'Сінельникове',
    'Самар' instead of 'Новомосковськ', 'Кам'янське' with ASCII apostrophe).
    """
    if not city_name:
        return []

    cleaned = clean_city_name(city_name)
    if not cleaned:
        return []

    variants: List[str] = [cleaned]

    def _add_variant(cand: str):
        if cand and cand not in variants:
            variants.append(cand)

    # 1. Check known aliases (e.g. "Сінельникове" -> "Синельникове", "Новомосковськ" -> "Самар")
    lower_name = cleaned.lower()
    if lower_name in CITY_TOPONYM_ALIASES:
        _add_variant(CITY_TOPONYM_ALIASES[lower_name])

    # 2. Check missing apostrophes according to Ukrainian phonetic rules
    # Labials [б, п, в, м, ф] before iotated vowels [я, ю, є, ї] (e.g. Камянське -> Кам'янське)
    if "'" not in cleaned:
        injected_labials = re.sub(r"([бпвмфБПВМФ])([яюєїЯЮЄЇ])", r"\1'\2", cleaned)
        if injected_labials != cleaned:
            _add_variant(injected_labials)

        # 'р' before iotated vowels [я, ю, є, ї] (e.g. Мар'яна, Бур'ян)
        injected_r = re.sub(r"([рР])([яюєїЯЮЄЇ])", r"\1'\2", cleaned)
        if injected_r != cleaned:
            _add_variant(injected_r)

    # 3. Phonetic vowel variations: 'і' <-> 'и'
    # In Ukrainian toponyms (and Russian transliteration), 'і' and 'и' are frequently interchanged
    # (e.g. Сінельникове -> Синельникове, Винниця -> Вінниця, Пірятин -> Пирятин)
    current_pool = list(variants)
    for base in current_pool:
        # 'і' -> 'и'
        if "і" in base or "І" in base:
            cand_y = base.replace("і", "и").replace("І", "И")
            _add_variant(cand_y)
            if cand_y.lower() in CITY_TOPONYM_ALIASES:
                _add_variant(CITY_TOPONYM_ALIASES[cand_y.lower()])

        # 'и' -> 'і' (avoiding invalid Ukrainian sequence 'ії')
        if "и" in base or "И" in base:
            cand_i = re.sub(r"и(?!ї)", "і", base)
            cand_i = re.sub(r"И(?!ї)", "І", cand_i)
            if cand_i != base:
                _add_variant(cand_i)
                if cand_i.lower() in CITY_TOPONYM_ALIASES:
                    _add_variant(CITY_TOPONYM_ALIASES[cand_i.lower()])

    # 4. Russian / colloquial endings & character substitutions
    current_pool = list(variants)
    for base in current_pool:
        # Ending -ово -> -ове (e.g. Синельниково -> Синельникове, Сінельниково -> Сінельникове)
        if base.endswith("ово") or base.endswith("Ово"):
            cand = base[:-1] + ("е" if base[-1] == "о" else "Е")
            _add_variant(cand)
            # also test with 'і' -> 'и'
            if "і" in cand or "І" in cand:
                _add_variant(cand.replace("і", "и").replace("І", "И"))

        # Ending -ове -> -ово (e.g. Мукачеве -> Мукачево)
        elif base.endswith("ове") or base.endswith("Ове"):
            cand = base[:-1] + ("о" if base[-1] == "е" else "О")
            _add_variant(cand)

        # Ending -ево -> -еве / -еве -> -ево
        elif base.endswith("ево") or base.endswith("Ево"):
            cand = base[:-1] + ("е" if base[-1] == "о" else "Е")
            _add_variant(cand)

        # Ending -ов / -ев -> -ів (e.g. Харьков -> Харків, Обухов -> Обухів)
        if base.endswith("ов") or base.endswith("Ов"):
            _add_variant(base[:-2] + ("ів" if base.endswith("ов") else "Ів"))
        elif base.endswith("ев") or base.endswith("Ев"):
            _add_variant(base[:-2] + ("ів" if base.endswith("ев") else "Ів"))

        # Ending -ск -> -ськ (e.g. Бердянск -> Бердянськ)
        if base.endswith("ск") or base.endswith("Ск"):
            _add_variant(base[:-2] + ("ськ" if base.endswith("ск") else "Ськ"))

        # Russian letters: ы -> и, э -> е, ъ -> '
        if any(c in base for c in "ыЫэЭъЪ"):
            cand_ru = (
                base.replace("ы", "и")
                .replace("Ы", "И")
                .replace("э", "е")
                .replace("Э", "Е")
                .replace("ъ", "'")
                .replace("Ъ", "'")
            )
            _add_variant(cand_ru)
            if cand_ru.lower() in CITY_TOPONYM_ALIASES:
                _add_variant(CITY_TOPONYM_ALIASES[cand_ru.lower()])

    # Prioritize official alias if present
    if lower_name in CITY_TOPONYM_ALIASES:
        official_alias = CITY_TOPONYM_ALIASES[lower_name]
        if official_alias in variants:
            variants.remove(official_alias)
            # Insert right after cleaned (or first if cleaned was different)
            variants.insert(1 if variants else 0, official_alias)

    return variants


def _phonetic_stem(s: str) -> str:
    """Normalize a city string into a phonetic stem for comparison."""
    if not s:
        return ""
    # Strip parentheses with oblast/district (e.g. "Синельникове (Дніпропетровська обл)" -> "Синельникове")
    s = re.sub(r"\(.*?\)", "", s).strip()
    s = clean_city_name(s).lower()
    s = normalize_apostrophes(s).replace("'", "")
    # Normalize vowels and characters
    s = (
        s.replace("і", "и")
        .replace("ї", "и")
        .replace("ы", "и")
        .replace("э", "е")
        .replace("ё", "е")
    )
    # Strip neuter/adjectival endings (-ове, -ово, -еве, -ево, -ське, -ська, -ський)
    if s.endswith("ове") or s.endswith("ово") or s.endswith("еве") or s.endswith("ево"):
        s = s[:-3]
    elif s.endswith("ське") or s.endswith("ська") or s.endswith("ський"):
        s = s[:-4]
    elif s.endswith("о") or s.endswith("е"):
        s = s[:-1]
    return s


def is_city_matched(city1: Optional[str], city2: Optional[str]) -> bool:
    """Check if two city names match, ignoring apostrophe variations, case, prefixes,
    parenthesized region descriptions, phonetic i/y differences, and toponym aliases.
    """
    if not city1 or not city2:
        return False

    c1 = clean_city_name(city1).lower()
    c2 = clean_city_name(city2).lower()
    if not c1 or not c2:
        return False

    # 1. Direct equality or substring containment
    if c1 == c2 or c1 in c2 or c2 in c1:
        return True

    # 2. Strip parenthesized region/district info (e.g. "Кам'янське (Дніпропетровська обл)")
    c1_clean = re.sub(r"\(.*?\)", "", c1).strip()
    c2_clean = re.sub(r"\(.*?\)", "", c2).strip()
    if c1_clean and c2_clean and (c1_clean == c2_clean or c1_clean in c2_clean or c2_clean in c1_clean):
        return True

    # 3. Known toponym aliases (e.g. "новомосковськ" <-> "самар", "червоноград" <-> "шептицький", "сінельникове" <-> "синельникове")
    a1 = CITY_TOPONYM_ALIASES.get(c1_clean, c1_clean).lower()
    a2 = CITY_TOPONYM_ALIASES.get(c2_clean, c2_clean).lower()
    if a1 == a2 or a1 == c2_clean or a2 == c1_clean or a1 in a2 or a2 in a1:
        return True

    # 4. Phonetic stem matching (handles any i/y or ending variations)
    stem1 = _phonetic_stem(c1)
    stem2 = _phonetic_stem(c2)
    if stem1 and stem2 and (stem1 == stem2 or stem1 in stem2 or stem2 in stem1):
        return True

    return False

