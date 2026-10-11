#!/usr/bin/env python3
"""SYS-3 Grabber: watch OVH dedicated server stock and (optionally) order it.

Standard library only. See README.md for setup.

Commands:
  find    search the OVH catalog for plan codes (e.g. `find 2288G`)
  check   one-off availability check
  watch   poll until the server is in stock, notify, optionally order
  order   place an order right now (use --dry-run to stop before checkout)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = {
    "ovh-eu": "https://eu.api.ovh.com/v1",
    "ovh-ca": "https://ca.api.ovh.com/v1",
}
# In-stock decision is an ALLOWLIST, not a denylist: a datacenter counts as
# orderable only if its availability matches a known delivery-window shape
# ("1H-high", "24H", "72H", "480H", ... optionally a "-high"/"-low" tier, or a
# bare "high"/"low"). Any other value -- "unavailable", "comingSoon", "unknown",
# null, or a status OVH adds in future -- is treated as NOT available, so an
# unexpected status can never trigger an auto-order. Override with the config
# key "available_values" (an explicit list of exact, case-insensitive values).
IN_STOCK_RE = re.compile(r"^\d+h(-(?:high|low))?$|^(?:high|low)$")


def is_available(availability, cfg=None):
    s = (availability or "").strip().lower()
    if not s:
        return False
    allow = (cfg or {}).get("available_values")
    if allow:
        return s in {str(v).strip().lower() for v in allow}
    return bool(IN_STOCK_RE.match(s))


# ---------------------------------------------------------------- config
def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    cfg.setdefault("endpoint", "ovh-eu")
    cfg.setdefault("subsidiary", "GB")
    cfg.setdefault("server", None)      # hardware ref, e.g. "24sys03" (see below)
    cfg.setdefault("plan_code", None)   # optional filter / order code, e.g. "24sys032-v1"
    cfg.setdefault("datacenters", ["lon"])  # Erith, UK is reported as "lon"
    cfg.setdefault("memory_contains", "32g")
    cfg.setdefault("storage_contains", "960")
    cfg.setdefault("poll_seconds", 30)
    cfg.setdefault("os", "none_64.en")
    cfg.setdefault("region", "europe")
    cfg.setdefault("duration", "P1M")
    cfg.setdefault("option_keywords", [])
    cfg.setdefault("notify", {})
    cfg.setdefault("notify_repeat_seconds", 600)  # re-alert while in stock; 0 = once
    cfg.setdefault("max_price", None)
    cfg.setdefault("order_lock", ".sys3grabber.ordered")
    if not cfg.get("plan_code") and not cfg.get("server"):
        sys.exit("config: set 'server' (e.g. 24sys03) or 'plan_code' (use the `find` "
                 "command). 'server' sees every storage config incl. hardware-RAID SSD; "
                 "'plan_code' alone only sees that one plan's configs.")
    return cfg


# ------------------------------------------------------------------ http
class Client:
    def __init__(self, endpoint, app_key=None, app_secret=None, consumer_key=None):
        self.base = API[endpoint]
        self.ak, self.asec, self.ck = app_key, app_secret, consumer_key
        self._delta = None

    def _time_delta(self):
        if self._delta is None:
            self._delta = int(self.request("GET", "/auth/time", auth=False)) - int(time.time())
        return self._delta

    def request(self, method, path, params=None, body=None, auth=True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body) if body is not None else ""
        headers = {"Content-Type": "application/json"}
        if auth:
            if not (self.ak and self.asec and self.ck):
                raise RuntimeError("OVH credentials missing (see README)")
            ts = str(int(time.time()) + self._time_delta())
            sig = hashlib.sha1("+".join(
                [self.asec, self.ck, method, url, data, ts]).encode()).hexdigest()
            headers.update({"X-Ovh-Application": self.ak, "X-Ovh-Consumer": self.ck,
                            "X-Ovh-Timestamp": ts, "X-Ovh-Signature": "$1$" + sig})
        req = urllib.request.Request(url, data=data.encode() if data else None,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode()[:500]}")
        return json.loads(raw) if raw else None


def client_from_env(cfg):
    return Client(cfg["endpoint"], os.environ.get("OVH_APPLICATION_KEY"),
                  os.environ.get("OVH_APPLICATION_SECRET"), os.environ.get("OVH_CONSUMER_KEY"))


# ---------------------------------------------------------- availability
def find_available(rows, cfg):
    """Return [(fqn, datacenter, availability)] matching the config that are in stock."""
    hits = []
    for row in rows:
        fqn = row.get("fqn", "")
        # In server mode the feed carries several plan codes (e.g. 24sys03-v1 and
        # the SSD-bearing 24sys032-v1); if plan_code is set, narrow to it.
        if cfg.get("plan_code") and row.get("planCode") and row["planCode"] != cfg["plan_code"]:
            continue
        if cfg["memory_contains"].lower() not in (row.get("memory") or fqn).lower():
            continue
        if cfg["storage_contains"].lower() not in (row.get("storage") or fqn).lower():
            continue
        for dc in row.get("datacenters", []):
            if dc["datacenter"] in cfg["datacenters"] and is_available(dc.get("availability"), cfg):
                hits.append((fqn, dc["datacenter"], dc["availability"], row))
    return hits


def fetch_availability(api, cfg):
    # Query by hardware ref ("server") when given: it returns every storage
    # config for the box, including hardware-RAID SSD, which the planCode-only
    # query omits. Fall back to planCode for the narrow/legacy behaviour.
    params = {"server": cfg["server"]} if cfg.get("server") else {"planCode": cfg["plan_code"]}
    return api.request("GET", "/dedicated/server/datacenter/availabilities",
                       params=params, auth=False)


# --------------------------------------------------------------- notify
def _post(url, payload=None, raw=None, headers=None, method="POST"):
    data = raw if raw is not None else json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method),
                           timeout=15).read()


def _matrix_send(c, message):
    # Matrix client-server API. Token from env MATRIX_TOKEN (preferred) or config.
    token = os.environ.get("MATRIX_TOKEN") or c.get("token")
    if not token:
        raise RuntimeError("no token (set MATRIX_TOKEN env or notify.matrix.token)")
    hs = c["homeserver"].rstrip("/")
    room = urllib.parse.quote(c["room_id"])
    txn = str(int(time.time() * 1000))
    _post(f"{hs}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}",
          {"msgtype": "m.text", "body": message},
          headers={"Authorization": f"Bearer {token}"}, method="PUT")


def notify(cfg, message):
    n = cfg["notify"]
    print(message, flush=True)
    for name, fn in (
        ("ntfy", lambda c: _post(f"{c.get('server', 'https://ntfy.sh')}/{c['topic']}",
                                 raw=message.encode(), headers={"Title": "SYS-3 in stock"})),
        ("discord", lambda c: _post(c["webhook_url"], {"content": message})),
        ("telegram", lambda c: _post(f"https://api.telegram.org/bot{c['bot_token']}/sendMessage",
                                     {"chat_id": c["chat_id"], "text": message})),
        ("matrix", lambda c: _matrix_send(c, message)),
    ):
        if n.get(name):
            try:
                fn(n[name])
            except Exception as e:  # never let a notifier kill the watcher
                print(f"notify[{name}] failed: {e}", file=sys.stderr)


# ----------------------------------------------------------------- order
def _order_lock_path(cfg):
    return cfg.get("order_lock") or ".sys3grabber.ordered"


def check_order_lock(cfg):
    """Raise if a real order has already been attempted in a previous run/poll."""
    lock = _order_lock_path(cfg)
    if os.path.exists(lock):
        with open(lock) as f:
            prev = f.read().strip()
        raise RuntimeError(
            f"order lock {lock!r} exists -- an order was already attempted "
            f"({prev}). Check your OVH account; delete this file to allow another order.")


def pick_options(api, cfg, fqn, plan_code):
    """Work out the addon plan codes (ram, storage, bandwidth, ...) to add to the cart."""
    cat = api.request("GET", "/order/catalog/public/eco",
                      params={"ovhSubsidiary": cfg["subsidiary"]}, auth=False)
    plan = next((p for p in cat["plans"] if p["planCode"] == plan_code), None)
    if not plan:
        raise RuntimeError(f"plan {plan_code} not in {cfg['subsidiary']} catalog")
    addons = plan.get("addonFamilies", [])
    # fqn looks like <plan>.<ram>.<storage>; those two addon codes are explicit.
    wanted = fqn.split(".")[1:]
    chosen = []
    for fam in addons:
        codes = fam.get("addons", [])
        pick = next((c for c in codes if any(w and w in c for w in wanted)), None)
        for kw in cfg["option_keywords"]:
            pick = next((c for c in codes if kw in c), pick)
        if not pick and fam.get("mandatory"):
            pick = fam.get("default") or (codes[0] if codes else None)
        if pick:
            chosen.append((fam.get("name"), pick))
    return chosen


def place_order(api, cfg, fqn, datacenter, plan_code=None, dry_run=True):
    # Order the exact config that came into stock: its plan code is on the
    # matched row and may differ from any single configured plan_code (e.g. the
    # SSD config is 24sys032-v1 while the box's hardware ref is 24sys03).
    plan_code = plan_code or cfg.get("plan_code")
    if not plan_code:
        raise RuntimeError("no plan_code for this config; cannot order")
    # Refuse before building anything if we've already attempted a real order.
    if not dry_run:
        check_order_lock(cfg)
    options = pick_options(api, cfg, fqn, plan_code)
    print(f"ordering plan {plan_code} ({fqn}) @ {datacenter}")
    print("options:", ", ".join(f"{n}={c}" for n, c in options))
    cart = api.request("POST", "/order/cart", body={"ovhSubsidiary": cfg["subsidiary"]})
    cid = cart["cartId"]
    api.request("POST", f"/order/cart/{cid}/assign")
    item = api.request("POST", f"/order/cart/{cid}/eco", body={
        "planCode": plan_code, "duration": cfg["duration"],
        "pricingMode": "default", "quantity": 1})
    iid = item["itemId"]
    for label, value in (("dedicated_datacenter", datacenter),
                         ("dedicated_os", cfg["os"]), ("region", cfg["region"])):
        api.request("POST", f"/order/cart/{cid}/item/{iid}/configuration",
                    body={"label": label, "value": value})
    for _, code in options:
        api.request("POST", f"/order/cart/{cid}/eco/options", body={
            "itemId": iid, "planCode": code, "duration": cfg["duration"],
            "pricingMode": "default", "quantity": 1})
    preview = api.request("GET", f"/order/cart/{cid}/checkout")
    total = preview["prices"]["withTax"]["value"]
    print(f"cart {cid}: total incl. tax {total} {preview['prices']['withTax'].get('currencyCode', '')}")
    if cfg["max_price"] is not None and total > cfg["max_price"]:
        raise RuntimeError(f"total {total} exceeds max_price {cfg['max_price']}; not ordering")
    if dry_run:
        print("dry run: cart built and priced, NOT checked out.")
        return cid
    # Write the lock BEFORE the checkout request. If the request succeeds but the
    # response is lost (timeout/crash), the order may still have gone through, so
    # the next attempt must find the lock and refuse rather than buy a second time.
    lock = _order_lock_path(cfg)
    with open(lock, "w") as f:
        json.dump({"status": "attempting", "cartId": cid, "fqn": fqn,
                   "datacenter": datacenter, "ts": int(time.time())}, f)
    result = api.request("POST", f"/order/cart/{cid}/checkout", body={
        "autoPayWithPreferredPaymentMethod": True, "waiveRetractationPeriod": True})
    with open(lock, "w") as f:
        json.dump({"status": "placed", "cartId": cid, "fqn": fqn,
                   "ts": int(time.time()), "result": result}, f)
    print("ORDER PLACED:", json.dumps(result))
    return result


# -------------------------------------------------------------- commands
def cmd_find(args):
    api = Client("ovh-eu")
    cat = api.request("GET", "/order/catalog/public/eco",
                      params={"ovhSubsidiary": args.subsidiary}, auth=False)
    q = args.query.lower()
    for p in cat["plans"]:
        text = " ".join(str(p.get(k, "")) for k in ("planCode", "invoiceName", "product")).lower()
        if q in text:
            print(f"{p['planCode']:20} {p.get('invoiceName', '')}")


def cmd_check(args):
    cfg = load_config(args.config)
    hits = find_available(fetch_availability(Client(cfg["endpoint"]), cfg), cfg)
    for fqn, dc, av, _ in hits:
        print(f"IN STOCK: {fqn} @ {dc} ({av})")
    if not hits:
        print("not available")
    return 0 if hits else 1


def cmd_watch(args):
    cfg = load_config(args.config)
    api = client_from_env(cfg)
    if args.order:
        check_order_lock(cfg)  # fail fast rather than poll then refuse
    target = cfg.get("server") or cfg.get("plan_code")
    print(f"watching {target} in {cfg['datacenters']} every {cfg['poll_seconds']}s "
          f"(auto-order: {'ON' if args.order else 'off'})", flush=True)
    last_notify = 0
    while True:
        try:
            hits = find_available(fetch_availability(api, cfg), cfg)
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr)
            hits = []
        if hits:
            fqn, dc, av, row = hits[0]
            if args.order:
                notify(cfg, f"SYS-3 IN STOCK: {fqn} @ {dc} ({av})")
                # One checkout attempt per run, full stop. Whether it succeeds or
                # raises, we never loop back and risk ordering the server twice;
                # the lock file also blocks a re-order across restarts.
                try:
                    place_order(api, cfg, fqn, dc, plan_code=row.get("planCode"), dry_run=False)
                    notify(cfg, "Order placed - check your OVH account to pay/confirm.")
                    return 0
                except Exception as e:
                    notify(cfg, f"Order attempt stopped (NOT retrying): {e}")
                    return 1
            else:
                # Notify-only: keep watching and re-notify every
                # notify_repeat_seconds while it stays in stock (0 = once only).
                now = time.time()
                rpt = cfg["notify_repeat_seconds"]
                if last_notify == 0 or (rpt and now - last_notify >= rpt):
                    notify(cfg, f"SYS-3 IN STOCK: {fqn} @ {dc} ({av})")
                    last_notify = now
        else:
            last_notify = 0  # reset so the next restock alerts immediately
        time.sleep(cfg["poll_seconds"])


def cmd_order(args):
    cfg = load_config(args.config)
    api = client_from_env(cfg)
    hits = find_available(fetch_availability(api, cfg), cfg)
    if not hits:
        sys.exit("not in stock right now")
    fqn, dc, _, row = hits[0]
    try:
        place_order(api, cfg, fqn, dc, plan_code=row.get("planCode"), dry_run=args.dry_run)
    except RuntimeError as e:
        sys.exit(str(e))


def main():
    # -c/--config on a shared parent so it works before OR after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", default="config.json")
    ap = argparse.ArgumentParser(description=__doc__, parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", parents=[common]); f.add_argument("query")
    f.add_argument("--subsidiary", default="GB"); f.set_defaults(fn=cmd_find)
    sub.add_parser("check", parents=[common]).set_defaults(fn=cmd_check)
    w = sub.add_parser("watch", parents=[common])
    w.add_argument("--order", action="store_true", help="auto-order when in stock")
    w.set_defaults(fn=cmd_watch)
    o = sub.add_parser("order", parents=[common]); o.add_argument("--dry-run", action="store_true")
    o.set_defaults(fn=cmd_order)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
