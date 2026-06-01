# Curator gate — the Phase-3 INTEGRATION point: the single composition function that wires the four
# pure gates into one verdict, exactly as gap_detector.detect_gaps composes the adapters + ledger in
# Phase 2. It is the core side of the firewall (PITFALL #6): ZERO *arr field names, ZERO wire
# vocabulary — it speaks only the neutral Candidate / Manifest / Profile contract types and the
# matching/quality/fakeflac/selector core modules.
#
# evaluate() answers the whole Phase-3 question for a gap in one call:
#   "Given these slskd candidate folders, this authoritative manifest, and this quality profile —
#    do we ACCEPT a download, and if so WHICH copy, and WHY?"
# The pipeline is eligibility-BEFORE-acceptance (the no-downgrade / no-fake invariant): a candidate
# must pass BOTH the quality gate (QUAL-02) AND the fake-FLAC gate (QUAL-03) before it is even scored
# for matching. Ineligible candidates are excluded WITH their reason and can never be accepted, so a
# below-cutoff or fake-FLAC folder is structurally unable to slip past as a "good match".
#
# Pure: no I/O, no clock, no network. Phase 4 consumes the GateResult to actually trigger a download.
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from core import fakeflac, matching, quality, selector
from core.candidate import Candidate
from core.manifest import Manifest
from core.matching import MatchConfig
from core.quality import Profile


@dataclass(frozen=True)
class GateResult:
    """The single verdict the gate emits per gap (SP-1 frozen dataclass, RESEARCH line 160).

    decision : "accept" only when an eligible candidate matched strongly + unambiguously; else
               "decline" (the precision-over-recall default — a human is never asked to adjudicate).
    chosen   : the selected Candidate on accept (picked by selector among the accepted set), else None.
    distance : the best eligible match distance (1.0 when nothing was eligible).
    reasons  : the full explainability trail — per-candidate eligibility outcomes PLUS the match
               decision reasons — so every accept/decline is auditable (the Soularr-opacity fix).
    """

    decision: Literal["accept", "decline"]
    chosen: Optional[Candidate]
    distance: float
    reasons: List[str] = field(default_factory=list)


def _config_from_settings() -> MatchConfig:
    """Build a MatchConfig from the live config.settings (SP-4) so the owner tunes thresholds/weights
    via env WITHOUT a rebuild. Imported lazily so this module parses where config's env isn't set up.
    The MatchConfig defaults already EQUAL the Settings defaults, so this is behavior-preserving."""
    from config import settings

    return MatchConfig(
        w_artist=settings.match_w_artist,
        w_album=settings.match_w_album,
        w_track_count=settings.match_w_track_count,
        w_track_titles=settings.match_w_track_titles,
        strong_thresh=settings.match_strong_thresh,
        rec_gap_thresh=settings.match_rec_gap_thresh,
        same_album_thresh=settings.match_same_album_thresh,
    )


def _fakeflac_floor() -> int:
    """The fake-FLAC bytes/sec floor from config.settings (SP-4); falls back to the default 400."""
    try:
        from config import settings

        return settings.fakeflac_min_kbps
    except Exception:  # pragma: no cover - config always importable in practice
        return 400


def evaluate(
    candidates: List[Candidate],
    manifest: Manifest,
    profile: Profile,
    cfg: Optional[MatchConfig] = None,
    min_kbps: Optional[int] = None,
) -> GateResult:
    """Compose quality + fakeflac + matching + selector into one GateResult (the Phase-3 verdict).

    Mirrors detect_gaps' single-composition-point shape (gap_detector 23-39). For each candidate:
      1. quality.gate(candidate, profile)  — QUAL-02 no-downgrade; reject below cutoff / not-allowed.
      2. fakeflac.check(candidate, floor)  — QUAL-03 re-wrapped-lossy defense.
    A candidate is ELIGIBLE iff BOTH pass; only eligibles are scored (matching.score) and fed to
    matching.recommend() (MATCH-02 strong-thresh + rec-gap). Ineligible candidates are recorded in
    the reason trail with their rejection reason and excluded from acceptance entirely.

    On accept, selector.select() picks the chosen copy AMONG THE ACCEPTED set (the only place
    uploader speed/slots are read — matching never saw them). The returned GateResult carries the
    full reason trail (eligibility lines + the decision reasons), so the outcome is explainable.

    cfg / min_kbps default to config.settings (env-tunable, no rebuild). Pure; never raises on a
    malformed candidate — quality/fakeflac/matching are each individually defensive (SP-3).
    """
    cfg = cfg or _config_from_settings()
    floor = _fakeflac_floor() if min_kbps is None else min_kbps

    reasons: List[str] = []
    eligible: List[matching.Scored] = []

    for cand in candidates:
        label = cand.folder or "<candidate>"

        q_ok, q_reason = quality.gate(cand, profile)
        if not q_ok:
            reasons.append(f"[{label}] excluded: {q_reason}")
            continue

        f_ok, f_reason = fakeflac.check(cand, floor)
        if not f_ok:
            reasons.append(f"[{label}] excluded: {f_reason}")
            continue

        dist, score_reasons = matching.score(cand, manifest, cfg)
        eligible.append((dist, cand, score_reasons))
        reasons.append(f"[{label}] eligible: quality+fakeflac OK, match dist={dist:.2f}")

    decision, _rec_chosen, distance, decision_reasons = matching.recommend(eligible, cfg)
    reasons.extend(decision_reasons)

    chosen: Optional[Candidate] = None
    if decision == "accept":
        # Selection is separate from matching: pick the best copy among ALL the accepted candidates.
        # recommend() declines on ambiguity, so on accept the strong winner is the only sub-strong,
        # unambiguous match; selector ratifies it (and would tie-break a genuine multi-accept set).
        best = min(t[0] for t in eligible)
        accepted_set = [t for t in eligible if t[0] <= best + 1e-9]
        chosen = selector.select(accepted_set)
        reasons.append(f"selected '{chosen.folder}' among {len(accepted_set)} accepted")

    return GateResult(decision=decision, chosen=chosen, distance=distance, reasons=reasons)
