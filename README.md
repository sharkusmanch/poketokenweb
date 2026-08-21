<div align="center">

# PokeTokenWeb

**Your AI coding tokens, hatched into Pokémon — in your browser, on your phone.**

[![CI](https://github.com/sharkusmanch/poketokenweb/actions/workflows/ci.yml/badge.svg)](https://github.com/sharkusmanch/poketokenweb/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-3fb950)](LICENSE)

</div>

PokeTokenWeb turns the tokens you burn in **Claude Code** and **Codex** into a growing
Pokémon companion, served as a self-hosted, mobile-friendly web app. Spend tokens, hatch
an egg, evolve it through its real evolution line, graduate it into your Pokédex, and
start again. Underneath the companion it is a precise usage tracker — today's spend, cost,
and official 5-hour / weekly limits, read straight from your local logs.

<div align="center">
<img src="assets/screenshot-home.png" width="380" alt="Home screen: an Oshawott companion with its evolution line, today's token spend and cost, and week/month totals">
</div>

> Unofficial, non-commercial Pokémon fan project. See [License & disclaimer](#license--disclaimer).

## Credits — this is a derivative work

**All original design, game balance, and the idea belong to
[chattymin/PokeTokenBar](https://github.com/chattymin/PokeTokenBar)**, a macOS menu bar
app. The token economy, evolution pacing, rarity curve, hatch thresholds, shiny odds, and
shop prices are used verbatim because they are tuned values. **If you like this, star the
upstream project.**

The Python engine here is forked from
[rubensanchezrivero/poketokenbar-plasma](https://github.com/rubensanchezrivero/poketokenbar-plasma)
(an unofficial Linux/KDE port of the same app), at commit **`54d47a2`**. The git history of
that port is preserved in this repository. Everything under `poketokenbar/` is theirs;
`poketokenweb/` (the HTTP layer) and `web/` (the browser UI) are what this project adds.

## Run it yourself

```bash
curl -O https://raw.githubusercontent.com/sharkusmanch/poketokenweb/main/docker-compose.yml
docker compose up -d
```

Then open <http://localhost:8080>.

### Configuration

Everything is an environment variable; nothing is compiled in.

| Variable | Default | Purpose |
|---|---|---|
| `TZ` | `UTC` | Your local timezone. **Usage is bucketed by local date**, so set this. |
| `HOME` | `/config` | Where the app looks for `.claude/`, `.codex/` and `.claude.json`. |
| `APPRISE_URLS` | *(unset)* | Comma-separated notification URIs. Unset disables notifications. |
| `PORT` | `8080` | HTTP listen port. |
| `POKETOKENWEB_HOST` | `0.0.0.0` | Listen address. This app has **no authentication**; the compose file publishes it on loopback only. |
| `POKETOKENWEB_DATA_DIR` | `/data` | Save, settings, sprite cache, scan cache. |
| `POKETOKENWEB_WEB_ROOT` | `/app/web` | Built frontend assets. |
| `POKETOKENWEB_SPOOL_DIR` | `/tmp/poketokenbar/commands` | UI → daemon command queue. |
| `CLAUDE_CONFIG_DIR` | *(unset)* | Extra Claude project root, if you use one. |

### Notifications

Hatches, evolutions, graduations, shinies and Ditto reveals are pushed through
[Apprise](https://github.com/caronc/apprise), so they can go anywhere it supports:

```bash
APPRISE_URLS=discord://webhook_id/webhook_token
APPRISE_URLS=ntfy://ntfy.sh/your-topic
APPRISE_URLS=pover://user@token
APPRISE_URLS=apprise://your-apprise-server:8000/your-key
APPRISE_URLS=discord://id/token,ntfy://ntfy.sh/topic          # several at once
```

A malformed URI is reported at startup rather than silently never firing.

### File ownership

The image runs as **UID/GID 1000**, the first-user UID on most Linux systems, so a
bind-mounted `~/.claude` is readable out of the box. If your logs are owned by a different
user, set `PUID`/`PGID` in `.env` (see `.env.example`).

## Security

**This application has no authentication of its own.** The compose file binds to
`127.0.0.1` for that reason. If you expose it, put it behind a reverse proxy with auth.

- Your log directories are mounted **read-only** and are never written to.
- The Claude OAuth token is read from `~/.claude/.credentials.json` and sent **only** to
  `api.anthropic.com` to fetch your official limits. It is optional — omit that mount and
  token counts still work; only the limits section disappears.
- Outbound network is limited to: `api.anthropic.com`, `pokeapi.co`,
  `raw.githubusercontent.com` (sprites), the Anthropic/OpenAI status pages, and whatever
  `APPRISE_URLS` points at.

## Data sources

| Path | Read for |
|---|---|
| `~/.claude/projects/**/*.jsonl` | Claude Code usage |
| `~/.codex/sessions/**/*.jsonl` | Codex usage |
| `~/.claude/.credentials.json` | OAuth token for official limits (optional) |
| `~/.claude.json` | Which account those limits belong to (optional) |
| [PokéAPI](https://pokeapi.co/) + [PokeAPI/sprites](https://github.com/PokeAPI/sprites) | Species, evolution chains, sprites — fetched at runtime, cached, never bundled |

## What's missing compared to the macOS app

- **Eight of the ten usage providers.** Only Claude Code and Codex are supported; the
  Linux port never carried Gemini CLI, Antigravity, OpenCode, Hermes, Cursor, Grok,
  Copilot or Kiro.
- The menu-bar presence and the floating desktop pet — this is a browser tab.
- The game balance assumes one person's coding pace. If your logs include scheduled or
  agent-driven runs, the companion will grow considerably faster than intended.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
cd web && npm ci && npm test && npm run build
```

The upstream Swift sources are the specification for game behaviour; `poketokenbar/` is
kept unmodified from the Linux port so its fixes can be merged. CI prints a drift diff
against that upstream on every run.

## License & disclaimer

MIT — see [LICENSE](LICENSE). The MIT licence covers this project's source code only; it
grants no rights to third-party trademarks, artwork, or data.

Pokémon is a trademark of Nintendo / Creatures Inc. / GAME FREAK Inc. This is an
**unofficial, non-commercial fan project** with no affiliation to Nintendo, Game Freak,
Creatures Inc., The Pokémon Company, Anthropic, or OpenAI.

If you are a rights holder with a concern about this project, please open an issue and I
will respond promptly.
