"""Tests for poketokenweb.events — the bounded celebration log.

Payloads below are copied verbatim from
poketokenbar/companion_store.py::_note_celebration — {"kind","title","detail"},
with NO id field. That absence is the whole reason two identical hatches must
still both be recorded.
"""

import json
import os

import pytest

from poketokenweb import events

# --- real payloads, copied from _note_celebration ------------------------
HATCHED = {
    "kind": "hatched",
    "title": "It hatched!",
    "detail": "Pikachu came out of the egg.",
}
SHINY = {
    "kind": "shiny",
    "title": "A shiny hatched!",
    "detail": "A shiny Pikachu — 1 in 4096!",
}
EVOLVED = {"kind": "evolved", "title": "Evolved!", "detail": "It became Raichu."}
GRADUATED = {
    "kind": "graduated",
    "title": "Graduated!",
    "detail": "Raichu joined your Pokedex.",
}
DITTO = {
    "kind": "ditto",
    "title": "Huh? It's Ditto!",
    "detail": "Your companion was a Ditto all along.",
}
ALL_PAYLOADS = [HATCHED, SHINY, EVOLVED, GRADUATED, DITTO]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "events.json"


# --- read ----------------------------------------------------------------


def test_read_missing_file_returns_empty(path):
    assert events.read(path) == []


def test_read_missing_parent_directory_returns_empty(tmp_path):
    assert events.read(tmp_path / "nope" / "events.json") == []


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "{", "not json at all", '{"kind": "hatched"}', "null", "42", '"str"'],
)
def test_read_corrupt_file_returns_empty_never_raises(path, raw):
    path.write_text(raw, encoding="utf-8")
    assert events.read(path) == []


def test_read_drops_non_dict_entries(path):
    path.write_text(json.dumps([HATCHED, "junk", 7, None]), encoding="utf-8")
    assert events.read(path) == [HATCHED]


def test_read_after_corruption_recovers_on_next_append(path):
    path.write_text("}}garbage{{", encoding="utf-8")
    assert events.read(path) == []
    events.append(path, HATCHED, now=1.0)
    assert [e["kind"] for e in events.read(path)] == ["hatched"]


# --- append --------------------------------------------------------------


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
def test_append_records_the_real_payload_shape(path, payload):
    entry = events.append(path, payload, now=1700000000.0)
    assert entry == {
        "kind": payload["kind"],
        "title": payload["title"],
        "detail": payload["detail"],
        "published_at": 1700000000.0,
    }
    assert events.read(path) == [entry]


@pytest.mark.parametrize("empty", [None, {}, {"title": "t", "detail": "d"}, {"kind": ""}, {"kind": None}])
def test_append_ignores_empty_or_kindless_payloads(path, empty):
    assert events.append(path, empty) is None
    assert events.read(path) == []


def test_append_does_not_clobber_an_existing_log(path):
    events.append(path, HATCHED, now=1.0)
    events.append(path, EVOLVED, now=2.0)
    assert [e["kind"] for e in events.read(path)] == ["evolved", "hatched"]


def test_newest_first_ordering(path):
    for i, payload in enumerate(ALL_PAYLOADS):
        events.append(path, payload, now=float(i))
    got = events.read(path)
    assert [e["published_at"] for e in got] == [4.0, 3.0, 2.0, 1.0, 0.0]
    assert got[0]["kind"] == "ditto"


def test_two_identical_hatches_are_both_recorded(path):
    """The payload has no id — published_at is the only discriminator."""
    first = events.append(path, HATCHED, now=100.0)
    second = events.append(path, dict(HATCHED), now=200.0)
    got = events.read(path)
    assert len(got) == 2, "identical hatches must not be deduplicated"
    assert got == [second, first]
    assert {e["published_at"] for e in got} == {100.0, 200.0}


def test_many_identical_hatches_are_all_recorded(path):
    for i in range(5):
        events.append(path, HATCHED, now=float(i))
    got = events.read(path)
    assert len(got) == 5
    assert [e["published_at"] for e in got] == [4.0, 3.0, 2.0, 1.0, 0.0]


def test_identical_hatches_in_the_same_instant_are_both_recorded(path):
    """Even a clock collision must not collapse two real hatches."""
    events.append(path, HATCHED, now=100.0)
    events.append(path, HATCHED, now=100.0)
    assert len(events.read(path)) == 2


def test_append_defaults_published_at_to_now(path, monkeypatch):
    monkeypatch.setattr(events.time, "time", lambda: 1234.5)
    assert events.append(path, HATCHED)["published_at"] == 1234.5


def test_append_missing_title_and_detail_are_blank(path):
    entry = events.append(path, {"kind": "hatched"}, now=1.0)
    assert entry["title"] == "" and entry["detail"] == ""


def test_append_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nested" / "events.json"
    events.append(path, HATCHED, now=1.0)
    assert len(events.read(path)) == 1


def test_append_does_not_mutate_the_caller_payload(path):
    payload = dict(HATCHED)
    events.append(path, payload, now=1.0)
    assert payload == HATCHED


# --- bounding ------------------------------------------------------------


def test_log_is_bounded_to_max_events(path):
    for i in range(events.MAX_EVENTS + 20):
        events.append(path, HATCHED, now=float(i))
    got = events.read(path)
    assert len(got) == events.MAX_EVENTS
    assert got[0]["published_at"] == float(events.MAX_EVENTS + 19)
    # the oldest 20 were dropped, newest kept
    assert got[-1]["published_at"] == float(20)


def test_bounding_truncates_an_oversized_existing_file(path):
    oversized = [dict(HATCHED, published_at=float(i)) for i in range(200)]
    path.write_text(json.dumps(oversized), encoding="utf-8")
    events.append(path, EVOLVED, now=999.0)
    got = events.read(path)
    assert len(got) == events.MAX_EVENTS
    assert got[0]["kind"] == "evolved"


def test_max_events_is_fifty():
    assert events.MAX_EVENTS == 50


# --- atomic write --------------------------------------------------------


def test_append_writes_via_os_replace_from_the_same_directory(path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(events.os, "replace", spy)
    events.append(path, HATCHED, now=1.0)
    assert len(seen) == 1, "the log must be swapped in with os.replace"
    src, dst = seen[0]
    assert dst == str(path)
    assert os.path.dirname(src) == os.path.dirname(dst), "tmp must share the fs"


def test_append_leaves_no_temp_files_behind(path):
    events.append(path, HATCHED, now=1.0)
    events.append(path, EVOLVED, now=2.0)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_failed_write_leaves_the_previous_log_intact(path, monkeypatch):
    events.append(path, HATCHED, now=1.0)
    before = path.read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(events.json, "dumps", boom)
    with pytest.raises(OSError):
        events.append(path, EVOLVED, now=2.0)
    assert path.read_text(encoding="utf-8") == before
    monkeypatch.undo()
    assert [e["kind"] for e in events.read(path)] == ["hatched"]


def test_failed_replace_keeps_old_log_and_cleans_up_the_temp_file(path, monkeypatch):
    """Disk full / read-only fs mid-write: no partial log, no tmp litter."""
    events.append(path, HATCHED, now=1.0)
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("read-only file system")

    monkeypatch.setattr(events.os, "replace", boom)
    with pytest.raises(OSError):
        events.append(path, EVOLVED, now=2.0)
    assert path.read_text(encoding="utf-8") == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]
