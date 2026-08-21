import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def _isolated_claude_env(monkeypatch):
    """Keep the developer's own environment out of the suite.

    Both variables widen where transcripts are discovered, so a developer who
    actually uses these features would otherwise see unrelated tests fail with
    inflated token counts.
    """
    for var in ("CLAUDE_CONFIG_DIR", "POKETOKENWEB_CLAUDE_PROJECT_ROOTS"):
        monkeypatch.delenv(var, raising=False)
