"""Portable push notifications via the Apprise *library*.

Portability is the point: the exact same code delivers to Discord, ntfy,
Pushover, an Apprise API server, a generic JSON webhook, or anything else
Apprise speaks — the only thing that changes is the configured URI. Nothing
in this module knows a provider's HTTP shape; Apprise owns that. Adding a
provider is a config edit, never a code edit, so **never** add a
``if scheme == "discord"``-style branch here.

Configuration is a single environment variable, ``APPRISE_URLS``, holding one
or more URIs separated by commas and/or whitespace::

    APPRISE_URLS="ntfy://ntfy.example.com/poketoken, discord://id/token"

Validation is fail-fast and offline: ``apprise.Apprise().add(uri)`` resolves
the URI against the plugin registry and returns False for anything it cannot
dispatch, performing **no** network I/O. So a typo is detectable at startup.

CALLERS MUST SURFACE ``Notifier.invalid``.
    ``invalid`` lists every URI Apprise rejected. If you compute it and never
    log it, a typo'd URI is indistinguishable from "notifications not
    configured" — silence looks identical in both cases and the user never
    learns their hatch alerts are going nowhere. At startup, log/print every
    entry of ``.invalid`` loudly, and log whether ``.enabled`` is True.

``send()`` never raises. It is driven from a polling loop; a DNS blip or a
502 from a webhook must degrade to "no notification", never kill the loop.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Callable

import apprise

ENV_VAR = "APPRISE_URLS"

_SEPARATORS = re.compile(r"[,\s]+")

#: Celebration kind -> Apprise notification type. Unknown kinds fall back to
#: DEFAULT_NOTIFY_TYPE, so a new celebration kind upstream degrades to an INFO
#: push instead of blowing up the poll loop.
KIND_NOTIFY_TYPES: dict[str, Any] = {
    "hatched": apprise.NotifyType.SUCCESS,
    "evolved": apprise.NotifyType.SUCCESS,
    "graduated": apprise.NotifyType.SUCCESS,
    "shiny": apprise.NotifyType.SUCCESS,
    "ditto": apprise.NotifyType.INFO,
}
DEFAULT_NOTIFY_TYPE = apprise.NotifyType.INFO


def notify_type_for(kind: str | None) -> Any:
    """Map a celebration kind to an Apprise notify type (INFO if unknown)."""
    if not isinstance(kind, str):
        return DEFAULT_NOTIFY_TYPE
    return KIND_NOTIFY_TYPES.get(kind, DEFAULT_NOTIFY_TYPE)


def parse_urls(raw: str | None) -> list[str]:
    """Split a raw config string on commas and/or whitespace, dropping empties."""
    if not raw:
        return []
    return [part for part in _SEPARATORS.split(raw.strip()) if part]


class Notifier:
    """Fan-out push sender over any set of Apprise URIs.

    ``factory`` exists so tests can inject a fake Apprise; production uses the
    real one.
    """

    def __init__(
        self,
        urls: list[str] | None = None,
        factory: Callable[[], Any] = apprise.Apprise,
    ) -> None:
        self.urls: list[str] = list(urls or [])
        self.accepted: list[str] = []
        self.invalid: list[str] = []
        self._apprise = factory()
        for url in self.urls:
            if self._add(url):
                self.accepted.append(url)
            else:
                self.invalid.append(url)

    def _add(self, url: str) -> bool:
        # add() is offline validation, but a malformed URI should never be
        # able to take down startup — treat any blow-up as "rejected".
        try:
            return bool(self._apprise.add(url))
        except Exception:
            return False

    @property
    def enabled(self) -> bool:
        """True only when at least one URI was accepted by Apprise."""
        return bool(self.accepted)

    def send(
        self, title: str, body: str, kind: str, attach: str | None = None
    ) -> bool:
        """Push one celebration. Returns delivery success; NEVER raises.

        `attach` is a local file path -- the companion's sprite. Apprise routes
        it only to backends that accept attachments and silently ignores it
        elsewhere, so it is safe to pass unconditionally: every target in this
        project's docs (Pushover, Discord, ntfy, Telegram, Slack, email, and
        the Apprise API server) reports attachment_support = True.
        """
        if not self.enabled:
            return False
        try:
            return bool(
                self._apprise.notify(
                    body=body,
                    title=title,
                    notify_type=notify_type_for(kind),
                    # None is Apprise's own "no attachment" default.
                    attach=attach or None,
                )
            )
        except Exception:
            # Called from the poll loop: a delivery failure must not kill it.
            return False


def from_env(env: Mapping[str, str] | None = None) -> Notifier:
    """Build a Notifier from ``APPRISE_URLS`` (defaults to os.environ)."""
    source = os.environ if env is None else env
    return Notifier(parse_urls(source.get(ENV_VAR)))
