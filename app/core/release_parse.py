# Curator release-name tokenizer — a PURE, offline, *arr-free helper (core side of the firewall).
# parse(folder_name) extracts (artist, album, year, format, source, edition) from a noisy slskd
# folder name like "Pink Floyd - The Wall (1979) [FLAC]". It carries ZERO *arr field names.
#
# Untrusted-input contract (THREAT T-03-01, RESEARCH §591): the folder name is attacker-influence-
# able free text. Every regex here is ANCHORED/BOUNDED with NO nested quantifiers, so a hostile or
# 500+ char folder name cannot trigger catastrophic backtracking (ReDoS). On garbage / empty /
# non-Latin input parse() returns None-valued fields and NEVER raises (SP-3 graceful).
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# --- token regexes: anchored on word boundaries, alternations only, no nested quantifiers ---
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_FORMAT_RE = re.compile(
    r"\b(?:FLAC|ALAC|WAV|APE|MP3|AAC|OGG|320|256|192|V0|V2|V8|24bit|16bit|24-?44|Hi-?Res)\b",
    re.IGNORECASE,
)
_SOURCE_RE = re.compile(r"\b(?:WEB|CD|Vinyl|LP|SACD|Cassette|Tape)\b", re.IGNORECASE)
_EDITION_RE = re.compile(
    r"\b(?:Deluxe|Remastered|Remaster|Anniversary|Expanded|Bonus)\b", re.IGNORECASE
)
# bracket/paren/brace groups: bounded char-class repetition (no '.'), cannot catastrophically backtrack
_BRACKET_RE = re.compile(r"[\[\{\(][^\[\]\{\}\(\)]*[\]\}\)]")
_SEP_CHARS_RE = re.compile(r"[_\.]+")          # treat underscores/dots as spaces
_WS_RE = re.compile(r"\s{1,}")                 # collapse runs of whitespace (bounded, no nesting)


@dataclass(frozen=True)
class ParsedRelease:
    """The token set extracted from a folder name. All fields Optional — None means 'not found'."""

    artist: Optional[str] = None
    album: Optional[str] = None
    year: Optional[int] = None
    format: Optional[str] = None     # normalized lower-case, e.g. 'flac', 'mp3', '320'
    source: Optional[str] = None     # normalized lower-case, e.g. 'web', 'cd'
    edition: Optional[str] = None    # normalized lower-case, e.g. 'deluxe', 'remaster'


def _fold(s: str) -> str:
    """NFKD-normalize + strip combining marks so non-Latin/diacritic names compare cleanly.

    (RESEARCH 217-221.) Returns the folded string; never raises.
    """
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _clean(s: str) -> Optional[str]:
    s = _SEP_CHARS_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip(" -")
    return s or None


def parse(folder_name) -> ParsedRelease:
    """Tokenize a slskd folder name into a ParsedRelease. Pure; never raises (SP-3).

    Strategy: fold for non-Latin robustness; extract year/format/source/edition tokens; strip those
    tokens AND every bracket/paren/brace group; split the remainder on the FIRST ' - ' into
    artist / album. Garbage or empty input yields all-None fields.
    """
    if not isinstance(folder_name, str) or not folder_name.strip():
        return ParsedRelease()

    folded = _fold(folder_name)

    # --- extract tokens (search the folded text; these are anchored/bounded regexes) ---
    ym = _YEAR_RE.search(folded)
    year = int(ym.group(0)) if ym else None
    fm = _FORMAT_RE.search(folded)
    fmt = fm.group(0).lower() if fm else None
    sm = _SOURCE_RE.search(folded)
    source = sm.group(0).lower() if sm else None
    em = _EDITION_RE.search(folded)
    edition = em.group(0).lower() if em else None

    # --- strip bracket/paren groups, then any bare year/format/source/edition tokens ---
    stripped = _BRACKET_RE.sub(" ", folded)
    stripped = _YEAR_RE.sub(" ", stripped)
    stripped = _FORMAT_RE.sub(" ", stripped)
    stripped = _SOURCE_RE.sub(" ", stripped)
    stripped = _EDITION_RE.sub(" ", stripped)
    # normalize _ and . to spaces BEFORE the split so 'Artist_-_Album' splits like 'Artist - Album'
    stripped = _SEP_CHARS_RE.sub(" ", stripped)
    stripped = _WS_RE.sub(" ", stripped)

    # --- split the clean remainder into artist / album on the first ' - ' separator ---
    artist: Optional[str] = None
    album: Optional[str] = None
    if " - " in stripped:
        left, right = stripped.split(" - ", 1)
        artist = _clean(left)
        album = _clean(right)
    else:
        # No separator: best-effort treat the whole remainder as the album (artist unknown).
        album = _clean(stripped)

    return ParsedRelease(
        artist=artist,
        album=album,
        year=year,
        format=fmt,
        source=source,
        edition=edition,
    )
