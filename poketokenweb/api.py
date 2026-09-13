"""Pure request/response transforms for the web layer.

Everything here is a function of its arguments. The only filesystem contact in
the whole module is the stat() inside `sprite_file()`, which has to ask whether
one requested sprite really exists.

IMPORT-TIME I/O IS FORBIDDEN HERE.
    An earlier version derived the shop allowlist with

        BUY_KEYS = frozenset(e.key for e in shop.entries(save.load(None)))

    `save.load(None)` falls back to `save.default_path()` — the *real* user save
    at ~/.local/share/poketokenbar/companion.json — reads it, and, when its top
    level is not a dict, calls `_quarantine()`, which RENAMES the user's save to
    companion.json.corrupt. Merely importing this module (a test collection, a
    `--help`, an autoreload) destroyed save data. It also raised at import when
    Path.home() was undeterminable. The keys are therefore derived from a fresh
    in-memory CompanionState, which touches no disk at all.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from poketokenbar import companion, l10n, shop

# --- allowlists ------------------------------------------------------------

# Derived from the engine so a new shop entry cannot be silently unbuyable.
# A FRESH in-memory state: no save file is read (see the module docstring).
# Yields {"rareCandy", "mint", "shinyCharm", "egg", "egg:uncommon", "egg:rare"}
# — the egg tiers use a COLON, matching shop.buy()'s tier lookup table.
BUY_KEYS: frozenset[str] = frozenset(
    entry.key for entry in shop.entries(companion.CompanionState())
)

# Only what shop.use_item() actually consumes. "shinyCharm" is passive and
# permanent — use_item falls through to ShopError("shinyCharm cannot be used"),
# so advertising it would offer the browser a button that can only fail.
USE_KEYS: frozenset[str] = frozenset({"rareCandy", "mint"})

COMMAND_NAMES: frozenset[str] = frozenset({"refresh", "buy", "use"})

# Settings a browser client may change. Deliberately excluded: every
# show_*_in_menu / floating_pet_* / *_notifications key, which configure a
# macOS-style menu bar and desktop notifications that do not exist here.
# "reload_config", "export" and "import" are likewise not commands: they take
# server-side filesystem paths and are the daemon's own business.
WEB_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "refresh_interval",
        "warn_threshold",
        "crit_threshold",
        "limit_display_mode",
        "language",
        "growth_difficulty",
        "shop_difficulty",
    }
)

# Inclusive bounds. refresh_interval's floor matters: the value is persisted to
# disk and drives the daemon's poll loop, so 0 or a negative interval makes that
# loop spin without sleeping.
CONFIG_RANGES: dict[str, tuple[int, int]] = {
    "refresh_interval": (30, 3600),
    "warn_threshold": (1, 100),
    "crit_threshold": (1, 100),
}

# Inclusive float bounds, mirroring poketokenbar.balance. Narrower than the
# engine's clamp on purpose: the clamp is a last line of defence against a
# hand-edited config file, while this rejects the request outright so the UI
# can say why.
CONFIG_FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "growth_difficulty": (0.1, 2.0),
    "shop_difficulty": (0.1, 2.0),
}

CONFIG_ENUMS: dict[str, tuple[str, ...]] = {
    # limits.windows()/panel_text() branch on exactly these three.
    "limit_display_mode": ("session", "weekly", "both"),
    # From l10n.LANGUAGES — the tuple of language CODES. NOT l10n.STRINGS,
    # whose keys are string ids ("bag", "shop", ...): using those would accept
    # "bag" as a language and reject "en".
    "language": tuple(l10n.LANGUAGES),
}


class ValidationError(ValueError):
    """Rejected client input. A ValueError so callers can catch either."""


# --- error sanitizing ------------------------------------------------------

REDACTED = "<path>"

# Matched first and left untouched: a URL is diagnosis, not a filesystem leak.
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# An absolute (or ~-rooted) filesystem path. The leading lookbehind stops it
# from eating the "/100" in "50/100" or the "/or" in "and/or".
#
# The predecessor `(/[\w.\-]+){2,}` was wrong twice over: the {2,} let
# single-segment paths such as /data and /tmp through untouched, and with no
# URL handling it rewrote "https://api.anthropic.com/api/oauth/usage" into
# "https:/<path>", shredding the very diagnosis it claimed to keep.
_PATH_RE = re.compile(r"(?<![\w.\-])~?/[\w.\-]+(?:/[\w.\-]+)*/?")


def sanitize_error(text: object) -> str:
    """Strip filesystem paths from one engine error string.

    The engine puts raw exception text in errors[] — e.g. limits.py raises
    "cannot read /home/u/.claude/.credentials.json" — and that list is served
    straight to the browser.
    """
    text = text if isinstance(text, str) else str(text)
    out: list[str] = []
    cursor = 0
    for match in _URL_RE.finditer(text):
        out.append(_PATH_RE.sub(REDACTED, text[cursor : match.start()]))
        out.append(match.group(0))  # URLs survive verbatim
        cursor = match.end()
    out.append(_PATH_RE.sub(REDACTED, text[cursor:]))
    return "".join(out)


# --- public_state ----------------------------------------------------------


def _sprite_url(value: object) -> str:
    """Map one engine sprite_path to a browser URL, or "" when there is none."""
    if not isinstance(value, str) or not value:
        return ""
    name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return f"/sprites/{name}" if name else ""


def _rewrite(node):
    """Rebuild `node`, replacing every value under a "sprite_path" key.

    sprite_path occurs at many depths — companion, each form of evo_line, every
    shop row, every bag row, every dex entry, and each link of every
    catch_log[].chain — so this walks rather than patching known locations.
    Rebuilding also gives the deep copy: no container is shared with the input.
    """
    if isinstance(node, dict):
        return {
            key: (_sprite_url(value) if key == "sprite_path" else _rewrite(value))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_rewrite(item) for item in node]
    return node


def public_state(payload: dict, sprite_dir: Path) -> dict:
    """The browser-facing view of a state.json payload.

    Deep-copied, so the caller's cached payload is never mutated. Local sprite
    paths become /sprites/<filename> URLs and errors[] loses its filesystem
    paths.

    `sprite_dir` is the directory those files are served from. It is not read
    here — the rewrite is pure string work, and validating a sprite's existence
    is `sprite_file()`'s job at request time — but it is part of the signature
    so the URL mapping and the file lookup stay declared together.
    """
    source = payload if isinstance(payload, dict) else {}
    out = _rewrite(source)
    raw_errors = source.get("errors")
    out["errors"] = [
        sanitize_error(entry)
        for entry in (raw_errors if isinstance(raw_errors, list) else [])
    ]
    _redact_account(out)
    _drop_forecasts_past_the_reset(out)
    return out


# The engine fits a slope to the utilization samples and extrapolates it to
# 100%. It is never told when the window RESETS, so it will happily forecast an
# ETA hours past the point where utilization drops back to zero -- "at this
# rate, full at 05:08" for a 5-hour window that resets at 20:00. The hour is
# correct arithmetic for a moment that cannot arrive, which reads as a wrong
# clock rather than a wrong forecast (this was reported as a timezone bug).
#
# Dropped rather than clamped to the reset: the window does not fill at the
# reset, it empties. There is no true ETA to show, and the engine already has a
# "no meaningful ETA" shape -- a rate with no eta_text, which the UI hides --
# so this reuses it instead of inventing a second one.
#
# rate_per_minute is kept: the burn is real even when the cap is not reachable.


def _forecast_reference(payload: dict) -> float | None:
    """The instant the forecast was computed from."""
    updated = payload.get("updated_at")
    return float(updated) if isinstance(updated, (int, float)) else None


def _resets_at_epoch(limits: dict, kind: str) -> float | None:
    window = limits.get(kind)
    if not isinstance(window, dict):
        return None
    raw = window.get("resets_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        # An unparseable reset is not a reason to suppress a forecast.
        return None


def _drop_forecasts_past_the_reset(out: dict) -> None:
    burn = out.get("burn")
    limits = out.get("limits")
    if not isinstance(burn, dict) or not isinstance(limits, dict):
        return
    reference = _forecast_reference(out)
    if reference is None:
        # Without the instant the forecast was made, minutes_to_full cannot be
        # placed on a timeline. Leave it alone rather than guess with now().
        return

    for kind, forecast in burn.items():
        if not isinstance(forecast, dict):
            continue
        minutes = forecast.get("minutes_to_full")
        if not isinstance(minutes, (int, float)):
            continue
        resets_at = _resets_at_epoch(limits, kind)
        if resets_at is None:
            continue
        if reference + minutes * 60.0 >= resets_at:
            forecast["minutes_to_full"] = None
            forecast["eta_text"] = ""


# Of the account block the engine attaches to limits, only the display name is
# ever rendered. The rest -- uuid, email, organization -- is the operator's
# identity, and this app ships with no authentication of its own, so a stranger
# who exposes it (a LAN, a reverse proxy, a port-forward) would publish their
# Anthropic account details. Serve only what the UI uses.
ACCOUNT_PUBLIC_KEYS = ("display_name",)


def _redact_account(out: dict) -> None:
    limits = out.get("limits")
    if not isinstance(limits, dict):
        return
    account = limits.get("account")
    if not isinstance(account, dict):
        return
    limits["account"] = {
        key: account[key] for key in ACCOUNT_PUBLIC_KEYS if key in account
    }


# --- sprite_file -----------------------------------------------------------

# Sprite cache filenames are "<id>-<flags>.gif|png" and "item-<name>.png".
# Requiring a leading alphanumeric rejects "", ".", "..", "~/x", "%2e%2e%2fx"
# and dotfiles before anything touches the filesystem. This is a fail-fast
# filter, not the containment check — that is the resolve()/parent test below,
# which is what actually stops traversal and symlink escapes.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def sprite_file(name: str, sprite_dir: Path) -> Path | None:
    """Resolve a requested sprite name to a real file directly in sprite_dir.

    Returns None — never raises — for anything else: traversal, url-encoded
    traversal, absolute paths, subdirectories, symlinks pointing outside the
    directory, missing files, directories, and non-string input.
    """
    if not isinstance(name, str):
        return None
    if not _SAFE_NAME.fullmatch(name):
        return None
    try:
        root = Path(sprite_dir).resolve()
        # resolve() follows symlinks, so a link out of the directory lands
        # outside root and fails the parent check below.
        candidate = (root / name).resolve()
        # parent-of, not merely "under": a real subdirectory is not served.
        if candidate.parent != root:
            return None
        if not candidate.is_file():
            return None
    except (OSError, ValueError, TypeError):
        return None
    return candidate


# --- request validation ----------------------------------------------------


def _allowlisted(value: object, allowed, label: str) -> str:
    """Membership test that is safe for arbitrary JSON.

    `if value not in allowed` raises TypeError: unhashable type: 'dict' when a
    client posts {"key": {}}, because a dict/list from JSON is unhashable. The
    isinstance gate runs first so every rejection is a ValueError.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be a string")
    if value not in allowed:
        raise ValidationError(f"unknown {label}: {value!r}")
    return value


def validate_command(body: dict) -> tuple[str, dict]:
    """Validate a POSTed command. Returns (name, args) ready to enqueue."""
    if not isinstance(body, dict):
        raise ValidationError("command body must be an object")

    name = _allowlisted(body.get("name"), COMMAND_NAMES, "command")
    if name == "refresh":
        return name, {}  # takes no arguments; anything sent is dropped

    args = body.get("args")
    if not isinstance(args, dict):
        raise ValidationError("args must be an object")
    allowed = BUY_KEYS if name == "buy" else USE_KEYS
    key = _allowlisted(args.get("key"), allowed, f"{name} key")
    return name, {"key": key}


_INTEGER_RE = re.compile(r"-?[0-9]+")


def _as_int(value: object, key: str) -> int:
    """Integer from JSON, matching what config._coerce() would later accept."""
    # bool is an int subclass; True would sail through as 1.
    if isinstance(value, bool):
        raise ValidationError(f"{key} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ValidationError(f"{key} must be an integer")


def _as_float(value: object, key: str) -> float:
    """Float from JSON, matching what config._coerce() would later accept.

    NaN and the infinities are rejected here rather than clamped: they arrive
    only from a hand-written request, and silently turning one into 2.0 hides
    a client bug. bool is an int subclass, so True would otherwise pass as 1.0.
    """
    if isinstance(value, bool):
        raise ValidationError(f"{key} must be a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValidationError(f"{key} must be a number") from None
    else:
        raise ValidationError(f"{key} must be a number")
    if not math.isfinite(number):
        raise ValidationError(f"{key} must be a finite number")
    return number


def validate_config(body: dict) -> tuple[str, str]:
    """Validate a settings change. Returns (key, value) as strings.

    The value is returned as a string because the engine's config.set_value()
    coerces from one.
    """
    if not isinstance(body, dict):
        raise ValidationError("config body must be an object")

    key = _allowlisted(body.get("key"), WEB_CONFIG_KEYS, "setting")
    if "value" not in body:
        raise ValidationError(f"{key} needs a value")
    value = body["value"]

    if key in CONFIG_ENUMS:
        return key, _allowlisted(value, CONFIG_ENUMS[key], f"{key} value")

    if key in CONFIG_FLOAT_RANGES:
        low_f, high_f = CONFIG_FLOAT_RANGES[key]
        number_f = _as_float(value, key)
        if not low_f <= number_f <= high_f:
            raise ValidationError(f"{key} must be between {low_f} and {high_f}")
        return key, repr(number_f)

    low, high = CONFIG_RANGES[key]
    number = _as_int(value, key)
    if not low <= number <= high:
        raise ValidationError(f"{key} must be between {low} and {high}")
    return key, str(number)
