"""Text normalization utilities for Ukrainian apostrophe variants and entity matching."""

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


def normalize_apostrophes(text: Optional[str]) -> str:
    """Normalize all Unicode apostrophe variants to standard ASCII apostrophe (')."""
    if not text:
        return "" if text is None else text
    return APOSTROPHE_PATTERN.sub("'", text)


def get_city_search_variants(city_name: str) -> List[str]:
    """Generate search query variants for city names, normalizing apostrophes and handling missing apostrophes.
    
    Nova Poshta API 2.0 strictly requires ASCII apostrophe (') for cities like Кам'янське.
    If the input contains no apostrophe, generates candidate variants according to Ukrainian
    phonetic apostrophe rules (after labial consonants б, п, в, м, ф or р before я, ю, є, ї).
    """
    if not city_name:
        return []
    
    normalized = normalize_apostrophes(city_name).strip()
    variants = [normalized]

    if "'" not in normalized:
        # Check labials [б, п, в, м, ф] before iotated vowels [я, ю, є, ї] (e.g. Камянське -> Кам'янське)
        injected_labials = re.sub(r"([бпвмфБПВМФ])([яюєїЯЮЄЇ])", r"\1'\2", normalized)
        if injected_labials != normalized and injected_labials not in variants:
            variants.append(injected_labials)

        # Check 'р' before iotated vowels [я, ю, є, ї] (e.g. Мар'яна, Бур'ян)
        injected_r = re.sub(r"([рР])([яюєїЯЮЄЇ])", r"\1'\2", normalized)
        if injected_r != normalized and injected_r not in variants:
            variants.append(injected_r)

    return variants


def is_city_matched(city1: Optional[str], city2: Optional[str]) -> bool:
    """Check if two city names match, ignoring apostrophe variations, case, and whitespace."""
    if not city1 or not city2:
        return False
    c1 = normalize_apostrophes(city1).strip().lower()
    c2 = normalize_apostrophes(city2).strip().lower()
    return bool(c1 and c2 and (c1 == c2 or c1 in c2 or c2 in c1))
