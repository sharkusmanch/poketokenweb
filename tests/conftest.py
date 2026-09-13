import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def _isolated_claude_env(monkeypatch):
    """Keep the developer's own environment out of the suite.

    Each of these widens where transcripts are discovered, so a developer who
    actually uses these features would otherwise see unrelated tests fail with
    inflated token counts.
    """
    for var in (
        "CLAUDE_CONFIG_DIR",
        "POKETOKENWEB_CLAUDE_PROJECT_ROOTS",
        "POKETOKENWEB_CODEX_SESSION_ROOTS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _no_outbound_http(monkeypatch, request):
    """Fail any test that reaches the real internet.

    Found the hard way: a new test for the detail helper silently fetched real
    species data and a real sprite from pokeapi.co on every run, because the
    machine running it happens to have egress. It asserted against whatever
    came back. A suite that quietly depends on a third-party service is both
    slow and dishonest -- it passes or fails for reasons unrelated to the code.

    ``urllib.request.urlopen`` is the engine's only outbound path (pokeapi and
    sprites both use it). Loopback is allowed through: the entrypoint tests
    start a real server on 127.0.0.1 and poll it, which is the app talking to
    itself, not to a third party.

    Opt out with ``@pytest.mark.network`` for a test that genuinely means it.
    """
    if request.node.get_closest_marker("network"):
        return

    import urllib.request
    from urllib.parse import urlsplit

    real = urllib.request.urlopen
    loopback = {"localhost", "127.0.0.1", "::1"}

    def guard(url, *args, **kwargs):
        target = getattr(url, "full_url", None) or str(url)
        if (urlsplit(target).hostname or "") in loopback:
            return real(url, *args, **kwargs)
        raise AssertionError(
            f"this test reached the network ({target!r}); stub the client "
            "or mark it @pytest.mark.network"
        )

    monkeypatch.setattr("urllib.request.urlopen", guard)
