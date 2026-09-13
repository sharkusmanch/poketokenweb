"""Tests for poketokenweb.api — pure request/response transforms.

Reachability notes that shape what is (and is not) tested here:

* `sprite_path` values inside a state payload are written ONLY by the engine's
  SpriteStore (companion_store.py), so no attacker-controlled path can reach
  public_state(). The real-world cases are the empty string (egg-stage
  companion, egg shop rows) and correct rewriting at every depth — not
  hostile input.
* `sprite_file()` DOES receive raw URL input, so traversal is genuinely
  reachable there and is tested exhaustively.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from poketokenbar import companion, config, l10n, shop
from poketokenweb import api


# --- module constants ------------------------------------------------------


def test_buy_keys_match_engine_shop_entries():
    expected = {e.key for e in shop.entries(companion.CompanionState())}
    assert api.BUY_KEYS == expected
    # Colon, not hyphen — a hyphenated key would be silently unbuyable.
    assert "egg:uncommon" in api.BUY_KEYS
    assert "egg:rare" in api.BUY_KEYS


def test_use_keys_exclude_unusable_items():
    # shop.use_item raises ShopError("shinyCharm cannot be used").
    assert api.USE_KEYS == frozenset({"rareCandy", "mint"})
    assert "shinyCharm" not in api.USE_KEYS


def test_use_keys_are_all_accepted_by_the_engine():
    """Every advertised USE key must reach a real branch of shop.use_item."""
    for key in api.USE_KEYS:
        state = companion.CompanionState()
        state.inventory[key] = 1
        state.active = companion.MonState(base_id=1, path_ids=[1], planned_path_ids=[1])
        shop.use_item(state, key, rng=__import__("random").Random(0))


def test_shiny_charm_is_rejected_by_the_engine():
    state = companion.CompanionState()
    state.inventory["shinyCharm"] = 1
    with pytest.raises(shop.ShopError):
        shop.use_item(state, "shinyCharm")


def test_web_config_keys_are_a_subset_of_engine_settings():
    assert api.WEB_CONFIG_KEYS <= set(config.DEFAULTS)


def test_web_config_keys_exclude_desktop_panel_settings():
    for key in (
        "floating_pet_enabled",
        "floating_pet_size",
        "floating_pet_bubble_alerts",
        "show_tokens_in_menu",
        "show_cost_in_menu",
        "show_limit_in_menu",
        "limit_notifications",
        "companion_notifications",
        "status_checks_enabled",
    ):
        assert key not in api.WEB_CONFIG_KEYS


def test_language_enum_comes_from_l10n_languages():
    assert set(api.CONFIG_ENUMS["language"]) == set(l10n.LANGUAGES)
    # The port added a fourth language beyond upstream's three.
    assert len(l10n.LANGUAGES) == 4


def test_language_enum_is_not_the_strings_table_keys():
    """l10n.STRINGS is keyed by STRING KEY, not by language code."""
    assert "bag" in l10n.STRINGS  # a string key that is NOT a language
    assert "bag" not in api.CONFIG_ENUMS["language"]
    assert "en" not in l10n.STRINGS
    assert "en" in api.CONFIG_ENUMS["language"]


# --- HARD CONSTRAINT: no filesystem I/O at import --------------------------


def test_import_does_not_touch_the_user_save(tmp_path, monkeypatch):
    """Re-importing api must not read or rename ~/.local/share/.../companion.json.

    A previous version derived BUY_KEYS via save.load(None), which reads the
    real save and _quarantine()s (RENAMES) it when the top level is not a dict.
    """
    import importlib
    import sys

    from poketokenbar import save

    home = tmp_path / "home"
    (home / ".local" / "share" / "poketokenbar").mkdir(parents=True)
    save_file = home / ".local" / "share" / "poketokenbar" / "companion.json"
    save_file.write_text("[]", encoding="utf-8")  # a list: triggers _quarantine
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert save.default_path() == save_file  # the trap is armed

    opened: list[str] = []
    real_open = Path.open

    def spy(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    sys.modules.pop("poketokenweb.api", None)
    importlib.import_module("poketokenweb.api")

    assert save_file.exists(), "import renamed (quarantined) the user's save"
    assert not (save_file.parent / "companion.json.corrupt").exists()
    assert not any("companion.json" in p for p in opened), opened


def test_import_survives_an_undeterminable_home(monkeypatch):
    """Path.home() raising must not break importing the module."""
    import importlib
    import sys

    def boom():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(boom))
    monkeypatch.delenv("HOME", raising=False)
    sys.modules.pop("poketokenweb.api", None)
    module = importlib.import_module("poketokenweb.api")
    assert module.BUY_KEYS


# --- public_state ----------------------------------------------------------


def _payload() -> dict:
    """A payload shaped like state.build() output, sprite_path at every depth."""
    return {
        "schema_version": 1,
        "errors": [],
        "companion": {
            "stage": "mon",
            "sprite_path": "/home/u/.cache/poketokenbar/sprites/25-a.gif",
            "evo_line": [
                {"species_id": 172, "sprite_path": "/home/u/.cache/poketokenbar/sprites/172-s.png"},
                {"species_id": 25, "sprite_path": "/home/u/.cache/poketokenbar/sprites/25-s.png"},
            ],
        },
        "shop": [
            {"key": "rareCandy", "sprite_path": "/c/sprites/item-rare-candy.png"},
            {"key": "egg:rare", "sprite_path": ""},
        ],
        "bag": [{"key": "mint", "sprite_path": "/c/sprites/item-mint.png"}],
        "dex": [{"species_id": 1, "sprite_path": "/c/sprites/1-s.png"}],
        "catch_log": [
            {
                "rarity": "rare",
                "chain": [
                    {"species_id": 1, "sprite_path": "/c/sprites/1-s.png"},
                    {"species_id": 2, "sprite_path": "/c/sprites/2-s.png"},
                    {"species_id": 3, "sprite_path": "/c/sprites/3-s.png"},
                ],
            }
        ],
        "panel": {"sprite_path": "/c/sprites/25-a.gif"},
    }


SPRITE_DIR = Path("/home/u/.cache/poketokenbar/sprites")


def test_public_state_rewrites_top_level_companion_sprite():
    out = api.public_state(_payload(), SPRITE_DIR)
    assert out["companion"]["sprite_path"] == "/sprites/25-a.gif"


def test_public_state_rewrites_evo_line_sprites():
    out = api.public_state(_payload(), SPRITE_DIR)
    assert [f["sprite_path"] for f in out["companion"]["evo_line"]] == [
        "/sprites/172-s.png",
        "/sprites/25-s.png",
    ]


def test_public_state_rewrites_shop_bag_and_dex_rows():
    out = api.public_state(_payload(), SPRITE_DIR)
    assert out["shop"][0]["sprite_path"] == "/sprites/item-rare-candy.png"
    assert out["bag"][0]["sprite_path"] == "/sprites/item-mint.png"
    assert out["dex"][0]["sprite_path"] == "/sprites/1-s.png"
    assert out["panel"]["sprite_path"] == "/sprites/25-a.gif"


def test_public_state_rewrites_nested_catch_log_chain():
    """Deepest nesting: catch_log[] -> chain[] -> sprite_path."""
    out = api.public_state(_payload(), SPRITE_DIR)
    assert [link["sprite_path"] for link in out["catch_log"][0]["chain"]] == [
        "/sprites/1-s.png",
        "/sprites/2-s.png",
        "/sprites/3-s.png",
    ]


def test_public_state_keeps_empty_sprite_path_empty():
    """Real case: an egg-stage companion and egg shop rows carry ''."""
    payload = _payload()
    payload["companion"] = {"stage": "egg", "sprite_path": ""}
    out = api.public_state(payload, SPRITE_DIR)
    assert out["companion"]["sprite_path"] == ""
    assert out["shop"][1]["sprite_path"] == ""


def test_public_state_maps_missing_sprite_to_empty_string():
    payload = _payload()
    payload["companion"]["sprite_path"] = None
    out = api.public_state(payload, SPRITE_DIR)
    assert out["companion"]["sprite_path"] == ""


def test_public_state_does_not_mutate_the_input():
    payload = _payload()
    out = api.public_state(payload, SPRITE_DIR)
    assert payload["companion"]["sprite_path"].startswith("/home/u/")
    assert payload["catch_log"][0]["chain"][0]["sprite_path"] == "/c/sprites/1-s.png"
    # And the copy is deep: mutating the result cannot reach the source.
    out["catch_log"][0]["chain"].append({"sprite_path": ""})
    assert len(payload["catch_log"][0]["chain"]) == 3
    out["errors"].append("x")
    assert payload["errors"] == []


def test_public_state_preserves_non_sprite_values():
    out = api.public_state(_payload(), SPRITE_DIR)
    assert out["schema_version"] == 1
    assert out["companion"]["stage"] == "mon"
    assert out["catch_log"][0]["rarity"] == "rare"


def test_public_state_on_a_real_engine_payload_leaks_no_absolute_path():
    """End-to-end shape check against poketokenbar.state.build()."""
    from poketokenbar import state as engine_state

    payload = engine_state.build(
        daily_by_provider={},
        config_values={"language": "en"},
        errors=["claude_code: cannot read /home/u/.claude/.credentials.json"],
        companion_payload={"stage": "egg", "sprite_path": ""},
        shop_payload=[{"key": "mint", "sprite_path": "/c/sprites/item-mint.png"}],
        catch_log=[{"chain": [{"sprite_path": "/c/sprites/1-s.png"}]}],
    )
    out = api.public_state(payload, SPRITE_DIR)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "sprite_path":
                    assert value == "" or value.startswith("/sprites/"), value
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(out)
    assert "/home/u/.claude" not in out["errors"][0]


# --- error sanitizing ------------------------------------------------------


def test_errors_are_sanitized_multi_segment_path():
    payload = _payload()
    payload["errors"] = ["cannot read /config/.claude/.credentials.json: denied"]
    out = api.public_state(payload, SPRITE_DIR)
    assert "/config/.claude/.credentials.json" not in out["errors"][0]
    assert "cannot read" in out["errors"][0]
    assert "denied" in out["errors"][0]


def test_errors_are_sanitized_single_segment_path():
    """The old `(/[\\w.\\-]+){2,}` regex let /data and /tmp through entirely."""
    payload = _payload()
    payload["errors"] = ["cannot read /data", "spool /tmp is not writable"]
    out = api.public_state(payload, SPRITE_DIR)
    assert "/data" not in out["errors"][0]
    assert "/tmp" not in out["errors"][1]
    assert "is not writable" in out["errors"][1]


def test_errors_keep_urls_intact():
    """The old regex mangled https://api.anthropic.com/api/oauth/usage."""
    payload = _payload()
    payload["errors"] = [
        "HTTP 429 from https://api.anthropic.com/api/oauth/usage",
        "http://localhost:8080/state failed",
    ]
    out = api.public_state(payload, SPRITE_DIR)
    assert out["errors"][0] == "HTTP 429 from https://api.anthropic.com/api/oauth/usage"
    assert out["errors"][1] == "http://localhost:8080/state failed"


def test_errors_sanitize_home_paths_and_keep_diagnosis():
    payload = _payload()
    payload["errors"] = ["save quarantined: ~/.local/share/poketokenbar/companion.json"]
    out = api.public_state(payload, SPRITE_DIR)
    assert "poketokenbar/companion.json" not in out["errors"][0]
    assert "save quarantined" in out["errors"][0]


def test_errors_do_not_redact_non_path_slashes():
    payload = _payload()
    payload["errors"] = ["used 50/100 tokens and/or cache"]
    out = api.public_state(payload, SPRITE_DIR)
    assert out["errors"][0] == "used 50/100 tokens and/or cache"


def test_errors_survive_non_string_entries():
    payload = _payload()
    payload["errors"] = [None, 42]
    out = api.public_state(payload, SPRITE_DIR)
    assert out["errors"] == ["None", "42"]


def test_errors_missing_key_is_tolerated():
    out = api.public_state({"companion": {"sprite_path": ""}}, SPRITE_DIR)
    assert out["errors"] == []


# --- sprite_file -----------------------------------------------------------


@pytest.fixture()
def sprite_dir(tmp_path) -> Path:
    directory = tmp_path / "sprites"
    directory.mkdir()
    (directory / "25-a.gif").write_bytes(b"GIF89a")
    (directory / "item-mint.png").write_bytes(b"PNG")
    (tmp_path / "secret.txt").write_text("token", encoding="utf-8")
    return directory


def test_sprite_file_returns_a_real_file(sprite_dir):
    found = api.sprite_file("25-a.gif", sprite_dir)
    assert found is not None
    assert found.read_bytes() == b"GIF89a"
    assert found.name == "25-a.gif"


def test_sprite_file_returns_none_for_a_missing_file(sprite_dir):
    assert api.sprite_file("999-a.gif", sprite_dir) is None


def test_sprite_file_returns_none_for_a_directory(sprite_dir):
    (sprite_dir / "nested").mkdir()
    assert api.sprite_file("nested", sprite_dir) is None


def test_sprite_file_returns_none_when_dir_missing(tmp_path):
    assert api.sprite_file("25-a.gif", tmp_path / "nope") is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../secret.txt",
        "../../etc/passwd",
        "..%2Fsecret.txt",
        "%2e%2e%2fsecret.txt",
        "%2E%2E/secret.txt",
        "..\\secret.txt",
        "/etc/passwd",
        "/tmp/secret.txt",
        "sub/25-a.gif",
        "./25-a.gif",
        "nested/../../secret.txt",
        "25-a.gif\x00.png",
        "  ",
        "~/secret.txt",
    ],
)
def test_sprite_file_rejects_traversal_and_junk(sprite_dir, name):
    assert api.sprite_file(name, sprite_dir) is None


@pytest.mark.parametrize("name", [None, 42, 3.5, True, {"a": 1}, ["25-a.gif"], b"25-a.gif"])
def test_sprite_file_rejects_non_string_names(sprite_dir, name):
    assert api.sprite_file(name, sprite_dir) is None


def test_sprite_file_rejects_subdirectory_that_really_exists(sprite_dir):
    """Containment must be parent-of, not merely 'under' the sprite dir."""
    nested = sprite_dir / "sub"
    nested.mkdir()
    (nested / "inner.png").write_bytes(b"PNG")
    assert (nested / "inner.png").is_file()
    assert api.sprite_file("sub/inner.png", sprite_dir) is None


def test_sprite_file_rejects_symlink_escaping_the_directory(sprite_dir):
    outside = sprite_dir.parent / "secret.txt"
    link = sprite_dir / "escape.png"
    os.symlink(outside, link)
    assert link.is_file()  # the naive check would pass
    assert api.sprite_file("escape.png", sprite_dir) is None


def test_sprite_file_rejects_symlink_into_a_subdirectory(sprite_dir):
    """"Directly inside" means parent-of, even for a link that stays under root.

    Without a subdirectory in the name the filename filter cannot see this, so
    the resolve()/parent check is the only thing enforcing the contract.
    """
    nested = sprite_dir / "sub"
    nested.mkdir()
    (nested / "inner.png").write_bytes(b"PNG")
    os.symlink(nested / "inner.png", sprite_dir / "deep.png")
    assert (sprite_dir / "deep.png").is_file()
    assert api.sprite_file("deep.png", sprite_dir) is None


def test_sprite_file_rejects_symlinked_directory_hop(sprite_dir, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "loot.png").write_bytes(b"PNG")
    os.symlink(elsewhere, sprite_dir / "hop")
    assert api.sprite_file("hop/loot.png", sprite_dir) is None


def test_sprite_file_allows_a_symlink_that_stays_inside(sprite_dir):
    os.symlink(sprite_dir / "25-a.gif", sprite_dir / "alias.gif")
    found = api.sprite_file("alias.gif", sprite_dir)
    assert found is not None and found.read_bytes() == b"GIF89a"


def test_sprite_file_accepts_a_relative_sprite_dir(sprite_dir, monkeypatch):
    monkeypatch.chdir(sprite_dir.parent)
    assert api.sprite_file("25-a.gif", Path("sprites")) is not None


# --- validate_command ------------------------------------------------------


def test_validate_command_refresh():
    assert api.validate_command({"name": "refresh"}) == ("refresh", {})


def test_validate_command_refresh_drops_extra_args():
    assert api.validate_command({"name": "refresh", "args": {"path": "/etc"}}) == (
        "refresh",
        {},
    )


@pytest.mark.parametrize("key", sorted({e.key for e in shop.entries(companion.CompanionState())}))
def test_validate_command_buy_accepts_every_shop_key(key):
    assert api.validate_command({"name": "buy", "args": {"key": key}}) == (
        "buy",
        {"key": key},
    )


@pytest.mark.parametrize("key", ["rareCandy", "mint"])
def test_validate_command_use_accepts_usable_items(key):
    assert api.validate_command({"name": "use", "args": {"key": key}}) == (
        "use",
        {"key": key},
    )


def test_validate_command_use_rejects_shiny_charm():
    with pytest.raises(ValueError):
        api.validate_command({"name": "use", "args": {"key": "shinyCharm"}})


def test_validate_command_buy_rejects_hyphenated_egg_key():
    with pytest.raises(ValueError):
        api.validate_command({"name": "buy", "args": {"key": "egg-rare"}})


@pytest.mark.parametrize("name", ["reload_config", "export", "import"])
@pytest.mark.parametrize(
    "args",
    [
        {"path": "/etc/passwd"},
        # An otherwise-valid args block, so the rejection has to come from the
        # NAME allowlist. With only the {"path": ...} case, widening
        # COMMAND_NAMES left the test green: it was really failing on the
        # missing "key", not on the command name.
        {"key": "mint"},
        {"key": "egg:rare"},
        {},
    ],
)
def test_validate_command_rejects_server_internal_commands(name, args):
    with pytest.raises(ValueError):
        api.validate_command({"name": name, "args": args})


@pytest.mark.parametrize("name", ["", "REFRESH", "buy ", "quit", "__init__", "drain"])
@pytest.mark.parametrize("args", [{}, {"key": "mint"}])
def test_validate_command_rejects_unknown_names(name, args):
    # args is valid-looking so the rejection must come from the name.
    with pytest.raises(ValueError):
        api.validate_command({"name": name, "args": args})


@pytest.mark.parametrize("name", [None, 42, 3.5, True, {"a": 1}, ["refresh"], b"refresh"])
def test_validate_command_rejects_non_string_names(name):
    with pytest.raises(ValueError):
        api.validate_command({"name": name})


@pytest.mark.parametrize("key", [{}, {"a": 1}, ["rareCandy"], 42, None, True, 3.5, b"mint"])
def test_validate_command_rejects_unhashable_or_wrong_typed_keys(key):
    """`if key not in allowed` raised TypeError: unhashable type: 'dict'."""
    for name in ("buy", "use"):
        with pytest.raises(ValueError):
            api.validate_command({"name": name, "args": {"key": key}})


def test_validate_command_rejects_missing_args_for_buy():
    with pytest.raises(ValueError):
        api.validate_command({"name": "buy"})


@pytest.mark.parametrize("args", [[], "key", 5, None])
def test_validate_command_rejects_non_dict_args(args):
    with pytest.raises(ValueError):
        api.validate_command({"name": "buy", "args": args})


@pytest.mark.parametrize("body", [None, [], "refresh", 7, {"name": {}}])
def test_validate_command_rejects_non_dict_body(body):
    with pytest.raises(ValueError):
        api.validate_command(body)


def test_validate_command_never_raises_typeerror_for_any_json(sprite_dir):
    import itertools

    values = [{}, [], 0, 1, "", "x", None, True, 3.5, {"key": {}}, [[]]]
    for name, key in itertools.product(values, values):
        try:
            api.validate_command({"name": name, "args": {"key": key}})
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover - failure path
            pytest.fail(f"{name!r}/{key!r} raised {type(exc).__name__}: {exc}")


# --- validate_config -------------------------------------------------------


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("refresh_interval", 30, "30"),
        ("refresh_interval", 3600, "3600"),
        ("refresh_interval", "120", "120"),
        ("warn_threshold", 1, "1"),
        ("warn_threshold", 100, "100"),
        ("crit_threshold", 95, "95"),
        ("limit_display_mode", "session", "session"),
        ("limit_display_mode", "weekly", "weekly"),
        ("limit_display_mode", "both", "both"),
        ("language", "en", "en"),
        ("language", "ko", "ko"),
        ("language", "ja", "ja"),
        ("language", "es", "es"),
    ],
)
def test_validate_config_accepts_valid_settings(key, value, expected):
    assert api.validate_config({"key": key, "value": value}) == (key, expected)


def test_validate_config_returns_strings_for_config_set_value():
    key, value = api.validate_config({"key": "refresh_interval", "value": 300})
    assert isinstance(value, str)
    # The engine coerces from string; prove the round trip.
    assert config._coerce(key, value) == 300


@pytest.mark.parametrize(
    "key",
    [
        "floating_pet_enabled",
        "floating_pet_size",
        "floating_pet_bubble_alerts",
        "show_tokens_in_menu",
        "show_cost_in_menu",
        "show_limit_in_menu",
        "limit_notifications",
        "companion_notifications",
        "status_checks_enabled",
    ],
)
def test_validate_config_rejects_desktop_panel_keys(key):
    with pytest.raises(ValueError):
        api.validate_config({"key": key, "value": "true"})


@pytest.mark.parametrize("key", ["", "nope", "__class__", "REFRESH_INTERVAL"])
def test_validate_config_rejects_unknown_keys(key):
    with pytest.raises(ValueError):
        api.validate_config({"key": key, "value": "1"})


@pytest.mark.parametrize("value", [0, -1, -3600, 29, 3601, 100000])
def test_validate_config_rejects_out_of_range_refresh_interval(value):
    """0 or negative makes the daemon poll loop spin with no sleep."""
    with pytest.raises(ValueError):
        api.validate_config({"key": "refresh_interval", "value": value})


@pytest.mark.parametrize("value", [0, -1, 101, 1000])
@pytest.mark.parametrize("key", ["warn_threshold", "crit_threshold"])
def test_validate_config_rejects_out_of_range_thresholds(key, value):
    with pytest.raises(ValueError):
        api.validate_config({"key": key, "value": value})


@pytest.mark.parametrize("value", ["", "fast", "120.5", "0x10", "1e3", " ", "12 34"])
def test_validate_config_rejects_non_integer_numeric_values(value):
    with pytest.raises(ValueError):
        api.validate_config({"key": "refresh_interval", "value": value})


@pytest.mark.parametrize("value", [True, False])
def test_validate_config_rejects_booleans_for_numeric_keys(value):
    with pytest.raises(ValueError):
        api.validate_config({"key": "warn_threshold", "value": value})


@pytest.mark.parametrize("value", ["", "SESSION", "monthly", "session,weekly"])
def test_validate_config_rejects_bad_limit_display_mode(value):
    with pytest.raises(ValueError):
        api.validate_config({"key": "limit_display_mode", "value": value})


@pytest.mark.parametrize("value", ["", "EN", "fr", "en-US", "bag", "shop", "home"])
def test_validate_config_rejects_bad_language(value):
    """'bag'/'shop'/'home' are l10n.STRINGS keys, not languages."""
    with pytest.raises(ValueError):
        api.validate_config({"key": "language", "value": value})


@pytest.mark.parametrize("key", [{}, [], 42, None, True, b"language"])
def test_validate_config_rejects_unhashable_or_wrong_typed_keys(key):
    with pytest.raises(ValueError):
        api.validate_config({"key": key, "value": "en"})


@pytest.mark.parametrize("value", [{}, [], None, {"a": 1}, ["en"], b"en", 3.5])
def test_validate_config_rejects_unhashable_or_wrong_typed_values(value):
    for key in ("language", "limit_display_mode", "refresh_interval"):
        with pytest.raises(ValueError):
            api.validate_config({"key": key, "value": value})


@pytest.mark.parametrize("body", [None, [], "language", 7, {}, {"key": "language"}])
def test_validate_config_rejects_malformed_bodies(body):
    with pytest.raises(ValueError):
        api.validate_config(body)


def test_validate_config_never_raises_typeerror_for_any_json():
    import itertools

    values = [{}, [], 0, 1, "", "en", None, True, 3.5, {"key": []}, [[]], "30"]
    for key, value in itertools.product(values, values):
        try:
            api.validate_config({"key": key, "value": value})
        except ValueError:
            pass
        except Exception as exc:  # pragma: no cover - failure path
            pytest.fail(f"{key!r}/{value!r} raised {type(exc).__name__}: {exc}")


# --- account redaction -----------------------------------------------------
# This app has no authentication of its own and is meant to be self-hosted by
# strangers, so the operator's Anthropic identity must not ride along in a
# payload that any client can fetch.

_ACCOUNT_PAYLOAD = {
    "limits": {
        "session": {"utilization": 83.0, "resets_at": None, "severity": "normal"},
        "plan": "max",
        "account": {
            "uuid": "71647f1c-8e7c-48a6-975c-3144960e8741",
            "email": "someone@example.com",
            "display_name": "Marcus",
            "organization": "someone@example.com's Organization",
        },
    },
    "errors": [],
}


def test_public_state_strips_account_identity(tmp_path):
    out = api.public_state(_ACCOUNT_PAYLOAD, tmp_path)
    assert out["limits"]["account"] == {"display_name": "Marcus"}


@pytest.mark.parametrize("secret", [
    "71647f1c-8e7c-48a6-975c-3144960e8741",
    "someone@example.com",
    "Organization",
])
def test_no_account_secret_survives_serialisation(tmp_path, secret):
    import json as _json
    assert secret not in _json.dumps(api.public_state(_ACCOUNT_PAYLOAD, tmp_path))


def test_display_name_is_preserved_for_the_ui(tmp_path):
    out = api.public_state(_ACCOUNT_PAYLOAD, tmp_path)
    assert out["limits"]["account"]["display_name"] == "Marcus"
    assert out["limits"]["plan"] == "max"


def test_redaction_tolerates_a_missing_or_odd_account(tmp_path):
    assert api.public_state({"limits": {}, "errors": []}, tmp_path)["limits"] == {}
    assert api.public_state({"limits": {"account": None}, "errors": []}, tmp_path)
    assert api.public_state({"errors": []}, tmp_path) is not None


def test_redaction_does_not_mutate_the_callers_payload(tmp_path):
    import copy as _copy
    original = _copy.deepcopy(_ACCOUNT_PAYLOAD)
    api.public_state(_ACCOUNT_PAYLOAD, tmp_path)
    assert _ACCOUNT_PAYLOAD == original


# --- forecasts that outlive the window they forecast ------------------------
# Reported as "I don't think it's using my timezone": the app said "at this
# rate, full at 05:08" while the 5-hour window reset at 20:00. The hour was
# correct LOCAL arithmetic for an instant that cannot arrive -- the window
# empties at 20:00, so 100% is never reached and the ETA is fiction.
#
# The engine fits a slope and extrapolates to 100% knowing nothing about
# resets_at, so nothing downstream had ever compared the two. That is why the
# whole suite stayed green: burn and limits were both passed through
# public_state untouched, and no test asserted anything about their relation.

# 18:42:49 PDT on 2026-08-21 -- the reference the real report was captured at.
_NOW = 1787362969.0
_RESET_SOON = "2026-08-22T03:00:00+00:00"   # 20:00 PDT, ~77 min later
_RESET_LATER = "2026-08-24T15:00:00+00:00"  # ~2.8 days later
_RESET_SOON_EPOCH = 1787367600.0             # _RESET_SOON as an epoch


def _burn_payload(session_minutes=None, weekly_minutes=None, **overrides):
    burn = {}
    if session_minutes is not None:
        burn["session"] = {
            "rate_per_minute": 0.1119,
            "minutes_to_full": session_minutes,
            "eta_text": "05:08",
        }
    if weekly_minutes is not None:
        burn["weekly"] = {
            "rate_per_minute": 0.0224,
            "minutes_to_full": weekly_minutes,
            "eta_text": "11:50",
        }
    payload = {
        "updated_at": _NOW,
        "burn": burn,
        "limits": {
            "session": {"utilization": 30.0, "resets_at": _RESET_SOON},
            "weekly": {"utilization": 77.0, "resets_at": _RESET_LATER},
        },
    }
    payload.update(overrides)
    return payload


def test_a_forecast_past_the_reset_is_dropped(tmp_path):
    # 626 minutes out, but the window resets in 77.
    out = api.public_state(_burn_payload(session_minutes=626), tmp_path)
    assert out["burn"]["session"]["eta_text"] == ""
    assert out["burn"]["session"]["minutes_to_full"] is None
    # The burn itself is real even though the cap is unreachable.
    assert out["burn"]["session"]["rate_per_minute"] == 0.1119


def test_a_forecast_inside_the_window_is_untouched(tmp_path):
    # 60 minutes out, window resets in 77 -- this one really can happen.
    out = api.public_state(_burn_payload(session_minutes=60), tmp_path)
    assert out["burn"]["session"]["eta_text"] == "05:08"
    assert out["burn"]["session"]["minutes_to_full"] == 60


def test_a_forecast_landing_exactly_on_the_reset_is_dropped(tmp_path):
    # The window empties at the reset rather than filling, so the boundary
    # belongs to "cannot happen".
    minutes = (_RESET_SOON_EPOCH - _NOW) / 60.0
    out = api.public_state(_burn_payload(session_minutes=minutes), tmp_path)
    assert out["burn"]["session"]["eta_text"] == ""


def test_one_second_before_the_reset_still_shows(tmp_path):
    minutes = (_RESET_SOON_EPOCH - _NOW - 1.0) / 60.0
    out = api.public_state(_burn_payload(session_minutes=minutes), tmp_path)
    assert out["burn"]["session"]["eta_text"] == "05:08"


def test_each_window_is_judged_against_its_own_reset(tmp_path):
    """The real payload: session unreachable, weekly genuinely reachable.

    Judging both against one reset -- or dropping the whole burn block when any
    forecast is impossible -- would silently lose the weekly forecast.
    """
    out = api.public_state(
        _burn_payload(session_minutes=626, weekly_minutes=1028), tmp_path
    )
    assert out["burn"]["session"]["eta_text"] == ""
    assert out["burn"]["weekly"]["eta_text"] == "11:50"
    assert out["burn"]["weekly"]["minutes_to_full"] == 1028


def test_a_flat_rate_forecast_is_left_alone(tmp_path):
    # The engine already emits rate-with-no-ETA when the slope is flat.
    payload = _burn_payload()
    payload["burn"] = {
        "session": {"rate_per_minute": 0.0, "minutes_to_full": None, "eta_text": ""}
    }
    out = api.public_state(payload, tmp_path)
    assert out["burn"]["session"] == {
        "rate_per_minute": 0.0,
        "minutes_to_full": None,
        "eta_text": "",
    }


def test_without_updated_at_the_forecast_is_not_second_guessed(tmp_path):
    # minutes_to_full cannot be placed on a timeline without the instant it was
    # measured from. Guessing with now() would suppress real forecasts whenever
    # the state file is stale.
    payload = _burn_payload(session_minutes=626)
    del payload["updated_at"]
    out = api.public_state(payload, tmp_path)
    assert out["burn"]["session"]["eta_text"] == "05:08"


@pytest.mark.parametrize("bad", ["", "not a date", "2026-13-45T99:00:00+00:00", None, 12345])
def test_an_unusable_reset_time_does_not_suppress_the_forecast(bad, tmp_path):
    payload = _burn_payload(session_minutes=626)
    payload["limits"]["session"]["resets_at"] = bad
    out = api.public_state(payload, tmp_path)
    assert out["burn"]["session"]["eta_text"] == "05:08"


def test_a_window_with_no_limits_entry_is_left_alone(tmp_path):
    payload = _burn_payload(session_minutes=626)
    del payload["limits"]["session"]
    out = api.public_state(payload, tmp_path)
    assert out["burn"]["session"]["eta_text"] == "05:08"


def test_a_missing_burn_or_limits_block_is_not_an_error(tmp_path):
    api.public_state({"updated_at": _NOW, "limits": {}}, tmp_path)
    api.public_state({"updated_at": _NOW, "burn": {}}, tmp_path)
    api.public_state({"burn": None, "limits": None}, tmp_path)


def test_the_callers_payload_is_never_mutated(tmp_path):
    import copy as _copy
    payload = _burn_payload(session_minutes=626)
    original = _copy.deepcopy(payload)
    api.public_state(payload, tmp_path)
    assert payload == original


# --- difficulty: float settings (#244/#287) ---------------------------------


class TestDifficultyValidation:
    def test_both_multipliers_are_web_settable(self):
        assert "growth_difficulty" in api.WEB_CONFIG_KEYS
        assert "shop_difficulty" in api.WEB_CONFIG_KEYS

    @pytest.mark.parametrize("key", ["growth_difficulty", "shop_difficulty"])
    @pytest.mark.parametrize("value", [0.1, 0.5, 1, 1.0, 2.0, "0.75"])
    def test_a_value_inside_the_range_is_accepted(self, key, value):
        got_key, got_value = api.validate_config({"key": key, "value": value})
        assert got_key == key
        assert 0.1 <= float(got_value) <= 2.0

    @pytest.mark.parametrize("value", [0, 0.05, 2.1, 100, -1])
    def test_a_value_outside_the_range_is_rejected(self, value):
        with pytest.raises(api.ValidationError):
            api.validate_config({"key": "growth_difficulty", "value": value})

    @pytest.mark.parametrize("value", ["NaN", "inf", "-inf"])
    def test_a_non_finite_value_is_rejected_rather_than_clamped(self, value):
        """Silently turning NaN into 2.0 would hide a client bug. The engine's
        own clamp still catches a hand-edited config file."""
        with pytest.raises(api.ValidationError):
            api.validate_config({"key": "growth_difficulty", "value": value})

    @pytest.mark.parametrize("value", [True, None, {}, [], "abc"])
    def test_a_non_numeric_value_is_rejected(self, value):
        # bool is an int subclass, so True would otherwise sail through as 1.0.
        with pytest.raises(api.ValidationError):
            api.validate_config({"key": "shop_difficulty", "value": value})

    def test_the_accepted_string_round_trips_through_the_engine_coercion(self):
        from poketokenbar import config

        _, value = api.validate_config({"key": "growth_difficulty", "value": 0.75})
        assert config._coerce("growth_difficulty", value) == pytest.approx(0.75)
