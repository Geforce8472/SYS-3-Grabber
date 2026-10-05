# SYS-3 Grabber

Watches OVH's public stock API for a dedicated server (SYS-3, Xeon-E 2288G, 32 GB, 2x960 GB NVMe, Erith UK)
and notifies you (ntfy / Discord / Telegram) when it appears. Optionally orders it automatically.
Python 3.8+, no dependencies.

## Setup
1. `python3 sys3grabber.py find 2288G` – find the plan code, put it in `config.json` (copy `config.example.json`).
2. `python3 sys3grabber.py check` – one-off stock check.
3. `python3 sys3grabber.py watch` – poll and notify. Run it somewhere always-on (VPS, Pi, `tmux`).

## Auto-order (optional)
Create API credentials at https://eu.api.ovh.com/createToken/ with rights `GET/POST/DELETE /order/*`
and `GET /me/*`, then:

    export OVH_APPLICATION_KEY=... OVH_APPLICATION_SECRET=... OVH_CONSUMER_KEY=...
    python3 sys3grabber.py order --dry-run      # builds + prices a cart if in stock, no purchase
    python3 sys3grabber.py watch --order        # buys the moment it's in stock

Safety: `max_price` (incl. tax) aborts the order if the cart is dearer. `--order` pays with your
default payment method and waives the withdrawal period, so it creates a binding contract.
Always run a `--dry-run` first and check the printed options/total.

## Caveats
- Written without live access to OVH's API (the build sandbox blocked it), so it is untested against the
  real endpoints. Verify with `find`, `check` and `order --dry-run` first; option/addon matching in
  `pick_options` may need `option_keywords` tweaks (e.g. `["bandwidth-1000", "vrack-bandwidth-500"]`).
- Datacenter code for Erith is `lon`; adjust `datacenters` if the API reports differently.
- OVH eco-range stock often sells out within seconds; keep `poll_seconds` modest (10–30) to be polite.
