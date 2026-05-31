# Phase 3: Matching & Quality Gating - Pattern Map

**Mapped:** 2026-05-30
**Files analyzed:** 21 (9 new `core/` modules, 3 edited adapters, 1 edited config, 1 extended firewall test, 5 new test modules, the labeled fixture corpus, 1 requirements edit)
**Analogs found:** 21 / 21 (every new file has a direct in-repo Phase-2 analog — this phase is a near-perfect mirror of the locked Phase-2 patterns)

> No CONTEXT.md exists (discuss-phase has not run). File list extracted from RESEARCH.md "Recommended Project Structure" (lines 164-193), "Validation Architecture / labeled fixture corpus" (lines 531-546), and "Wave 0 Gaps" (lines 564-571). All binding constraints come from RESEARCH-SEED.md + the locked Phase-2 firewall.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/core/candidate.py` | model (dataclass) | transform | `app/adapters/base.py` (`GapItem`) | exact (frozen neutral dataclass) |
| `app/core/manifest.py` | model (dataclass) | transform | `app/adapters/base.py` (`GapItem`) | exact |
| `app/core/quality.py` (`Profile`, `QualityRank`) | model + utility | transform | `app/adapters/base.py` (`GapItem`) + RESEARCH §5 code example | exact (dataclass) / role-match (pure gate) |
| `app/core/matching.py` | service (pure scorer) | transform | `app/core/gap_detector.py` (core firewall module) | role-match (pure core, no analog scorer) |
| `app/core/release_parse.py` | utility (pure tokenizer) | transform | `app/core/gap_detector.py` (core module) | role-match (no analog parser) |
| `app/core/fakeflac.py` | service (pure heuristic) | transform | `app/core/gap_detector.py` + RESEARCH §6 code example | role-match |
| `app/core/selector.py` | service (pure, swappable) | transform | `app/core/gap_detector.py` | role-match |
| `app/core/gate.py` | service (composition) | transform | `app/core/gap_detector.py` (`detect_gaps` composes adapters+repo) | exact (composition-in-core pattern) |
| `app/adapters/base.py` | config/contract (EDIT) | transform | itself (`ArrAdapter` Protocol + `GapItem`) | exact (extend in place) |
| `app/adapters/lidarr.py` | service (EDIT: `get_quality_profile`/`get_manifest`) | request-response | `app/adapters/lidarr.py` (`_paged`/`_map` defensive parse) | exact |
| `app/adapters/readarr.py` | service (EDIT: best-effort impls) | request-response | `app/adapters/readarr.py` (defensive `_paged`/`_map` + tolerate-both keys) | exact |
| `app/config.py` | config (EDIT: thresholds/weights) | transform | `app/config.py` (`Settings` frozen dataclass + `from_env()`) | exact (extend in place) |
| `app/tests/fixtures/candidates/*.json` (+ `manifests/`, `profiles/`) | test fixture | file-I/O | `app/tests/fixtures/lidarr_missing.json` | exact (slskd-shaped JSON corpus) |
| `app/tests/test_matching.py` | test | transform | `app/tests/test_lidarr_adapter.py` + `test_state_repo.py` (fixture-driven units) | exact |
| `app/tests/test_quality.py` | test | transform | `app/tests/test_lidarr_adapter.py` | exact |
| `app/tests/test_fakeflac.py` | test | transform | `app/tests/test_state_repo.py` (pure-unit pattern) | exact |
| `app/tests/test_release_parse.py` | test | transform | `app/tests/test_state_repo.py` (pure-unit pattern) | exact |
| `app/tests/test_gate.py` | test (end-to-end pure) | transform | `app/tests/test_gap_detector.py` (end-to-end over corpus + `FakeAdapter`) | exact |
| `app/tests/test_adapter_protocol.py` | test (EXTEND firewall grep) | transform | itself (`test_core_state_have_no_arr_field_names`) | exact (extend the regex + add `Profile`/`Manifest` to Protocol-conformance) |
| `app/requirements.txt` | config (EDIT: add `rapidfuzz`) | — | `app/requirements.txt` (Phase-2 human-verify checkpoint comment precedent) | exact |

---

## Shared Patterns

These cross-cutting patterns come straight from Phase-2 code and apply to MANY Phase-3 files. Reference them once here; per-file sections below point back.

### SP-1: Frozen neutral dataclass (the firewall's vocabulary)
**Source:** `app/adapters/base.py` lines 17-35 (`GapItem`)
**Apply to:** `candidate.py`, `manifest.py`, `quality.py` (`Profile`), `matching.py` (`MatchConfig`)
The ONLY shapes that cross the firewall are frozen dataclasses with neutral field names and a docstring explaining what crosses and why. Copy this exact style — `@dataclass(frozen=True)`, `Optional[...]`/`| None` fields, a `raw`-style provenance escape hatch only if needed, and a docstring naming the contract.
```python
@dataclass(frozen=True)
class GapItem:
    """The uniform gap the core acts on — the ONLY shape that crosses the adapter firewall.
    ...
    """
    arr_app: Literal["lidarr", "readarr"]
    arr_id: str
    kind: Literal["album", "book"]
    gap_type: GapType
    title: Optional[str]
    artist_or_author: Optional[str]
    foreign_id: Optional[str]
    quality_profile_id: Optional[int]
    raw: Dict[str, Any] = field(default_factory=dict)
```
New `Candidate`/`CandidateFile`/`Manifest`/`Profile` shapes are given in RESEARCH lines 368-395 — implement them in exactly this frozen-dataclass idiom.

### SP-2: The firewall — core modules import ONLY neutral types + repo
**Source:** `app/core/gap_detector.py` lines 1-20
**Apply to:** ALL of `app/core/*` (`matching`, `quality`, `fakeflac`, `release_parse`, `selector`, `gate`, `candidate`, `manifest`)
Core modules carry a header comment declaring they are the "core side of the firewall" with "ZERO *arr field names", and import only `from adapters.base import ...` (the neutral types / Protocol) and `from state import repo`. Replicate the header-comment convention and the import discipline.
```python
# detect_gaps() is the ONLY caller of the adapters ... it is
# the core side of the firewall (PITFALL #6) so it must contain ZERO *arr field names or wire
# vocabulary — it speaks only GapItem + the repo.
from adapters.base import ArrAdapter  # the Protocol the core depends on (the firewall's interface)
from state import repo
```
**Anti-pattern (Pitfall 2, RESEARCH 420-423):** never let `profileId`, `foreignAlbumId`, `qualityProfileId`, `records[`, `items[...]["allowed"]`, `cutoff` JSON keys appear under `app/core/`. The `Profile`/`Manifest` arrive PRE-NORMALIZED from the adapter.

### SP-3: Defensive parse → graceful skip/max-penalty, never crash
**Source:** `app/adapters/lidarr.py` lines 91-109 (`_map`) and `app/adapters/readarr.py` lines 95-119
**Apply to:** `lidarr.get_quality_profile`/`get_manifest`, `readarr.*`, and the `core/` graceful-degradation paths (empty parsed artist → distance 1.0; missing `length` → skip fake-FLAC sub-check; zero audio files → decline).
The Phase-2 rule (RESEARCH Pitfalls 4 & V5 input validation, lines 430-433 / 583): a malformed/missing input is skipped or maxed-out + logged, never raised into the loop.
```python
def _map(self, rec: dict, gap_type: str):
    # Defensive: a record without `id` ... skip it ... so one bad record cannot KeyError-abort
    if not isinstance(rec, dict) or rec.get("id") is None:
        log.warning("lidarr record not a dict or missing id; skipping: %r", rec)
        return None
    artist = rec.get("artist") or {}
    return GapItem(... foreign_id=rec.get("foreignAlbumId"), quality_profile_id=rec.get("profileId") ...)
```
Readarr additionally tolerates BOTH key spellings (`rec.get("qualityProfileId") or rec.get("profileId")`, line 114) — mirror this "tolerate-both / wrong-guess-skips-not-crashes" stance for the `get_quality_profile` profile JSON shape (A4 is unconfirmed).

### SP-4: Frozen `Settings` + `from_env()` env-snapshot (config tunables)
**Source:** `app/config.py` lines 10-48
**Apply to:** the threshold/weight/fake-FLAC env additions (`MATCH_STRONG_THRESH`, `MATCH_REC_GAP_THRESH`, per-weight, `FAKEFLAC_MIN_KBPS`).
Extend the EXISTING frozen dataclass + `from_env()` (do NOT introduce pydantic-settings — RESEARCH §Stack note + OQ1). Each field gets a static default + an `os.getenv(...)` read in `from_env()` so tests can `monkeypatch.setenv` then rebuild via `Settings.from_env()` (the WR-01 fix; precedent in `test_gap_detector.py` lines 137-147).
```python
@dataclass(frozen=True)
class Settings:
    lidarr_url: str = "http://lidarr:8686"
    ...
    @classmethod
    def from_env(cls) -> "Settings":
        return cls(lidarr_url=os.getenv("LIDARR_URL", "http://lidarr:8686"), ...)
settings = Settings.from_env()
```
Defaults live in `MatchConfig` (RESEARCH 207-215); `gate.py` reads `config.settings` to override them — keep the env names as the documented strings in RESEARCH §3 (lines 323-327).

### SP-5: Offline fixture loader + JSON corpus
**Source:** `app/tests/conftest.py` lines 27-39 (`load_fixture`) + `app/tests/fixtures/lidarr_missing.json`
**Apply to:** the whole `tests/fixtures/candidates/` corpus and every new test module.
Reuse the existing `load_fixture` conftest fixture (name → parsed dict from `tests/fixtures/`). The candidate corpus mirrors `lidarr_missing.json`'s shape: a small, hand-authored, fully-offline JSON file. For the nested `candidates/`, `manifests/`, `profiles/` layout, either extend `load_fixture` to accept a subpath (`load_fixture("candidates/known_good_flac")`) or add a thin sibling loader — the existing one already does `FIXTURES_DIR / f"{name}.json"`, so a subpath name works unchanged.
```python
@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    return _load
```

### SP-6: Pure-unit + fixture-driven test structure
**Source:** `app/tests/test_state_repo.py` (pure units, local `_gap()` builder) + `app/tests/test_lidarr_adapter.py` (fixture-driven, `load_fixture`) + `app/tests/test_gap_detector.py` (end-to-end + `FakeAdapter`)
**Apply to:** all 5 new test modules.
Each test file opens with a docstring listing the criteria/REQ-IDs it closes, uses a local builder helper (`_gap(**overrides)` → `SimpleNamespace` in `test_state_repo.py` lines 25-39) to keep tests independent, and asserts the LABELED expected decision per corpus fixture. The sandbox note ("Python 3.9 + offline locally, authoritative green at CI/NAS Python 3.12") appears verbatim in every Phase-2 test header — copy it (and note `rapidfuzz` is absent in the sandbox, so matcher tests run at CI/NAS exactly like the httpx tests did, per RESEARCH 511-516).

---

## Pattern Assignments

### `app/core/candidate.py` (model, transform) — NEW
**Analog:** `app/adapters/base.py` (`GapItem`, lines 17-35)
**Why:** `Candidate`/`CandidateFile` ARE the Phase 3→4 contract — the exact role `GapItem` plays for Phase 2→core. Same firewall-crossing frozen-dataclass requirement.
**Patterns:** SP-1 (frozen dataclass), SP-2 (lives in core, no *arr vocab).
**Shape to implement:** RESEARCH lines 368-393 verbatim (`CandidateFile` fields + `Candidate` fields + helpers `audio_file_count`, `file_titles`, `audio_files()`). Keep `username`/`upload_speed`/`free_upload_slots` present but document "selector-only, NEVER read by matching" (Pitfall 5, RESEARCH 435-437). Helpers are pure derived methods/properties (no I/O) so the dataclass stays frozen.

### `app/core/manifest.py` (model, transform) — NEW
**Analog:** `app/adapters/base.py` (`GapItem`, lines 17-35)
**Why:** `Manifest` is the normalized authoritative target built by the adapter from `foreign_id` — same neutral-type-across-firewall role as `GapItem`.
**Patterns:** SP-1, SP-2.
**Shape:** `Manifest(artist, album, track_count, track_titles: tuple[str,...] | None, kind, year=None)` (RESEARCH 394). `track_titles=None` is the graceful-omission path (§2 / OQ3) — design the matcher to skip the sub-distance when None. For books: `Manifest(author, title, ...)` rides the same type (RESEARCH §9, lines 399-401).

### `app/core/quality.py` (model + utility, transform) — NEW
**Analog:** `app/adapters/base.py` (`Profile` dataclass) + RESEARCH §5 code example (lines 442-451)
**Why:** `Profile` is a neutral frozen dataclass (SP-1); the `gate()` fn is a pure core function (SP-2). RESEARCH gives the exact gate body.
**Patterns:** SP-1 (`Profile(allowed: frozenset[int], cutoff_rank: int)`, RESEARCH 395), SP-2, SP-3 (rank `None` → reject with reason).
**Core pattern to copy** (RESEARCH 444-451):
```python
def gate(candidate, profile) -> tuple[bool, str]:
    for f in candidate.audio_files():
        rank = rank_for(f.extension, f.bitrate_kbps)
        if rank is None or rank not in profile.allowed:
            return False, f"quality REJECT: {f.filename} not in profile allowed set"
        if rank < profile.cutoff_rank:
            return False, f"quality REJECT: {f.filename} rank {rank} below cutoff {profile.cutoff_rank} (no downgrade)"
    return True, "quality OK: all audio files >= cutoff"
```
The `QualityRank` ladder + `rank_for(ext, bitrate)` is the novel bit; keep reason strings matching RESEARCH 334.

### `app/core/matching.py` (service / pure scorer, transform) — NEW
**Analog:** `app/core/gap_detector.py` (the only existing pure `core/` module — provides the firewall header + import discipline; there is no existing scorer, so the scoring body is ported from beets per RESEARCH §1).
**Why:** This is the phase's hard core. No code analog exists for the algorithm; the structural analog is "a pure `core/` module that imports only neutral types" (SP-2).
**Patterns:** SP-1 (`MatchConfig` frozen dataclass), SP-2 (header + imports), SP-3 (empty parsed name → distance 1.0, never crash).
**Core pattern to copy:** RESEARCH lines 201-249 (the ported `score()` + `_norm`/`_str_distance`/`_track_count_distance`) and 255-265 (`recommend()` with rec-gap). Every sub-score MUST emit a reason string (RESEARCH 328-335). Import `from rapidfuzz import fuzz` (degraded `difflib` fallback noted in RESEARCH 511/338, but rapidfuzz is the target).

### `app/core/release_parse.py` (utility / pure tokenizer, transform) — NEW
**Analog:** `app/core/gap_detector.py` (core-module structure only)
**Why:** No existing parser; structural analog is a pure `core/` helper.
**Patterns:** SP-2 (pure core), SP-3 (graceful on garbage/non-Latin → returns Nones, never crashes — feeds the `garbage_metadata` DECLINE and `non_latin` ACCEPT corpus cases).
**Core pattern:** the regex token set in RESEARCH lines 339-345 (year/format/source/edition + strip-to-clean-name). **Security (RESEARCH 591):** keep regexes anchored/bounded (no catastrophic backtracking) — test pathological inputs. `unicodedata.normalize("NFKD", s)` + strip combining (RESEARCH 217-221) is the non-Latin fold.

### `app/core/fakeflac.py` (service / pure heuristic, transform) — NEW
**Analog:** `app/core/gap_detector.py` (core module) + RESEARCH §6 code example (lines 456-469)
**Why:** Pure core heuristic; RESEARCH gives the exact body.
**Patterns:** SP-2, SP-3 (**critical** — missing `length` → SKIP the bytes/sec check, never reject on `None` input; Pitfall 4, RESEARCH 430-433).
**Core pattern to copy:** RESEARCH 457-469 (bytes/sec floor with `if f.length_seconds:` skip-guard, claimed-bitrate-in-lossy-bucket check, `_has_lossy_source_token(folder)`). Floor default `min_kbps=400` (config-tunable via SP-4). Only runs `if f.extension == "flac"`.

### `app/core/selector.py` (service / dumb swappable, transform) — NEW
**Analog:** `app/core/gap_detector.py`
**Why:** Pure core; deliberately separate from matching (Pitfall 5).
**Patterns:** SP-2. Reads `Candidate.username/upload_speed/free_upload_slots` — these fields are read ONLY here (RESEARCH 437). Sorts already-accepted candidates by (distance, format preference, free-slots/speed). Keep it small and swappable.

### `app/core/gate.py` (service / composition, transform) — NEW
**Analog:** `app/core/gap_detector.py` `detect_gaps()` (lines 23-39)
**Why:** `gate.py` composes matching+quality+fakeflac+selector exactly as `detect_gaps` composes adapters+repo — the "single composition point in core" pattern.
**Patterns:** SP-2, SP-4 (reads `config.settings` to build `MatchConfig`).
**Composition pattern to mirror** (`detect_gaps`, lines 23-39):
```python
def detect_gaps(adapters: List[ArrAdapter], conn: sqlite3.Connection) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for adapter in adapters:
        items = adapter.get_wanted()
        for it in items:
            repo.upsert_gap(conn, it)
        counts[adapter.app] = len(items)
    return counts
```
`gate.py` returns a `GateResult(decision='accept'|'decline', chosen, distance, reasons[])` (RESEARCH 160) — a frozen dataclass (SP-1). Flow: for each candidate run quality.gate + fakeflac.check (eligible iff both pass) → score eligibles → `recommend()` (rec-gap) → `selector` among accepts. The `__main__` one-shot UAT trigger convention (gap_detector lines 82-98) is optional here since Phase 3 is offline.

### `app/adapters/base.py` (config/contract, EDIT)
**Analog:** itself — `ArrAdapter` Protocol (lines 38-71) + `GapItem` (17-35)
**Why:** Add neutral `Profile`/`Manifest` types and make `get_quality_profile`/`get_manifest` real Protocol members (currently `get_quality_profile` is stubbed at line 52-54; `get_manifest` is not yet declared).
**Patterns:** SP-1 (new types), and the Protocol's existing "declared now, implemented in later phases" stub style (lines 51-69).
**Existing stub to upgrade** (lines 52-54):
```python
def get_quality_profile(self, profile_id: int) -> dict:
    """Phase 3 — resolve a quality profile/cutoff for a gap. Stubbed in Phase 2."""
    ...
```
Change return type from `dict` to the neutral `Profile` (this phase implements it), and add `def get_manifest(self, foreign_id: str) -> Manifest: ...` alongside it (impl now or Phase-4-wired per OQ2). Keep the firewall doc-comment at the top (lines 1-8) accurate.

### `app/adapters/lidarr.py` (service, EDIT: `get_quality_profile` + `get_manifest`)
**Analog:** `app/adapters/lidarr.py` itself — `_paged` (lines 42-75) for the GET+envelope idiom, `_map` (91-109) for normalize-behind-firewall.
**Why:** New methods do the same job as `get_wanted`: fetch *arr JSON, normalize to a neutral type, keep ALL *arr field names local. Lidarr is primary → `raise_for_status()` surfaces hard faults (line 65), NOT breaker-wrapped.
**Patterns:** SP-3 (defensive normalize), the injected `httpx.Client` for offline testability (lines 28-36).
**Imports/auth pattern to copy** (lines 9-36): `from adapters.base import GapItem` (add `Profile`, `Manifest`), `self._headers = {"X-Api-Key": api_key}`, fail-fast on empty key.
**Normalize-behind-firewall pattern** (the `_map` excerpt in SP-3): `get_quality_profile` GETs `/api/v1/qualityprofile/{id}`, reads the ordered `items[]` (`allowed` bool + nested quality) + `cutoff` (RESEARCH 349, A4-unconfirmed → keep all key access local + `.get()`-defensive), and returns `Profile(allowed_ranks, cutoff_rank)`. `get_manifest` builds `Manifest` from the MB track list. **All *arr/MB key names stay in THIS file.**

### `app/adapters/readarr.py` (service, EDIT: best-effort impls)
**Analog:** `app/adapters/readarr.py` itself — `_paged` swallow-to-`[]` (lines 41-83) + tolerate-both-keys `_map` (95-119)
**Why:** Books are best-effort; a profile/manifest fault must degrade, never gate music (ARR-02). Same defensive stance as Phase-2 Readarr.
**Patterns:** SP-3 (swallow fault → safe default; tolerate both profile-id spellings, line 114). The book `Profile` is a format ladder (EPUB/MOBI/PDF/AZW3, RESEARCH 352); `Manifest(author, title, ...)`. Make impls stub-safe so an unconfirmed shape skips the book rather than crashing.

### `app/config.py` (config, EDIT: thresholds/weights/fake-FLAC floor)
**Analog:** `app/config.py` itself (lines 10-48)
**Why & pattern:** SP-4 verbatim — add frozen fields + `os.getenv` reads in `from_env()`. Env names per RESEARCH 323-327 (`MATCH_STRONG_THRESH=0.15`, `MATCH_REC_GAP_THRESH=0.10`, weights, `FAKEFLAC_MIN_KBPS=400`). Defaults must equal `MatchConfig` defaults so behavior is identical with no env set.

### `app/tests/fixtures/candidates/*.json` (+ `manifests/`, `profiles/`) (test fixture) — NEW
**Analog:** `app/tests/fixtures/lidarr_missing.json`
**Why:** The labeled corpus is the spine of validation (RESEARCH 531-546) — hand-authored, offline, slskd-shaped JSON, exactly like the Phase-2 *arr fixtures.
**Patterns:** SP-5. Author the 11 labeled cases from RESEARCH 534-546 (`known_good_flac` ACCEPT … `garbage_metadata` DECLINE), each paired with the `Manifest` + `Profile` it's scored against. **Security note (V5, RESEARCH 583):** fixtures are local trusted test data; slskd strings are treated as opaque match text only.

### `app/tests/test_matching.py` / `test_quality.py` / `test_fakeflac.py` / `test_release_parse.py` (test) — NEW
**Analog:** `test_lidarr_adapter.py` (fixture-driven units, lines 19-37) + `test_state_repo.py` (pure-unit + local builder, lines 25-39)
**Why & pattern:** SP-6. Per-REQ docstring header (RESEARCH 548-557 maps each REQ→test). Calibration discipline (Pitfall 3, RESEARCH 425-428): known-good MUST land ≤ threshold and all bad MUST land >; if a `test_known_good_accepts` fails, tune the number/weight, NEVER the assertion. Property test (optional, RESEARCH 571): "more matching tracks never increases distance."

### `app/tests/test_gate.py` (test, end-to-end pure) — NEW
**Analog:** `app/tests/test_gap_detector.py` (end-to-end, `FakeAdapter`, lines 57-66)
**Why:** `test_gate.py` drives the full `(candidate, manifest, profile) → (decision, score, reasons)` over the corpus, the way `test_gap_detector` drives `detect_gaps` end-to-end. Use a `FakeAdapter`-style local helper to supply normalized `Profile`/`Manifest` (so no live *arr). Assert each corpus fixture's LABELED decision (RESEARCH 534-546) + that reason strings are present (Soularr-opacity fix). Includes `test_declines_below_threshold`, `test_declines_ambiguous` (rec-gap), `test_known_good_accepts`.

### `app/tests/test_adapter_protocol.py` (test, EXTEND firewall grep)
**Analog:** itself — `test_core_state_have_no_arr_field_names` (lines 67-78) + `_strip_comment` (54-64) + the `ARR_FIELD_NAMES` regex (line 24)
**Why:** The locked firewall grep MUST be extended to the new `app/core/` modules (RESEARCH 195, 557, 568). The test already `rglob`s all `app/core` + `app/state` `.py`/`.sql` files, so the NEW core modules are auto-covered the moment they exist — but the regex must grow to catch the new forbidden tokens.
**Exact extension point** (line 24):
```python
ARR_FIELD_NAMES = re.compile(r"foreignAlbumId|X-Api-Key|records\[|profileId")
```
Add the Pitfall-2 tokens (RESEARCH 423): `qualityProfileId`, and the profile-JSON shape leaks `items[`, `"allowed"`, `cutoff` (comment-stripped lines are already ignored by `_strip_comment`, so doc-mentions like "the cutoff" in a `#` comment won't false-positive). Also extend `test_both_satisfy_protocol` (lines 32-51) to assert the new adapters expose callable `get_quality_profile`/`get_manifest` now that they're implemented.

### `app/requirements.txt` (config, EDIT: add `rapidfuzz`)
**Analog:** `app/requirements.txt` (Phase-2 httpx/respx human-verify checkpoint comment precedent, RESEARCH 100, 110)
**Why & pattern:** Add `rapidfuzz==3.13.x` (pin exact patch at plan time). **Planner MUST gate it behind a `checkpoint:human-verify` task** exactly as httpx/respx were gated in Phase 2 (RESEARCH 100-110). `guessit` only if the custom tokenizer fails the corpus (fallback, RESEARCH 76, 345).

---

## No Analog Found

None. Every Phase-3 file maps to a concrete Phase-2 analog. The two pieces with no *code* analog for their internal algorithm — `matching.py` (ported beets scorer) and `release_parse.py` (regex tokenizer) — still map structurally to the `app/core/gap_detector.py` "pure core module behind the firewall" pattern, and their algorithm bodies are given verbatim in RESEARCH (§1 lines 201-265, §4 lines 339-345). The planner should port those from RESEARCH, wrapped in the SP-1/SP-2/SP-3 idioms above.

## Metadata

**Analog search scope:** `app/adapters/`, `app/core/`, `app/state/`, `app/tests/` (+ fixtures), `app/config.py`, `app/requirements*.txt`
**Files scanned:** full `app/` tree (27 files) + both phase-3 research docs
**Pattern extraction date:** 2026-05-30
**Firewall note (load-bearing):** `app/core/*` import only `adapters.base` neutral types (`GapItem`, new `Candidate`/`Manifest`/`Profile`), the `ArrAdapter` Protocol, and `state.repo`. The extended grep test (`test_adapter_protocol.py`) is the structural proof — extend its regex when adding the new modules.
