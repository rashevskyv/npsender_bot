## Senior Audit Report

### Status: APPROVED

### 1. Summary of Review
Task (from user): find and implement improvements that cut AI token spend and raise speed, without changing bot behaviour.
Scope: `src/ai/extractor.py`, `src/nova_poshta/client.py`, `src/utils/city_search.py`, their tests, project docs. `src/bot/handlers.py` touched only for the draft-edit path (round 2, user-approved). Untouched: `src/storage.py`, prompts' wording, models.

Verification:
- `python -m pytest -n auto` → 179 passed (baseline 171 + 8 new).
- Fuzzy-search equivalence: 184 searches (46 queries × min_score 0.72/0.75/0.85 + area filter) snapshotted before the change and compared after → 0 diffs; avg 291 ms → 124 ms.
- Register prompt size on 10 realistic drafts: 5772 → 3421 chars (−41%).

### 2. Fixes Applied by Reviewer
- **src/nova_poshta/client.py:46 `_get_http_client`**: `_post` opened a new `httpx.AsyncClient` (new TCP+TLS) per API call. Now one keep-alive client per event loop. Retry/backoff unchanged. Verified by `tests/test_nova_poshta.py` retry tests (now mock the getter).
- **src/ai/extractor.py `_get_openai_client`**: a new `AsyncOpenAI` (own connection pool) was built per message; now cached per (api_key, base_url, loop).
- **src/ai/extractor.py `AIExtractor._chat_json`**: three copies of call→fallback-call logic repeated the full ~2.5k-token request on *any* exception (timeouts included, on top of SDK's own retries). Now: one call, ```json fences stripped, retry without `response_format` only on `BadRequestError`. Verified by `test_ai_generic_error_is_not_retried`, `test_ai_retries_without_response_format_on_400_and_strips_fences`.
- **src/ai/extractor.py `filter_drafts_for_register` / `disambiguate_candidates`**: compact JSON, drafts trimmed to `REGISTER_DRAFT_FIELDS` (fields named in the prompt + `cod_payment_type`). Verified by `test_register_prompt_is_compact_and_trimmed`.
- **src/ai/extractor.py `parse_text`**: removed dead branch (`healed_fallback` vs `empty_res` were the same object); healing runs in `asyncio.to_thread` so the CPU-bound fuzzy scan no longer blocks other users.
- **src/utils/city_search.py `search`**: `ratio()` skipped when `real_quick_ratio`/`quick_ratio` upper bounds prove it cannot change the outcome (prune floor `min_score - 0.19`, bonuses total 0.18); matcher's b-index built once per target; `lru_cache(4096)` on results (callers get a list copy).

- **src/bot/handlers.py `parsed_info_from_draft` (round 2)**: "edit draft" re-parsed a bot-generated text with a full AI call. Now branch/postomat drafts are rebuilt from stored fields; AI is kept as fallback for address delivery, missing branch number, or unknown city. Full city description goes to `region_name`, which the existing lookup filter matches against `CityInfo.description`. Verified by `test_parsed_info_from_draft_rebuilds_branch_and_postomat_without_ai`, `test_parsed_info_from_draft_falls_back_for_unstructured_drafts`.

- **src/ai/extractor.py `NAME_RUN_PATTERN` (round 3, v0.24.20)**: offline healing fuzzy-matched surname "Залужна" to village "Залужне" and then dropped the name. Words inside a name-like run now count as a settlement only on an exact match. Verified by `test_heal_does_not_turn_surname_into_lookalike_village` (fails without the fix) and 177/177 green.
- **src/ai/extractor.py name extraction (round 4, v0.24.21)**: "Берегомет Данелюк Олександр" gave the city but no name (the 3-word regex match started with the city and was rejected whole; matches don't overlap). A leading city word in a 3-word run is now dropped and the name taken from the remaining two words. Verified by `test_heal_extracts_name_written_after_city_on_same_line` (fails without the fix) and 178/178 green.
- **src/bot/handlers.py warehouse lookup (round 5, v0.24.22)**: candidate-city `get_warehouse` calls ran serially with a 0.25 s sleep each. Now `asyncio.gather` bounded by `WAREHOUSE_LOOKUP_CONCURRENCY = 3`; order kept, per-city errors logged and skipped, rate limits left to `_post` backoff. Verified by `test_warehouse_lookup_across_cities_is_parallel_and_bounded` (fails on the old loop) and 179/179 green.

### 3. Blockers / Handoff Items (Crucial)
(none)

### 4. Recommendations & Code Quality Improvements (Optional/Minor)
- [ ] **src/bot/main.py**: shared HTTP clients are not closed on shutdown (harmless for a long-polling process; add `aclose()` in `finally` if clean shutdown logs matter).
- [ ] Graphify: no `graphify-out/` exists in this repo, so there was no graph to refresh.

### 5. Next Assignment
- **Original goal**: reduce AI token usage and latency of the Nova Poshta Telegram bot without changing user-visible behaviour.
- **Already done**: everything in section 2 (v0.24.19), 179 tests green.
- **Required**: nothing blocking. Optional follow-ups are section 4, in listed order.
- **Do not touch**: prompt wording in `SYSTEM_PROMPT` without an eval set of real messages; `_post` retry semantics.
- Update `walkthrough.md` and `audit.md`, run `graphify update`, re-submit for review.
