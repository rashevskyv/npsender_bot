"""Offline settlement database and high-performance fuzzy search engine for Ukrainian cities.

Provides systematic, typo-tolerant search across all 11,000+ Nova Poshta settlements
in Ukraine, eliminating reliance on ad-hoc city aliases. Handles misspellings,
vowel interchanges ('і'/'и', 'е'/'є'), grammatical case inflections ('у Львові', 'Синельниковому'),
missing apostrophes, and Russian transliterations.
"""

import difflib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Any

if TYPE_CHECKING:
    from src.nova_poshta.models import CityInfo

from src.utils.text_cleaner import (
    CITY_TOPONYM_ALIASES,
    clean_city_name,
    get_city_search_variants,
    normalize_apostrophes,
)

logger = logging.getLogger(__name__)

# Regex to strip parenthesized oblast/district details (e.g., "Великі Копані (Херсонська обл.)" -> "Великі Копані")
PAREN_DETAILS_PATTERN = re.compile(r"\s*\([^)]*\)")


@dataclass(slots=True)
class CityDatabaseEntry:
    """Lightweight indexed settlement entry for fast matching."""

    ref: str
    description: str
    clean_name: str
    clean_name_lower: str
    clean_ru: str
    clean_ru_lower: str
    name_len: int
    ru_len: int
    area: Optional[str]
    area_ru: Optional[str]
    settlement_type: str
    is_branch: bool
    city_info: Any


class CitySearchEngine:
    """Universal fuzzy search engine for all Nova Poshta settlements."""

    _instance: Optional["CitySearchEngine"] = None

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self._db_path = Path(db_path)
        else:
            # Default to data/cities_database.json relative to repository root
            project_root = Path(__file__).resolve().parent.parent.parent
            self._db_path = project_root / "data" / "cities_database.json"

        self._loaded: bool = False
        self._entries: List[CityDatabaseEntry] = []
        self._exact_map: Dict[str, List[CityDatabaseEntry]] = {}

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "CitySearchEngine":
        """Get or initialize the singleton CitySearchEngine instance."""
        if cls._instance is None:
            cls._instance = cls(db_path=db_path)
        return cls._instance

    def _ensure_loaded(self) -> None:
        """Lazily load and index cities database from disk."""
        if self._loaded:
            return

        if not self._db_path.exists():
            logger.warning(
                f"Cities database not found at {self._db_path}. "
                "Fuzzy offline search will return empty results until database is synced."
            )
            self._loaded = True
            return

        try:
            with open(self._db_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            from src.nova_poshta.models import CityInfo

            entries: List[CityDatabaseEntry] = []
            exact_map: Dict[str, List[CityDatabaseEntry]] = {}

            for item in raw_data:
                ref = item.get("ref", "")
                desc = item.get("description", "")
                desc_ru = item.get("description_ru") or ""
                area = item.get("area")
                area_ru = item.get("area_ru")
                stype = (item.get("settlement_type") or "").strip()
                is_branch = bool(item.get("is_branch", False))

                clean_name = PAREN_DETAILS_PATTERN.sub("", desc).strip()
                clean_name_lower = clean_city_name(clean_name).lower()

                clean_ru = PAREN_DETAILS_PATTERN.sub("", desc_ru).strip() if desc_ru else ""
                clean_ru_lower = clean_city_name(clean_ru).lower() if clean_ru else ""

                city_info = CityInfo(
                    Ref=ref,
                    Description=desc,
                    AreaDescription=area,
                    RegionsDescription=None,
                    SettlementRef=None,
                )

                entry = CityDatabaseEntry(
                    ref=ref,
                    description=desc,
                    clean_name=clean_name,
                    clean_name_lower=clean_name_lower,
                    clean_ru=clean_ru,
                    clean_ru_lower=clean_ru_lower,
                    name_len=len(clean_name_lower),
                    ru_len=len(clean_ru_lower),
                    area=area,
                    area_ru=area_ru,
                    settlement_type=stype,
                    is_branch=is_branch,
                    city_info=city_info,
                )
                entries.append(entry)

                # Index Ukrainian name
                if clean_name_lower:
                    exact_map.setdefault(clean_name_lower, []).append(entry)
                # Index Russian name
                if clean_ru_lower and clean_ru_lower != clean_name_lower:
                    exact_map.setdefault(clean_ru_lower, []).append(entry)

            self._entries = entries
            self._exact_map = exact_map
            self._loaded = True
            logger.info(
                f"CitySearchEngine initialized with {len(entries)} settlements from {self._db_path}."
            )
        except Exception as e:
            logger.error(f"Failed to load cities database from {self._db_path}: {e}")
            self._loaded = True

    def _rank_entries(
        self, entries: List[CityDatabaseEntry], area_filter: Optional[str] = None
    ) -> List[CityDatabaseEntry]:
        """Rank entries prioritizing major cities, presence of branches, and matching area."""
        area_norm = area_filter.lower() if area_filter else None

        def _sort_key(e: CityDatabaseEntry):
            area_match = 1 if (area_norm and e.area and area_norm in e.area.lower()) else 0
            is_city = 1 if e.settlement_type.lower() == "місто" else 0
            is_br = 1 if e.is_branch else 0
            is_concise = 1 if "(" not in e.description else 0
            return (area_match, is_city, is_br, is_concise)

        return sorted(entries, key=_sort_key, reverse=True)

    def search(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.72,
        area_filter: Optional[str] = None,
    ) -> List[CityInfo]:
        """Search for cities by name using exact, phonetic, progressive-stem, and fuzzy matching.

        Args:
            query: Input city name (can have typos, prefixes, inflections, Russian spelling).
            limit: Maximum number of results to return.
            min_score: Minimum fuzzy similarity score (0.0 - 1.0).
            area_filter: Optional region/oblast filter for disambiguation.

        Returns:
            List of matching CityInfo objects, sorted by relevance.
        """
        self._ensure_loaded()
        if not self._entries or not query:
            return []

        cleaned_query = clean_city_name(query)
        if not cleaned_query:
            return []

        clean_q_lower = cleaned_query.lower()
        q_len = len(clean_q_lower)

        # 1. Direct exact lookup (Ukrainian or Russian name)
        if clean_q_lower in self._exact_map:
            ranked = self._rank_entries(self._exact_map[clean_q_lower], area_filter)
            return [e.city_info for e in ranked[:limit]]

        # 2. Check known toponym aliases (e.g. "сінельникове" -> "Синельникове", "новомосковськ" -> "Самар")
        if clean_q_lower in CITY_TOPONYM_ALIASES:
            alias_target = clean_city_name(CITY_TOPONYM_ALIASES[clean_q_lower]).lower()
            if alias_target in self._exact_map:
                ranked = self._rank_entries(self._exact_map[alias_target], area_filter)
                return [e.city_info for e in ranked[:limit]]

        # 3. Check phonetic and morphological variants
        variants = get_city_search_variants(cleaned_query)
        for var in variants:
            var_low = clean_city_name(var).lower()
            if var_low in self._exact_map:
                ranked = self._rank_entries(self._exact_map[var_low], area_filter)
                return [e.city_info for e in ranked[:limit]]

        # 4. Fuzzy & progressive stem matching across all settlements
        # Build candidate variants for fuzzy matching
        q_variants = [clean_q_lower]
        if "і" in clean_q_lower:
            q_variants.append(clean_q_lower.replace("і", "и"))
        if "и" in clean_q_lower:
            q_variants.append(clean_q_lower.replace("и", "і"))
        if "є" in clean_q_lower:
            q_variants.append(clean_q_lower.replace("є", "е"))
        if "е" in clean_q_lower:
            q_variants.append(clean_q_lower.replace("е", "є"))
        if "ї" in clean_q_lower:
            q_variants.append(clean_q_lower.replace("ї", "і"))
        if clean_q_lower.endswith("ово"):
            q_variants.append(clean_q_lower[:-1] + "е")
        elif clean_q_lower.endswith("ево"):
            q_variants.append(clean_q_lower[:-1] + "е")

        candidates: List[Tuple[float, CityDatabaseEntry]] = []
        area_norm = area_filter.lower() if area_filter else None

        for entry in self._entries:
            # Pre-filter by length: avoid expensive SequenceMatcher on wildly different lengths
            min_len_diff = min(
                abs(entry.name_len - q_len),
                abs(entry.ru_len - q_len) if entry.ru_len else 99,
            )
            # Allow up to 4 chars difference for inflections/prefixes
            if min_len_diff > 4:
                continue

            b_ua = entry.clean_name_lower
            b_ru = entry.clean_ru_lower

            best_sim = 0.0
            for q_var in q_variants:
                # SequenceMatcher similarity ratio
                s_ua = difflib.SequenceMatcher(None, q_var, b_ua).ratio()
                s_ru = (
                    difflib.SequenceMatcher(None, q_var, b_ru).ratio() if b_ru else 0.0
                )
                sim = max(s_ua, s_ru)

                # Progressive suffix trimming ("прибирати по літері") for grammatical inflections
                # (e.g. Синельниковому -> Синельникове, Львові -> Львів, Сумах -> Суми, Тернополі -> Тернопіль)
                if len(q_var) >= 4 and entry.name_len >= 3:
                    for trim in (1, 2, 3, 4):
                        if len(q_var) - trim >= 3:
                            stem = q_var[:-trim]
                            if b_ua.startswith(stem) or (b_ru and b_ru.startswith(stem)):
                                stem_score = 0.88 - (trim * 0.02)
                                sim = max(sim, stem_score)

                if sim > best_sim:
                    best_sim = sim

            # Score bonuses
            bonus = 0.0
            if entry.settlement_type.lower() == "місто":
                bonus += 0.05
            if entry.is_branch:
                bonus += 0.03
            if area_norm and entry.area and area_norm in entry.area.lower():
                bonus += 0.08
            if "(" not in entry.description:
                bonus += 0.02

            # Penalize large length differences if similarity isn't very high
            if min_len_diff > 2 and best_sim < 0.90:
                bonus -= min(0.12, min_len_diff * 0.03)

            final_score = best_sim + bonus
            if best_sim >= min_score or final_score >= min_score:
                candidates.append((final_score, entry))

        if not candidates:
            return []

        # Sort candidates by final score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        results: List[CityInfo] = []
        seen_refs = set()
        for score, entry in candidates:
            if entry.ref not in seen_refs:
                seen_refs.add(entry.ref)
                results.append(entry.city_info)
                if len(results) >= limit:
                    break

        return results

    def resolve_canonical_city(
        self, query: str, min_score: float = 0.75, area_filter: Optional[str] = None
    ) -> Optional[CityInfo]:
        """Resolve a city string (even with typos or in Russian) to the canonical CityInfo.

        Returns None if no matching city meets the minimum confidence threshold.
        """
        results = self.search(
            query=query, limit=1, min_score=min_score, area_filter=area_filter
        )
        return results[0] if results else None

    def resolve_canonical_city_name(
        self, query: str, min_score: float = 0.75, area_filter: Optional[str] = None
    ) -> Optional[str]:
        """Resolve a city query to its canonical clean Ukrainian name (e.g. 'Синельникове', 'Вінниця')."""
        city_info = self.resolve_canonical_city(
            query=query, min_score=min_score, area_filter=area_filter
        )
        if not city_info:
            return None
        return PAREN_DETAILS_PATTERN.sub("", city_info.description).strip()

    @staticmethod
    def sync_database(
        api_key: str,
        api_url: str = "https://api.novaposhta.ua/v2.0/json/",
        output_path: Optional[str] = None,
    ) -> int:
        """Download complete settlements list from Nova Poshta API and save to JSON.

        Args:
            api_key: Nova Poshta API key.
            api_url: Nova Poshta API JSON endpoint.
            output_path: Destination path for JSON file.

        Returns:
            Number of settlements saved.
        """
        import httpx

        dest = (
            Path(output_path)
            if output_path
            else Path(__file__).resolve().parent.parent.parent
            / "data"
            / "cities_database.json"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)

        all_cities = []
        page = 1
        limit = 1000

        with httpx.Client(timeout=30.0) as client:
            while True:
                payload = {
                    "apiKey": api_key,
                    "modelName": "Address",
                    "calledMethod": "getCities",
                    "methodProperties": {"Page": str(page), "Limit": str(limit)},
                }
                resp = client.post(api_url, json=payload)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if not data:
                    break

                for item in data:
                    all_cities.append(
                        {
                            "ref": item.get("Ref", ""),
                            "description": item.get("Description", ""),
                            "description_ru": item.get("DescriptionRu", ""),
                            "area": item.get("AreaDescription", ""),
                            "area_ru": item.get("AreaDescriptionRu", ""),
                            "settlement_type": item.get(
                                "SettlementTypeDescription", ""
                            ),
                            "is_branch": item.get("IsBranch") == "1",
                        }
                    )

                if len(data) < limit:
                    break
                page += 1

        with open(dest, "w", encoding="utf-8") as f:
            json.dump(all_cities, f, ensure_ascii=False, indent=2)

        logger.info(f"Successfully synced {len(all_cities)} settlements to {dest}.")
        return len(all_cities)


# Global singleton instance for easy import across modules
city_search_engine = CitySearchEngine.get_instance()
