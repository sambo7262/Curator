# Regression coverage for the slskd folder-derivation fix (live-NAS bug): slskd search responses
# carry NO top-level folder field — the album directory lives inside each file's `filename` path.
# build_candidate previously read only result["folder"]/["directory"], so cand.folder was "",
# release_parse got "", and EVERY live candidate matched at the max-penalty distance (nothing ever
# passed the match gate). These tests use the EXACT folder shapes observed live (Queen, 2026-05-31).
from core.candidate import _common_dir, _parse_slskd_path, build_candidate


def _slskd_result(filenames, **over):
    res = {"username": "seeder", "files": [{"filename": fn, "size": 1000, "bitRate": None}
                                           for fn in filenames]}
    res.update(over)
    return res


def test_common_dir_recovers_album_directory_from_backslash_paths():
    files = [
        r"music\queen\A Kind Of Magic\101 Queen - One Vision.flac",
        r"music\queen\A Kind Of Magic\102 Queen - A Kind of Magic.flac",
    ]
    assert _common_dir(files) == "music/queen/A Kind Of Magic"


def test_common_dir_empty_when_files_have_no_directory():
    assert _common_dir(["track1.flac", "track2.flac"]) == ""
    assert _common_dir([]) == ""


def test_parse_slskd_path_recovers_artist_from_parent_segment():
    # `.../<artist>/<album>/` — the album folder's parent dir is the artist (live Soulseek convention).
    p = _parse_slskd_path("music/queen/A Kind Of Magic")
    assert p.artist == "queen"
    assert p.album == "A Kind Of Magic"


def test_parse_slskd_path_handles_junk_grandparents():
    # Only the IMMEDIATE parent is taken as artist; junk roots (@@mfapl, Music (320)) are grandparents.
    p = _parse_slskd_path("@@mfapl/Music (320)/Queen/A Kind of Magic")
    assert p.artist == "Queen"
    assert p.album == "A Kind of Magic"


def test_parse_slskd_path_leaf_with_dash_parses_without_parent():
    # A leaf that is itself 'Artist - Album' parses directly; the parent ('downloads') is ignored.
    p = _parse_slskd_path("audio/downloads/queen - absolute greatest hits")
    assert p.artist == "queen"
    assert p.album == "absolute greatest hits"


def test_single_segment_folder_unchanged_no_regression():
    # The offline-fixture / 'Artist - Album (Year) [FMT]' shape parses exactly as before (no parent
    # logic), so the matcher's corpus calibration is untouched.
    p = _parse_slskd_path("Radiohead - OK Computer (1997) [FLAC]")
    assert p.artist == "Radiohead"
    assert p.album == "OK Computer"


def test_build_candidate_derives_folder_and_parsed_fields_from_files():
    """END-TO-END: a slskd result with NO folder field still yields a non-empty folder + real
    parsed artist/album (the bug: this used to be '' / None -> dist 1.00 -> no match)."""
    result = _slskd_result([
        r"music\Queen\A Kind of Magic (1986)\Queen - A Kind of Magic - 01 - One Vision.mp3",
        r"music\Queen\A Kind of Magic (1986)\Queen - A Kind of Magic - 02 - A Kind of Magic.mp3",
    ])
    cand = build_candidate(result)
    assert cand.folder == "music/Queen/A Kind of Magic (1986)"
    assert cand.parsed_artist == "Queen"
    assert cand.parsed_album == "A Kind of Magic"
    assert cand.parsed_year == 1986


def test_build_candidate_explicit_folder_still_honored():
    """When slskd DID provide a folder/directory, it wins over derivation (back-compat)."""
    result = _slskd_result([r"x\y\01.flac"], folder="Radiohead - OK Computer (1997) [FLAC]")
    cand = build_candidate(result)
    assert cand.folder == "Radiohead - OK Computer (1997) [FLAC]"
    assert cand.parsed_artist == "Radiohead"
