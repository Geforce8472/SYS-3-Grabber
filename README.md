# SYS-3 Grabber

Watches OVH's public stock API for a dedicated server (SYS-3, Xeon-E 2288G, 32 GB, 2x960 GB NVMe, Erith UK)
and notifies you (ntfy / Discord / Telegram) when it appears. Optionally orders it automatically.
Python 3.8+, no dependencies.

## Setup
1. `python3 sys3grabber.py find 2288G` – find the plan code, put it in `config.json` (copy `config.example.json`).
2. `python3 sys3grabber.py check` – one-off stock check.
3. `python3 sys3grabber.py watch` – poll and notify. Run it somewhere always-on (VPS, Pi, `tmux`).

### `server` vs `plan_code` (important for SSD configs)
OVH's availability API returns *more* storage configs when queried by the box's hardware ref
(`server`, e.g. `24sys03`) than by a single `plan_code` (e.g. `24sys03-v1`). In particular the
hardware-RAID SSD config (`hardraid-4x960ssd`) only shows up under the `server` query, and it lives
under a *different* plan code (`24sys032-v1`). So:

- Set `server` to watch every storage config for the box (recommended). Leave `plan_code` unset to
  match any of them, or set it to narrow to one plan (e.g. `24sys032-v1` for the SSD variants).
- Set only `plan_code` for the old, narrow behaviour (that plan's configs only).
- `memory_contains` / `storage_contains` then pick the exact config, e.g. `"32g"` + `"960ssd"` for
  32 GB with 4×960 GB SATA SSD. When an order fires, it uses the plan code of the matched row, so
  mixed-plan `server` feeds order the right product automatically.
- `memory_contains` may also be a list, most-preferred first, e.g. `["32g", "64g"]`: either size
  matches, and if both are in stock at once the earlier one is ordered.

To find a box's `server` ref, run `check` with any config and look at an `fqn` (`<plan>.<ram>.<storage>`),
or query `/dedicated/server/datacenter/availabilities` – the `server` field is the ref.

## Auto-order (optional)
Create API credentials at https://eu.api.ovh.com/createToken/ with rights `GET/POST/DELETE /order/*`
and `GET /me/*`, then:

    export OVH_APPLICATION_KEY=... OVH_APPLICATION_SECRET=... OVH_CONSUMER_KEY=...
    python3 sys3grabber.py order --dry-run      # builds + prices a cart if in stock, no purchase
    python3 sys3grabber.py watch --order        # buys the moment it's in stock

Safety: `max_price` (incl. tax) aborts the order if the cart is dearer. `--order` pays with your
default payment method and waives the withdrawal period, so it creates a binding contract.
Always run a `--dry-run` first and check the printed options/total.

Once a real checkout is attempted, the tool writes a lock file (`.sys3grabber.ordered`, path
configurable via `order_lock`) **before** sending the request and then refuses any further order
until you delete it. This is deliberate: if a checkout request times out, the order may still have
gone through, so the tool stops rather than risk buying the server twice. `watch --order` likewise
makes at most one checkout attempt per run and never loops back to re-order. If an attempt is
interrupted, check your OVH account first, then remove the lock file to allow another.

In-stock detection is an allowlist: a datacenter is treated as orderable only when its availability
matches a known delivery-window value (`1H-high`, `24H`, `72H`, ...). Any unexpected or new status
is treated as *not* available, so it can never trigger an auto-order. Pin exact values with the
`available_values` config key if OVH's reported values differ.

### Per-poll logging
Set `"log_polls": true` to print a timestamped line each poll with the raw availability
each watched datacenter reports for the target config, e.g. `2026-10-12T11:14:58Z lon=unavailable`.
This records values that *fail* the in-stock allowlist too (a brief flicker, a `comingSoon`,
or `absent` when the feed omits the DC entirely), so over time `watch.log` becomes your own
availability history for a config OVH exposes no history for. ~1 line/poll, so keep an eye on
log size for very frequent polling.

## Notifications
`notify` may hold any of `ntfy`, `discord`, `telegram`, `matrix` (all fire if present); messages also
always print to stdout. Matrix sends to a room via the client-server API:

    "notify": {"matrix": {"homeserver": "http://localhost:8008", "room_id": "!abcdef:your.server"}}

The access token comes from the `MATRIX_TOKEN` env var (preferred, keeps it out of files) or
`notify.matrix.token`. Get a token by logging a (bot) user in once:

    curl -XPOST http://localhost:8008/_matrix/client/v3/login \
      -d '{"type":"m.login.password","user":"grabber","password":"..."}'

## Caveats
- The order/cart path was validated against the live OVH API via `order --dry-run`; still run a
  `--dry-run` for your own target first, as option/addon matching in `pick_options` may need
  `option_keywords` tweaks (e.g. `["bandwidth-1000", "vrack-bandwidth-500"]`).
- Datacenter code for Erith is `lon`; adjust `datacenters` if the API reports differently.
- OVH eco-range stock often sells out within seconds; keep `poll_seconds` modest (10–30) to be polite.
