---
summary: Classes of defect this project has shipped or nearly shipped, and the mechanism that now stops each one.
read_when: Fixing a defect or regression; touching the save format, the web/poll thread boundary, provider parsing, or anything that writes a file.
---

# Defect log

Each entry is a CLASS, not an incident. Before fixing something, check whether
it is already here — and sweep the class rather than the instance.

## Save data

- **A save-format bump is two version numbers, not one.** `save.SCHEMA_VERSION`
  went 1 → 2 while `transfer.FORMAT_VERSION` stayed at 1, so `transfer.decode`'s
  "refuse the future" guard was inert: an older build accepted a v2 export and
  wrote it back stripped of profiles, levels and release records. Individual
  values are rolled once at hatch and cannot be recovered. The test that claimed
  to cover this used a hand-written `format_version: 99` — a path the real bump
  never takes, so it stayed green.
  **Rule:** move both, and assert the two are equal.

- **Every write that protects data must be temp-then-rename.** The legacy-save
  backup — the artifact whose entire job is being the last copy of a pre-upgrade
  Pokédex — was the one direct `write_bytes` in the module. Killed mid-write
  (OOM, eviction, node reboot: all likeliest during the startup it runs in) it
  left a stub, and the `if backup.exists(): return` guard made that permanent.
  The import backup had the same shape plus a fixed filename, so a second import
  overwrote the backup of the original with a backup of the mistake.
  **Rule:** temp + `replace`, a unique temp name, and a unique destination name
  for anything that is a point-in-time copy.

- **A backup must be written from the bytes already in hand.** Deciding "this is
  legacy" from the parsed dict and then re-reading the file to copy it is a
  TOCTOU: the owning writer can persist a migrated save in between, and the
  backup captures that instead.

- **`load()` is not a pure read.** It can quarantine (rename the live save) and
  write a backup. `_quarantine` needs no file descriptor, so it succeeds in
  exactly the conditions where the read failed — a transient EMFILE on an
  unauthenticated GET could rename a healthy save away. Callers that are not the
  single owning writer pass `mutate=False`, and `CompanionStore` has a
  `read_only` mode so the property is structural rather than a convention.

- **Degrade a partially-unreadable record to "unknown", never to a default.** A
  profile whose IVs were half-readable was backfilled with zeros and rendered as
  a genuine 0-IV creature, indistinguishable from a real roll. Whole-object type
  errors already degraded honestly; per-field ones fabricated.

## The web / poll thread boundary

- **Two threads, one directory: every shared write needs a unique temp name.**
  `SpriteStore` used a fixed `<target>.tmp`. Once the detail page began
  downloading sprites from the web thread into the directory the poll thread
  also writes, the two raced the rename and the loser raised `FileNotFoundError`.

- **Anything evaluated as an argument is outside the `try` above it.** The
  companion payload builders were arguments to `state.build(...)`, i.e. outside
  the `except` that guards the companion — so a raise in one escaped `poll_once`
  and nothing was published at all. The companion is cosmetic; it must never
  cost the numbers.

- **An unauthenticated route with an unbounded parameter is an amplifier.**
  `/api/pokemon/<id>` accepted ids to 100,000. An id the upstream API does not
  have caches nothing, so every request for one replays upstream forever. Bound
  the parameter to what the app can actually use.

## Provider parsing

- **Dedup must not depend on scan order.** Codex kept whichever copy of a
  replayed turn was seen first. A fork replays its parent's turns carrying the
  *fork's* timestamps, so adding a second scan root reordered the scan and moved
  an archived parent's entire history onto the fork's day — 312,814 tokens
  across two months. Keeping the earliest timestamp is order-independent and
  picks the original.

- **An identity key needs entropy from the whole record.** Keying on two totals
  meant the first turn of every session (where cumulative == last) was
  effectively one number, and turns with no cumulative all collapsed onto one
  id. Key on the full vectors, and fall back to a file-positional id when there
  is nothing replay-stable to key on.

- **A "previous value" must be invalidated, not carried, across a gap.**
  `prior_cumulative` survived events that had no cumulative, so their growth was
  credited a second time to a later orphan turn.

- **Tokens known ≠ cost known.** A turn whose total is trustworthy but whose
  split is not cannot be priced; parking the whole amount in one bucket and
  pricing it there overstated real Codex turns ~2.4×. Carry the count and mark
  the cost unknown.

- **`$0.00` and "we could not price this" are different claims.** Rendering both
  the same made a day containing an unpriced model look cheap rather than unread.

## Game state

- **One rule for "what has this individual been", shared by every consumer.** A
  revealed Ditto was appended to the live dex view but not to the records
  `graduate` and `release` write, so it vanished from the Pokédex the moment it
  left — the one thing the collection screen promises cannot happen. It also
  produced a `final_id` absent from its own `chain_order`.

- **Identity is a property of the individual, not of the page being viewed.**
  Gender and ability were re-derived from whichever form was open, so one
  creature reported a different gender on each form of its own chain.

- **A setting loaded late is a setting applied twice.** The companion store was
  built at the default difficulty and the real value pushed in on the first
  poll, so that poll rescaled progress already banked at the config scale — and
  difficulty is deliberately not in the save, so the old value was unrecoverable.
  It compounded on every restart. Load a setting *with* the object that consumes
  it.

- **`used_since_install` is both the growth meter and the wallet basis.**
  Injected XP that touched it refunded part of its own item's price; once shop
  prices became adjustable, that turned buy-then-use into a token printer.

- **Clamp indices at both ends.** `current_id` clamped `stage_index` only at the
  top, so a negative value indexed from the back of the list and could raise —
  on a property read every render.

## Tests

- **A guard test that cannot fail is worse than no test.** Two were found: one
  asserted a clamp whose triggering condition is unreachable for every real
  threshold, the other could not tell which base a rescale used because the base
  cancels in the arithmetic. Inject the defect a new guard claims to catch and
  confirm it goes red.

- **A fixture path that silently misses is a silently disabled suite.** The Codex
  parity tests — the only assertion that this port agrees with the shipped macOS
  implementation on real bytes — skipped for months because one hardcoded
  relative path did not match the checkout layout.

- **A test that reaches the network passes for reasons unrelated to the code.**
  New detail tests silently fetched real species data and a real sprite on every
  run. `tests/conftest.py` now fails any test that leaves loopback.

- **An unseeded RNG in a test helper is a scheduled failure.** The companion
  helper in `test_companion.py` hatched with `random.Random()`, so `roll_ditto`
  fired on 1 in 128 common multi-form hatches and any test asserting chain shape
  failed about that often, with nothing in the output to explain it. Seed the
  rng, and have the helper assert the property it depends on.

- **Cover the seam, not just both sides of it.** The web-thread detail helper had
  zero coverage: route tests monkeypatched it and store tests built their own
  store, so the actual cross-thread code — the one place a request touches the
  save — never ran.
