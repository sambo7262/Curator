# Curator candidate model — the Phase 3->4 contract. CandidateFile/Candidate are the ONLY
# shape the slskd side hands the gating core: frozen, neutral, *arr-free. This is the core side
# of the firewall (PITFALL #6) — it carries ZERO *arr field names and ZERO wire vocabulary, only
# the normalized fields the matcher/quality/fakeflac gates read.
#
# Phase 3 DEFINES + tests this contract against fixture JSON; Phase 4 populates it from real slskd
# search results (via build_candidate / Candidate.from_slskd below — the single mapping seam).
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core import release_parse  # pure tokenizer; populates parsed_* at construction (no *arr, no I/O)

# Audio extensions the matcher counts as "tracks" (Pitfall: a folder of .cue/.log/.jpg is not music).
AUDIO_EXTENSIONS = frozenset({"flac", "mp3", "m4a", "alac", "ape", "wav", "ogg", "aac"})


@dataclass(frozen=True)
class CandidateFile:
    """One file inside a slskd candidate folder, normalized.

    The optional audio attributes (bitrate/length/sample_rate/bit_depth/is_vbr) come from slskd's
    per-file Soulseek attributes and are frequently ABSENT — every consumer must treat None as
    "unknown, skip this sub-check" rather than reject (Pitfall 4). extension is lower-case, no dot.
    """

    filename: str
    size_bytes: int
    extension: str                      # normalized lower, no leading dot: 'flac','mp3',...
    bitrate_kbps: Optional[int] = None  # slskd bitRate attr if present, else None (unknown)
    length_seconds: Optional[int] = None
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    is_vbr: Optional[bool] = None

    @property
    def is_audio(self) -> bool:
        return self.extension in AUDIO_EXTENSIONS


@dataclass(frozen=True)
class Candidate:
    """A scored-against-a-manifest slskd folder — the Phase 3->4 contract type.

    parsed_artist/parsed_album/parsed_year/parsed_format are derived by release_parse at
    construction (see build_candidate); the matcher anchors on the MANIFEST and uses these parsed
    tokens only as the thing being matched (anchoring rule, Pitfall 1).

    username / free_upload_slots / upload_speed are SELECTOR-ONLY: they are read ONLY by
    selector.py to tie-break already-accepted candidates and are NEVER read by matching (Pitfall 5
    — uploader speed must never bleed into the match score).
    """

    folder: str                                     # raw folder/dir name from slskd (opaque match text)
    files: Tuple[CandidateFile, ...]
    username: str = ""                              # uploader — SELECTOR-ONLY, never read by matching
    free_upload_slots: Optional[int] = None         # SELECTOR-ONLY
    upload_speed: Optional[int] = None              # SELECTOR-ONLY
    # derived by release_parse at construction (None when the folder name yields nothing):
    parsed_artist: Optional[str] = None
    parsed_album: Optional[str] = None
    parsed_year: Optional[int] = None
    parsed_format: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)  # provenance escape hatch (original slskd result)

    def audio_files(self) -> Tuple[CandidateFile, ...]:
        """The subset of files whose extension is a known audio extension (pure, no I/O)."""
        return tuple(f for f in self.files if f.is_audio)

    @property
    def audio_file_count(self) -> int:
        """Count of audio files — the track-count completeness signal (0 => immediate decline)."""
        return len(self.audio_files())

    @property
    def file_titles(self) -> Tuple[str, ...]:
        """Filename stems of the audio files, for per-track title coverage scoring.

        Strips the extension and any leading directory; the leading track-number prefix
        ('01 - ', '1. ') is NOT stripped here — the matcher's fuzzy token_set_ratio is robust to
        it, and keeping it dependency-free here avoids guessing a numbering scheme.
        """
        titles = []
        for f in self.audio_files():
            stem = f.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if "." in stem:
                stem = stem.rsplit(".", 1)[0]
            titles.append(stem)
        return tuple(titles)

    @classmethod
    def from_slskd(cls, result: Dict[str, Any]) -> "Candidate":
        """Map a slskd-shaped search-result dict into a Candidate (the Phase-3-owned factory).

        Defensive on EVERY field (SP-3): a missing key yields None / a safe default, never a
        KeyError — one malformed result must not abort the gating loop. parsed_* are populated by
        release_parse.parse(folder). Phase 4 feeds real slskd JSON into exactly this seam.
        """
        return build_candidate(result)


def _extension_of(filename: str) -> str:
    """Lower-case extension with no dot; '' when the name has no extension (defensive, never raises)."""
    if not isinstance(filename, str):
        return ""
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in stem:
        return ""
    return stem.rsplit(".", 1)[-1].lower()


def _common_dir(filenames) -> str:
    """Deepest directory shared by a set of slskd file paths (`\\`- or `/`-separated), or ''.

    slskd search responses carry NO top-level folder/directory field — the directory lives inside
    each file's `filename` (e.g. `music\\Queen\\A Kind of Magic\\01 - One Vision.flac`). Without this
    the candidate folder was empty, release_parse got "", and EVERY live candidate matched at the
    max-penalty distance (nothing ever passed the match gate). This recovers the album directory as
    the common parent of the files' paths so the matcher has real artist/album text to work with."""
    dirs = []
    for fn in filenames:
        if not isinstance(fn, str) or not fn:
            continue
        norm = fn.replace("\\", "/")
        if "/" in norm:
            dirs.append([s for s in norm.rsplit("/", 1)[0].split("/") if s])
    if not dirs:
        return ""
    common = dirs[0]
    for parts in dirs[1:]:
        i = 0
        while i < len(common) and i < len(parts) and common[i] == parts[i]:
            i += 1
        common = common[:i]
        if not common:
            break
    return "/".join(common)


def _parse_slskd_path(path: str):
    """Parse a slskd album-directory PATH into a ParsedRelease, recovering the artist from the path.

    Soulseek shares overwhelmingly nest as `.../<artist>/<album>/<track>` (live-confirmed: `music\\
    Queen\\A Kind of Magic\\..`, `@@mfapl\\Music (320)\\Queen\\A Kind of Magic\\..`). release_parse
    splits a single folder name on ' - ', which a path like that does NOT contain, so artist/album
    came out empty. Strategy: parse the LEAF segment (the album folder — yields album + year/format,
    and the artist too when the leaf is itself 'Artist - Album'); when the leaf yields no artist and
    there is a parent segment, take the IMMEDIATE parent directory as the artist (the album folder's
    parent is the artist by Soulseek convention). A single-segment folder (the offline-fixture and
    'Artist - Album (Year) [FMT]' cases) parses exactly as before — the parent logic never triggers,
    so the matcher's corpus calibration is unchanged."""
    if not isinstance(path, str) or not path.strip():
        return release_parse.parse("")
    segs = [s for s in path.replace("\\", "/").split("/") if s.strip()]
    if not segs:
        return release_parse.parse("")
    parsed = release_parse.parse(segs[-1])
    if parsed.artist is None and len(segs) >= 2:
        # the album folder's parent dir is the artist; reuse release_parse to fold/clean it
        artist = release_parse.parse(segs[-2]).album
        if artist:
            parsed = release_parse.ParsedRelease(
                artist=artist, album=parsed.album, year=parsed.year,
                format=parsed.format, source=parsed.source, edition=parsed.edition,
            )
    return parsed


def _int_or_none(value: Any) -> Optional[int]:
    """Coerce a slskd attr to int, tolerating None/str/garbage -> None (never raises)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def build_candidate(result: Dict[str, Any]) -> Candidate:
    """Module-level factory: slskd result dict -> Candidate (parsed_* via release_parse).

    slskd result shape (per RESEARCH §5/§8, confirmed against live slskd in Phase 4):
      { "username", "folder"|"directory", "freeUploadSlots", "uploadSpeed",
        "files": [ { "filename", "size", "bitRate", "length", "sampleRate", "bitDepth",
                     "isVariableBitRate" }, ... ] }
    Every access is .get()-defensive so an absent optional attribute becomes None, not a crash.
    """
    if not isinstance(result, dict):
        result = {}

    raw_files = result.get("files") or []
    files = []
    for rf in raw_files:
        if not isinstance(rf, dict):
            continue
        filename = rf.get("filename") or rf.get("name") or ""
        files.append(
            CandidateFile(
                filename=filename,
                size_bytes=_int_or_none(rf.get("size")) or 0,
                extension=_extension_of(filename),
                bitrate_kbps=_int_or_none(rf.get("bitRate")),
                length_seconds=_int_or_none(rf.get("length")),
                sample_rate=_int_or_none(rf.get("sampleRate")),
                bit_depth=_int_or_none(rf.get("bitDepth")),
                is_vbr=_bool_or_none(rf.get("isVariableBitRate")),
            )
        )

    # slskd responses carry no folder field -> derive the album directory from the files' paths.
    folder = result.get("folder") or result.get("directory") or _common_dir(
        [f.filename for f in files]
    )
    parsed = _parse_slskd_path(folder)
    return Candidate(
        folder=folder,
        files=tuple(files),
        username=result.get("username") or "",
        free_upload_slots=_int_or_none(result.get("freeUploadSlots")),
        upload_speed=_int_or_none(result.get("uploadSpeed")),
        parsed_artist=parsed.artist,
        parsed_album=parsed.album,
        parsed_year=parsed.year,
        parsed_format=parsed.format,
        raw=result,
    )
