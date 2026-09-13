# Plan — port upstream PokeTokenBar v2.5.2…v2.5.4 into PokeTokenWeb (v0.4.0)

Baseline: fork at `b02dcc7` (v0.3.1). Swift upstream reviewed through `09fd600` (v2.5.4).
Python engine upstream (`rubensanchezrivero/poketokenbar-plasma`) is **dormant** — no commits
since the vendored snapshot `54d47a2`. Every item below is a hand-port from the Swift source.

## Decisions taken before starting

| Decision | Choice | Consequence |
|---|---|---|
| Vendored engine | **Edit `poketokenbar/` directly** | The engine is now a hard fork tracking the Swift app. README + CI drift step must say so. |
| Pokédex detail | **Full parity incl. level-up learnset** | One extra PokéAPI request per species, on first open of its detail page, cached to disk. Never fetched from the poll loop. |
| Difficulty | **Web Settings, 0.1–2.0**, banked progress rescaled on change | Two float keys in `config.json`; `config._coerce` and `api` validation must learn floats. |
| Release | **v0.4.0**, direct to `main` in both repos | Tag push → GHCR + SLSA; `cosign verify-attestation`; digest-pinned bump in `home-ops-public`. |

## Invariants that must survive every change

1. **`errors == []` is never proof of success.** Any new scan path must be asserted on a
   positive token total, not on the absence of errors.
2. **Save decoding stays lenient.** One damaged field must never cost the Pokédex. New fields
   default; a corrupt sub-object drops only itself.
3. **Old saves load unchanged.** No migration may run for a save that predates a field; absence
   is the "legacy" signal (`released_at == None` means graduated, exactly as upstream).
4. **Cost 0 and cost unknown must not render identically.** This is the whole point of item 3.
5. **Family price fallbacks stay.** Upstream deleted them in #289 and now reports `claude-opus-5`
   as unavailable. Opus 5 is the dominant model in this deployment's logs.
6. **No `== "claude_code"`-style literal branches** in generic paths (upstream's extension rule).

## Work items

Each is one commit. Order is dependency order.

### 1. Pricing rows + cost provenance — upstream #198, #277/#279, #289, #293
- `pricing.py`: exact rows `claude-fable-5` (10/50/12.5/1.00) and `claude-fable-5-1`
  (10/50/12.5/**0.25** — cache read is the only field that differs, which is exactly why the
  family fallback alone was wrong). Keep `fable`/`opus`/`sonnet`/`haiku`/`gpt`/`gemini` fallbacks.
- New `estimated_cost(...) -> float | None`; `None` = no exact row **and** no family match.
  `cost()` keeps its `float` contract (`None → 0.0`) so existing callers are untouched.
- `models.py`: `CostCoverage{reported, estimated, unknown}` on `DailyUsage` and the period dicts.
- `state.py`: `cost_text` gains `≈` prefix when estimated and `+` suffix when partially unknown;
  fully unknown renders the localized "unavailable", never `$0.00`.
- Trigger to verify: the Claude parser substitutes the literal model name `"unknown"` when
  `message.model` is absent — that is a reachable unknown-cost input, not a synthetic one.

### 2. Codex parser fixes — upstream #181, #279, #187
- `session_roots` adds `~/.codex/archived_sessions` alongside `~/.codex/sessions`.
- Total-only turns: when every `last_token_usage` component is 0 but `total_tokens` > 0, count it
  **only** when cumulative is absent, cumulative is itself component-empty, `last.total ==
  cumulative.total`, or cumulative grew since the previous kept event. A fork's post-replay
  zero-context turn stays at 0.
- `PARSER_VERSION` 1 → 2 so cached blobs are re-parsed.
- `POKETOKENWEB_CODEX_SESSION_ROOTS`, mirroring the existing Claude knob.
- Fix `tests/test_codex.py` fixture discovery so the real Swift fixtures actually run here
  (they are currently skipped — 6 skips in the baseline).

### 3. Released Pokémon stay in the Pokédex — upstream #242, #211
- `DexEntry.released_at`; `released_at is None` means graduated, so no migration.
- Buying an egg appends a released entry: **reached forms only** (`path_ids[:stage_index+1]`),
  shiny follows the *displayed* shiny so a disguised Ditto is not revealed early,
  `collected_finals` untouched.
- `is_raising` becomes `species_id == active.current_id` (the current form only), not
  "every non-graduated form".
- Catch log gains a `released` flag → a neutral badge, distinct from `RAISING`.

### 4. Repeat hatches grow 2× — upstream #254
- `MonState.has_growth_boost`, decided at hatch from the **base** predicate over
  `collected_finals` (fork keys are `"{base}-{final}"`, so the prefix test includes the dash).
- Threshold divides by the multiplier, floor 1. Composes multiplicatively with difficulty.
- Rare-candy XP is deliberately **not** scaled.

### 5. Difficulty multipliers — upstream #244 + #287
- `balance.clamp_difficulty` / `scaled`, range 0.1–2.0.
- Consumption-site scaling only. The constant tables are never scaled: graded egg prices derive
  from a *ratio* of `graduation_total`, so scaling the table would let the growth slider move
  shop prices as a side effect.
- All threshold reads route through one store helper so a call site that forgets the multiplier
  cannot silently fall back to default difficulty.
- Changing growth difficulty **rescales banked progress** proportionally and must never turn an
  incomplete stage into a completed one. It must not evolve, graduate or hatch on its own.
- Lifetime tokens, per-provider ledgers and the wallet are never touched by a rescale.

### 6. Egg cards stay visible, purchase gated — upstream #261
- Eggs remain listed during the egg stage with the buy button disabled and a one-line reason.
- `shop.buy` rejects an egg purchase with no active companion, so a blocked attempt cannot
  debit the wallet.

### 7. This month's day-by-day trend — upstream #270
- Built from the entries the period scan already loaded — no extra read, no new parse.
- The date axis is built **from the month range**, and totals are folded onto it. Grouping by
  entry instead would paint a partial previous month, because a session that began last month
  and ran into this one is read in full by the mtime filter.
- Empty days are explicit zeros: bar position *is* the date.
- Invariant to assert: `sum(month_daily) == periods.month.tokens`.

### 8. Absolute reset time + refresh on reconnect — upstream #263, #252
- Wall-clock suffix beside the countdown; weekday shown when the reset is not within ~6h.
- `online` and `visibilitychange` listeners trigger an immediate re-read.

### 9. Individual values + Pokédex detail page — upstream #264
- `poketokenbar/profile.py`: per-individual IVs (6 × 0–31), gender, ability, level, computed
  stats with the nature modifier applied.
- Rolled once at hatch, persisted on the companion and on each dex entry, so the same creature
  stays the same creature across restarts.
- Save gains a schema version; a one-time backup is written before the first profile migration.
- Imported profiles are clamped at the trust boundary (`transfer.py` is a foreign-input path).
- A revealed Ditto rebases its profile onto Ditto's own identity.
- `GET /api/pokemon/<species_id>` returns the stored profile plus species metadata fetched and
  cached on demand. The poll loop never fetches a learnset.

### 10. Docs, README, version
- README: providers table, engine-fork note, new settings, new env vars.
- `pyproject.toml` / `web/package.json` → 0.4.0.
- CI drift step reworded (the engine is a fork now, not a merge target).

## Verification

- `python -m pytest -q` — 838 passing baseline must grow, not shrink; the 6 Codex fixture skips
  must become passes.
- `cd web && npx tsc -b && npx vitest run && npm run build`.
- Every new conditional branch: confirm it is **reachable** before writing a guard, and confirm a
  new guard **fails when the defect is injected**. Coverage percentage is not evidence.
- Adversarial subagent review of the running code, not of this document.
