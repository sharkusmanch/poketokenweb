"""Tests for poketokenweb.notify.

Two layers:
  * a *faithful* fake Apprise (scheme-registry lookup, same as the real
    dispatcher does) so send() never touches the network, plus a test that
    the fake and the real library agree on a URI table — a fake tuned to the
    exact URLs under test would prove nothing;
  * a handful of tests against the REAL apprise.Apprise for URI
    acceptance/rejection only. That path is pure parsing (no network I/O).
"""

import apprise
import pytest

from poketokenweb import notify


# --- faithful fake -------------------------------------------------------
# The real Apprise.add() resolves the URI scheme against its plugin registry
# and rejects anything it cannot dispatch. The fake models exactly that rule
# (scheme lookup), not the specific strings any test happens to pass.
KNOWN_SCHEMES = {
    "apprise",
    "apprises",
    "discord",
    "ntfy",
    "ntfys",
    "json",
    "jsons",
    "mailto",
    "pover",
    "slack",
    "tgram",
}


class FakeApprise:
    """Stand-in for apprise.Apprise with the same add()/notify() contract."""

    instances = []

    def __init__(self):
        self.servers = []
        self.calls = []
        self.result = True
        self.raises = None
        FakeApprise.instances.append(self)

    def add(self, uri):
        if not isinstance(uri, str) or "://" not in uri:
            return False
        scheme, _, rest = uri.partition("://")
        if not scheme.isalnum() or scheme.lower() not in KNOWN_SCHEMES:
            return False
        if not rest:
            return False
        self.servers.append(uri)
        return True

    def notify(self, body, title="", notify_type=apprise.NotifyType.INFO):
        self.calls.append({"body": body, "title": title, "notify_type": notify_type})
        if self.raises is not None:
            raise self.raises
        return self.result


@pytest.fixture(autouse=True)
def _reset_fakes():
    FakeApprise.instances = []
    yield
    FakeApprise.instances = []


def make(urls):
    return notify.Notifier(urls, factory=FakeApprise)


# --- parse_urls ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("", []),
        ("   ", []),
        (",, ,\n", []),
        ("ntfy://host/topic", ["ntfy://host/topic"]),
        ("a://x,b://y", ["a://x", "b://y"]),
        ("a://x, b://y", ["a://x", "b://y"]),
        ("a://x  b://y", ["a://x", "b://y"]),
        ("a://x,,b://y", ["a://x", "b://y"]),
        (" a://x \n b://y \t c://z ", ["a://x", "b://y", "c://z"]),
        ("a://x,\nb://y", ["a://x", "b://y"]),
    ],
)
def test_parse_urls(raw, expected):
    assert notify.parse_urls(raw) == expected


# --- validation / enabled / invalid --------------------------------------


def test_valid_urls_enable_notifier():
    n = make(["ntfy://host/topic", "discord://id/token"])
    assert n.enabled is True
    assert n.invalid == []


def test_invalid_uri_is_reported_and_does_not_disable_the_rest():
    n = make(["ntfy://host/topic", "ntfyy://host/topic"])
    assert n.invalid == ["ntfyy://host/topic"]
    assert n.enabled is True


def test_all_invalid_urls_disable_notifier():
    n = make(["typo://host/topic", "not a url"])
    assert n.invalid == ["typo://host/topic", "not a url"]
    assert n.enabled is False


def test_no_urls_is_disabled_and_has_no_invalid():
    n = make([])
    assert n.enabled is False
    assert n.invalid == []
    assert notify.Notifier(None, factory=FakeApprise).enabled is False


def test_add_raising_is_treated_as_invalid_not_fatal():
    class Exploding(FakeApprise):
        def add(self, uri):
            raise RuntimeError("boom")

    n = notify.Notifier(["ntfy://host/topic"], factory=Exploding)
    assert n.enabled is False
    assert n.invalid == ["ntfy://host/topic"]


# --- send ----------------------------------------------------------------


def test_send_passes_title_and_body_through():
    n = make(["ntfy://host/topic"])
    assert n.send("It hatched!", "Pikachu came out of the egg.", "hatched") is True
    call = FakeApprise.instances[0].calls[0]
    assert call["title"] == "It hatched!"
    assert call["body"] == "Pikachu came out of the egg."


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("hatched", apprise.NotifyType.SUCCESS),
        ("evolved", apprise.NotifyType.SUCCESS),
        ("graduated", apprise.NotifyType.SUCCESS),
        ("shiny", apprise.NotifyType.SUCCESS),
        ("ditto", apprise.NotifyType.INFO),
        ("who-knows", apprise.NotifyType.INFO),
        ("", apprise.NotifyType.INFO),
        (None, apprise.NotifyType.INFO),
    ],
)
def test_kind_maps_to_notify_type(kind, expected):
    n = make(["ntfy://host/topic"])
    n.send("t", "b", kind)
    assert FakeApprise.instances[0].calls[0]["notify_type"] is expected


def test_send_returns_false_when_delivery_fails():
    n = make(["ntfy://host/topic"])
    FakeApprise.instances[0].result = False
    assert n.send("t", "b", "hatched") is False


def test_send_never_raises_when_apprise_explodes():
    n = make(["ntfy://host/topic"])
    FakeApprise.instances[0].raises = RuntimeError("network on fire")
    assert n.send("t", "b", "hatched") is False


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), OSError("dns"), ValueError("bad"), KeyError("k")],
)
def test_send_never_raises_for_any_exception_type(exc):
    n = make(["ntfy://host/topic"])
    FakeApprise.instances[0].raises = exc
    assert n.send("t", "b", "hatched") is False


def test_send_on_disabled_notifier_is_a_noop():
    n = make(["typo://host"])
    assert n.send("t", "b", "hatched") is False
    assert FakeApprise.instances[0].calls == []


# --- from_env ------------------------------------------------------------


def test_from_env_reads_apprise_urls():
    n = notify.from_env({"APPRISE_URLS": "ntfy://host/topic, discord://id/token"})
    assert [s for s in n.urls] == ["ntfy://host/topic", "discord://id/token"]
    assert n.enabled is True


def test_from_env_unset_is_disabled():
    n = notify.from_env({})
    assert n.enabled is False
    assert n.invalid == []


def test_from_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("APPRISE_URLS", "ntfy://host/topic")
    assert notify.from_env().enabled is True
    monkeypatch.delenv("APPRISE_URLS")
    assert notify.from_env().enabled is False


# --- the REAL library: URI acceptance/rejection only (pure parsing) ------

REAL_URIS = [
    ("apprise://host:8000/key", True),
    ("discord://id/token", True),
    ("ntfy://host/topic", True),
    ("json://host/hook", True),
    ("bogus://nope", False),
    ("ntfyy://host/topic", False),
    ("typo://host/topic", False),
    ("not a url", False),
    ("", False),
]


@pytest.mark.parametrize("uri,accepted", REAL_URIS)
def test_real_apprise_accepts_and_rejects(uri, accepted):
    n = notify.Notifier([uri])  # default factory == apprise.Apprise
    assert n.enabled is accepted
    assert n.invalid == ([] if accepted else [uri])


def test_real_apprise_multi_provider_portability():
    """Same code path, four different providers — no per-provider branch."""
    urls = ["apprise://host:8000/key", "discord://id/token", "ntfy://host/topic", "json://host/hook"]
    n = notify.Notifier(urls)
    assert n.enabled is True
    assert n.invalid == []


def test_real_apprise_salvages_a_hyphen_prefixed_scheme():
    """Documented quirk of apprise 1.12.0, found while writing these tests.

    Apprise.add() first runs its own apprise.utils.parse.parse_urls(), which
    treats "-" as a delimiter, so "typo-ntfy://host/topic" is silently split
    down to "ntfy://host/topic" and ACCEPTED. .invalid therefore catches most
    typos but not a hyphen-prefixed one; callers must not treat an empty
    .invalid as proof that every URI was spelled as intended.
    """
    n = notify.Notifier(["typo-ntfy://host/topic"])
    assert n.enabled is True
    assert n.invalid == []


@pytest.mark.parametrize("uri,accepted", REAL_URIS)
def test_fake_agrees_with_real_apprise(uri, accepted):
    """The fake must not be hand-tuned: its verdict matches the real library."""
    assert FakeApprise().add(uri) is accepted
    assert bool(apprise.Apprise().add(uri)) is accepted
