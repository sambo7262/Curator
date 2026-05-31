"""Phase-3 release-name tokenizer proofs (MATCH-01 — the parsed candidate identity feed).

These close the release_parse half of MATCH-01: a noisy slskd folder name must tokenize into
(artist, album, year, format, source, edition) for the matcher to anchor against, AND the parser
must be a pure, ReDoS-safe, non-Latin-robust function that NEVER raises on garbage (THREAT
T-03-01 / Pitfall: garbage_metadata DECLINE, non_latin ACCEPT corpus cases depend on this).

release_parse imports NO rapidfuzz (only stdlib re/unicodedata), so this module runs in the
Python 3.9 + offline dev sandbox exactly as it will at CI/NAS Python 3.12 — the authoritative
green gate remains CI/NAS, but there is no sandbox skip here.
"""
import time

from core.release_parse import ParsedRelease, parse


def test_clean_name_with_year_and_format():
    """A clean 'Artist - Album (YEAR) [FORMAT]' yields all tokens, noise stripped from name."""
    r = parse("Pink Floyd - The Wall (1979) [FLAC]")
    assert r.artist == "Pink Floyd"
    assert r.album == "The Wall"
    assert r.year == 1979
    assert r.format == "flac"


def test_bracket_and_paren_noise_stripped_from_album():
    """Bracket/paren/brace groups are removed so format/year tokens don't pollute the album text."""
    r = parse("Radiohead - In Rainbows (2007) [FLAC] {WEB}")
    assert r.artist == "Radiohead"
    assert r.album == "In Rainbows"
    assert r.year == 2007
    assert r.format == "flac"
    assert r.source == "web"
    # the bracketed tokens must NOT survive in the album string
    assert "FLAC" not in (r.album or "")
    assert "2007" not in (r.album or "")


def test_source_and_edition_tokens():
    """source (CD/WEB/Vinyl...) and edition (Deluxe/Remaster...) are extracted + normalized lower."""
    r = parse("Miles Davis - Kind of Blue (1959) [CD] Remastered")
    assert r.artist == "Miles Davis"
    assert r.album == "Kind of Blue"
    assert r.source == "cd"
    assert r.edition == "remastered"  # the Remastered alternation matches the full word


def test_underscores_and_dots_treated_as_separators():
    """Folder names using _ or . as spaces still produce clean artist/album text."""
    r = parse("The_Beatles_-_Abbey_Road_(1969)_[FLAC]")
    assert r.artist == "The Beatles"
    assert r.album == "Abbey Road"
    assert r.year == 1969
    assert r.format == "flac"


def test_non_latin_folds_and_parses_without_crash():
    """A non-Latin/diacritic folder NFKD-folds and parses; combining marks are stripped."""
    r = parse("Björk - Homogénic (1997) [FLAC]")
    assert isinstance(r, ParsedRelease)
    assert r.year == 1997
    assert r.format == "flac"
    # NFKD fold strips the combining marks: 'Bjork', 'Homogenic'
    assert r.artist == "Bjork"
    assert r.album == "Homogenic"


def test_garbage_returns_none_fields_and_never_raises():
    """Pathological / meaningless input returns None-valued artist/album and raises nothing."""
    r = parse("!!!___")
    assert r.artist is None
    # album may be None or an empty-ish remainder, but must never raise and never be garbage tokens
    assert r.year is None
    assert r.format is None


def test_empty_and_non_string_input_safe():
    """Empty string / whitespace / non-str input degrade to all-None, never crash (SP-3)."""
    assert parse("") == ParsedRelease()
    assert parse("    ") == ParsedRelease()
    assert parse(None) == ParsedRelease()
    assert parse(12345) == ParsedRelease()


def test_no_separator_falls_back_to_album_only():
    """A folder name without a ' - ' separator yields album-only (artist unknown), no crash."""
    r = parse("GreatestHitsCollection (2001)")
    assert r.artist is None
    assert r.year == 2001


def test_adversarial_long_folder_name_returns_bounded(reraise=None):
    """ReDoS guard (THREAT T-03-01): a 2000+ char hostile folder returns within trivial time."""
    hostile = ("A - " + ("(" * 500) + "x" * 500 + ")" * 500 + " [FLAC] (1999)")
    start = time.monotonic()
    r = parse(hostile)
    elapsed = time.monotonic() - start
    assert isinstance(r, ParsedRelease)
    # anchored/bounded regexes => no catastrophic backtracking; must finish near-instantly
    assert elapsed < 1.0, f"parse() took {elapsed:.3f}s on adversarial input (possible ReDoS)"
