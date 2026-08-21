"""PokeAPI serves nine generations; the engine draws from five.

The cap exists because the Black/White animated sprite set ends at 649, not
because the data does. These tests pin it as a setting whose default preserves
the engine's behaviour exactly.
"""

from __future__ import annotations

import pytest

from poketokenbar import pokeapi
from poketokenweb import species


@pytest.fixture(autouse=True)
def restore_engine_cap():
    """Rebinding a module global leaks across tests if it is not put back."""
    original = pokeapi.MAX_SPECIES_ID
    yield
    pokeapi.MAX_SPECIES_ID = original


# --- parsing ---------------------------------------------------------------

def test_default_matches_the_engine():
    assert species.configured_max({}) == species.DEFAULT_MAX_SPECIES_ID == 649


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_unset_or_blank_uses_the_default(raw):
    env = {} if raw is None else {"POKETOKENWEB_MAX_SPECIES_ID": raw}
    assert species.configured_max(env) == 649


def test_an_explicit_value_is_honoured():
    assert species.configured_max({"POKETOKENWEB_MAX_SPECIES_ID": "1025"}) == 1025


@pytest.mark.parametrize("raw", ["abc", "1025.5", "-", "0x400"])
def test_junk_falls_back_rather_than_crashing_startup(raw):
    assert species.configured_max({"POKETOKENWEB_MAX_SPECIES_ID": raw}) == 649


@pytest.mark.parametrize("raw", ["0", "-1", "-999"])
def test_a_pool_of_nothing_falls_back(raw):
    # A cap below 1 leaves no species to hatch from at all.
    assert species.configured_max({"POKETOKENWEB_MAX_SPECIES_ID": raw}) == 649


# --- applying to the engine ------------------------------------------------

def test_apply_rebinds_the_engine_global(tmp_path):
    cap = species.apply({"POKETOKENWEB_MAX_SPECIES_ID": "1025"}, tmp_path)
    assert cap == 1025
    # Both engine uses -- the GraphQL bound and the evolution-chain filter --
    # read this global at call time.
    assert pokeapi.MAX_SPECIES_ID == 1025


def test_apply_with_no_setting_leaves_the_engine_default(tmp_path):
    assert species.apply({}, tmp_path) == 649
    assert pokeapi.MAX_SPECIES_ID == 649


# --- cache invalidation ----------------------------------------------------
# base_species_index() returns the cached file BEFORE it builds a query, so
# without this the setting would appear to do nothing at all.

def test_raising_the_cap_drops_the_stale_index(tmp_path):
    index = tmp_path / species.INDEX_FILENAME
    index.write_text('[{"id": 1, "capture_rate": 45}]')
    (tmp_path / species.MARKER_FILENAME).write_text("649")

    species.apply({"POKETOKENWEB_MAX_SPECIES_ID": "1025"}, tmp_path)

    assert not index.exists()
    assert (tmp_path / species.MARKER_FILENAME).read_text() == "1025"


def test_lowering_the_cap_also_drops_it(tmp_path):
    index = tmp_path / species.INDEX_FILENAME
    index.write_text("[]")
    (tmp_path / species.MARKER_FILENAME).write_text("1025")

    species.apply({"POKETOKENWEB_MAX_SPECIES_ID": "151"}, tmp_path)

    assert not index.exists()


def test_an_unchanged_cap_keeps_the_index(tmp_path):
    # Rebuilding on every boot would spend a GraphQL round trip for nothing.
    index = tmp_path / species.INDEX_FILENAME
    index.write_text('[{"id": 1, "capture_rate": 45}]')
    (tmp_path / species.MARKER_FILENAME).write_text("649")

    species.apply({}, tmp_path)

    assert index.exists()
    assert index.read_text() == '[{"id": 1, "capture_rate": 45}]'


def test_a_first_run_records_the_cap_without_needing_an_index(tmp_path):
    species.apply({}, tmp_path)
    assert (tmp_path / species.MARKER_FILENAME).read_text() == "649"


def test_an_index_from_before_this_feature_is_dropped_once(tmp_path):
    # Upgrading in place: an index exists but no marker records its cap, so it
    # cannot be trusted to match.
    index = tmp_path / species.INDEX_FILENAME
    index.write_text("[]")

    species.apply({}, tmp_path)

    assert not index.exists()
    assert (tmp_path / species.MARKER_FILENAME).read_text() == "649"


def test_an_unwritable_cache_does_not_fail_startup(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    # Must not raise: a cache we cannot rewrite is not worth refusing to boot.
    assert species.apply({"POKETOKENWEB_MAX_SPECIES_ID": "1025"}, blocked / "cache") == 1025


def test_apply_without_a_cache_dir_still_sets_the_cap():
    assert species.apply({"POKETOKENWEB_MAX_SPECIES_ID": "386"}, None) == 386
    assert pokeapi.MAX_SPECIES_ID == 386
