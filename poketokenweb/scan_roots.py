"""Fork-local: additional transcript roots, one list per provider.

The vendored engine discovers roots from ``$HOME`` and, for Claude,
``CLAUDE_CONFIG_DIR`` (a *config* dir, to which ``projects`` is appended -- the
name and meaning are Claude Code's, and honouring it is interop, not this
project's convention).

This module owns the fork's own knobs, which are deliberately
``POKETOKENWEB_``-prefixed like every other setting this project invents
(see :mod:`poketokenweb.paths`). Squatting ``CLAUDE_*`` or ``CODEX_*`` would
risk colliding with a variable those vendors ship later, in the very
environment their CLIs run in.

**One variable per provider, never a shared one.** Parsers must not share
folders: a Codex rollout under a Claude root is not a Claude transcript, and a
single list would hand every directory to every parser. Upstream learned this
the same way (#177) and stores the list keyed by provider id.

Entries are directories scanned recursively. Passing a config dir rather than
its ``projects`` subdirectory still works -- the scan recurses -- so the
distinction is not worth warning about. What IS worth reporting is a root that
does not exist, which otherwise reads exactly like "feature not configured":
see :func:`missing_roots`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

CLAUDE_ROOTS_ENV = "POKETOKENWEB_CLAUDE_PROJECT_ROOTS"
CODEX_ROOTS_ENV = "POKETOKENWEB_CODEX_SESSION_ROOTS"

# The full registry, so a caller can report on every knob without repeating the
# list -- and so adding a provider is one line here rather than a grep.
ROOT_ENV_NAMES: tuple[str, ...] = (CLAUDE_ROOTS_ENV, CODEX_ROOTS_ENV)

# Kept for the module's previous name; the Claude knob is unchanged.
PROJECT_ROOTS_ENV = CLAUDE_ROOTS_ENV


def configured_roots(
    env_name: str = CLAUDE_ROOTS_ENV, env: Mapping[str, str] | None = None
) -> list[Path]:
    """Roots named by ``env_name``, in order, ``os.pathsep``-separated.

    Blank entries are skipped: a trailing separator would otherwise become
    ``Path(".")`` and silently pull the working directory into every scan.

    Entries are stripped, ``~``-expanded and resolved. Neither Compose nor a
    Kubernetes ``env:`` block expands ``~``, and ``~/sync/projects`` is the
    obvious thing to write -- unexpanded it is a silent no-op. Resolving keeps
    scan-cache keys stable, since those are the ``str()`` of this path.
    """
    env = os.environ if env is None else env
    roots: list[Path] = []
    for raw in env.get(env_name, "").split(os.pathsep):
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
