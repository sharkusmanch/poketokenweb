import os
from pathlib import Path

from poketokenweb import scan_roots


def test_unset_env_yields_no_roots():
    assert scan_roots.configured_roots(env={}) == []


def test_single_root_is_read(tmp_path):
    assert scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: str(tmp_path)}
    ) == [tmp_path]


def test_several_roots_are_split_on_pathsep(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    roots = scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: os.pathsep.join([str(a), str(b)])}
    )
    assert roots == [a, b]


def test_blank_entries_are_skipped(tmp_path):
    # A trailing separator must not inject Path("."), which would silently
    # scan the working directory.
    roots = scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: f"{tmp_path}{os.pathsep}"}
    )
    assert roots == [tmp_path]


def test_surrounding_whitespace_is_stripped(tmp_path):
    roots = scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: f"  {tmp_path}  "}
    )
    assert roots == [tmp_path]


def test_tilde_is_expanded():
    roots = scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: "~/somewhere"}
    )
    assert roots == [(Path.home() / "somewhere").resolve()]


def test_missing_roots_are_reported(tmp_path):
    present = tmp_path / "present"
    present.mkdir()
    absent = tmp_path / "absent"
    assert scan_roots.missing_roots([present, absent]) == [absent]


def test_a_file_is_reported_as_missing(tmp_path):
    # is_dir() is the real requirement -- a file silently contributes nothing.
    not_a_dir = tmp_path / "f.txt"
    not_a_dir.write_text("", encoding="utf-8")
    assert scan_roots.missing_roots([not_a_dir]) == [not_a_dir]


def test_relative_entries_are_made_absolute(tmp_path, monkeypatch):
    # Scan-cache rows are keyed on str(path); a relative root would silently
    # invalidate the cache if the process ever changed directory.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    roots = scan_roots.configured_roots(env={scan_roots.PROJECT_ROOTS_ENV: "rel"})
    assert roots[0].is_absolute()
    assert roots == [(tmp_path / "rel").resolve()]


def test_overlapping_roots_do_not_double_count(tmp_path):
    # .env.example promises this; the only existing coverage predates the
    # feature and uses the two built-in roots, so nothing would catch a
    # regression that appended extras after the dedup.
    import json

    from poketokenbar.providers.claude import ClaudeProvider

    outer = tmp_path / "outer"
    (outer / "-proj").mkdir(parents=True)
    row = {
        "type": "assistant",
        "timestamp": "2026-08-21T12:00:00Z",
        "requestId": "r1",
        "message": {
            "id": "m1",
            "model": "claude-sonnet-4-20250514",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }
    (outer / "-proj" / "s.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    roots = scan_roots.configured_roots(
        env={scan_roots.PROJECT_ROOTS_ENV: os.pathsep.join([str(outer), str(outer / "-proj")])}
    )
    entries = ClaudeProvider(home=tmp_path / "empty", extra_roots=roots).scan_entries()
    assert len(entries) == 1
    assert sum(e.input for e in entries) == 100


# --- one list per provider (#177) -------------------------------------------


def test_codex_has_its_own_variable():
    """Parsers must not share folders. A Codex rollout under a Claude root is
    not a Claude transcript, and one shared list hands every directory to every
    parser."""
    assert scan_roots.CODEX_ROOTS_ENV != scan_roots.CLAUDE_ROOTS_ENV
    assert scan_roots.CODEX_ROOTS_ENV in scan_roots.ROOT_ENV_NAMES
    assert scan_roots.CLAUDE_ROOTS_ENV in scan_roots.ROOT_ENV_NAMES


def test_the_codex_variable_is_read_independently(tmp_path):
    a = tmp_path / "codex-logs"
    a.mkdir()
    env = {scan_roots.CODEX_ROOTS_ENV: str(a)}
    assert scan_roots.configured_roots(scan_roots.CODEX_ROOTS_ENV, env=env) == [a.resolve()]
    # The Claude knob must not see it.
    assert scan_roots.configured_roots(scan_roots.CLAUDE_ROOTS_ENV, env=env) == []


def test_every_registered_variable_is_poketokenweb_prefixed():
    """Squatting CLAUDE_* or CODEX_* risks colliding with a variable those
    vendors ship later, in the very environment their CLIs run in."""
    for name in scan_roots.ROOT_ENV_NAMES:
        assert name.startswith("POKETOKENWEB_")
