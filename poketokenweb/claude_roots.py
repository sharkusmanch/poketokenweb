"""Fork-local: additional Claude transcript roots.

The vendored engine discovers roots from ``$HOME`` and ``CLAUDE_CONFIG_DIR``
(a *config* dir, to which ``projects`` is appended -- the name and meaning are
Claude Code's, and honouring it is interop, not this project's convention).

This module owns the fork's own knob, which is deliberately
``POKETOKENWEB_``-prefixed like every other setting this project invents
(see :mod:`poketokenweb.paths`). Squatting ``CLAUDE_*`` would risk colliding
with a variable Anthropic ships later, in the very environment Claude Code
itself runs in.

Entries are directories that are scanned recursively for ``*.jsonl``. Passing a
config dir rather than its ``projects`` subdirectory still works -- the scan
recurses -- so the distinction is not worth warning about. What IS worth
reporting is a root that does not exist, which otherwise reads exactly like
"feature not configured": see :func:`missing_roots`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

PROJECT_ROOTS_ENV = "POKETOKENWEB_CLAUDE_PROJECT_ROOTS"


def configured_roots(env: Mapping[str, str] | None = None) -> list[Path]:
    """Roots named by the env var, in order, ``os.pathsep``-separated.

    Blank entries are skipped: a trailing separator would otherwise become
    ``Path(".")`` and silently pull the working directory into every scan.

    Entries are stripped, ``~``-expanded and resolved. Neither Compose nor a
    Kubernetes ``env:`` block expands ``~``, and ``~/sync/projects`` is the
    obvious thing to write -- unexpanded it is a silent no-op. Resolving keeps
    scan-cache keys stable, since those are the ``str()`` of this path.
    """
    env = os.environ if env is None else env
    roots: list[Path] = []
    for raw in env.get(PROJECT_ROOTS_ENV, "").split(os.pathsep):
        entry = raw.strip()
        if entry:
            roots.append(Path(entry).expanduser().resolve())
    return roots


def missing_roots(roots: Iterable[Path]) -> list[Path]:
    """Configured roots that are not existing directories.

    The engine drops these silently, which looks identical to the var being
    unset -- the same trap this project already calls out for notification
    URIs. The caller logs these once at startup.
    """
    return [root for root in roots if not root.is_dir()]
