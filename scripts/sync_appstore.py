#!/usr/bin/env python3
"""Hamon Film — iTunes (App Store) → WordPress bridge, v2.2.

Runs inside GitHub Actions (outside Iran). It reads Apple's public, documented
iTunes Search / Lookup APIs (+ public top-chart RSS when available), downloads the
icon and screenshots itself and POSTs everything over HTTPS to the Hamon Film
publisher panel (REST namespace kpp-itunes/v1). The shared Iranian host therefore
never needs outbound access to Apple. Only the Python standard library is used.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "HamonFilm-GitHub-Bridge/2.2 (+https://hamonfilm.ir)"
IMPORT_URL = os.environ.get("HAMON_IMPORT_URL", "").strip()
TOKEN = os.environ.get("HAMON_IMPORT_TOKEN", "").strip()
COUNTRY = (os.environ.get("APPLE_COUNTRY") or "us").strip().lower()
TERMS = [t.strip() for t in (os.environ.get("APPLE_TERMS") or
         "top games,action games,racing games,puzzle games,arcade games,adventure games,strategy games,"
         "sports games,simulation games,educational apps,photo editor,music player,productivity,video player,"
         "social networking,health fitness,navigation,finance").split(",") if t.strip()]
MAX_NEW = int(os.environ.get("MAX_NEW_PER_RUN") or 30)
SHOTS = max(0, min(8, int(os.environ.get("SHOTS_PER_APP") or 5)))
FREE_ONLY = (os.environ.get("FREE_ONLY") or "").lower() in ("1", "true", "yes")
MODE = (os.environ.get("SYNC_MODE") or "auto").strip().lower()  # auto | push | repo | both
REPO_MAX = max(10, min(600, int(os.environ.get("REPO_MAX") or 300)))
CATALOG_DIR = os.environ.get("CATALOG_DIR") or "catalog"
USE_CHARTS = (os.environ.get("USE_CHARTS") or "1").lower() not in ("0", "false", "no")

KEEP = ["trackId", "trackName", "trackViewUrl", "bundleId", "artistName", "description", "releaseNotes", "version",
        "fileSizeBytes", "minimumOsVersion", "primaryGenreName", "genres", "genreIds", "averageUserRating",
        "userRatingCount", "trackContentRating", "formattedPrice", "price", "releaseDate", "languageCodesISO2A",
        "artworkUrl512", "artworkUrl100", "screenshotUrls", "ipadScreenshotUrls"]


def log(*a):
    print(*a, flush=True)


def http(url, data=None, headers=None, timeout=40, tries=3, method=None):
    h = {"User-Agent": UA, "Accept": "application/json, */*"}
    h.update(headers or {})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            if e.code in (401, 403, 404, 413, 422) or i == tries - 1:
                return e.code, body
            last = e
        except Exception as e:  # network error / timeout
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError("request failed: %s (%s)" % (url.split("?")[0], last))


def apple_json(url):
    status, body = http(url, timeout=40)
    if status != 200:
        raise RuntimeError("Apple HTTP %s for %s" % (status, url.split("?")[0]))
    return json.loads(body.decode("utf-8", "replace"))


def sibling(path):
    if not re.search(r"import/?$", IMPORT_URL):
        raise SystemExit("HAMON_IMPORT_URL must be the import endpoint shown in WordPress (ends with /import).")
    return re.sub(r"import/?$", path, IMPORT_URL)


AUTH = {"Authorization": "Bearer " + TOKEN, "X-Hamon-Token": TOKEN}


def hamon_get(path):
    status, body = http(sibling(path), headers=AUTH, timeout=60)
    if status in (401, 403):
        raise SystemExit("Hamon rejected the token (HTTP %s). Create a new token in WordPress and update the HAMON_IMPORT_TOKEN secret. %s" % (status, body[:200].decode("utf-8", "replace")))
    if status == 404:
        raise SystemExit("HTTP 404: try the '?rest_route=/kpp-itunes/v1/import' address shown in WordPress as HAMON_IMPORT_URL, and make sure the panel plugin 2.62.0+ is active.")
    if status != 200:
        raise SystemExit("Hamon HTTP %s: %s" % (status, body[:300].decode("utf-8", "replace")))
    return json.loads(body.decode("utf-8", "replace"))


# ---------------------------------------------------------------- discovery

def discover_ids():
    ids = []
    seen = set()

    def add(i):
        i = str(i)
        if i.isdigit() and i not in seen:
            seen.add(i)
            ids.append(i)

    if USE_CHARTS:
        charts = [
            "https://itunes.apple.com/%s/rss/topfreeapplications/limit=100/genre=6014/json" % COUNTRY,
            "https://itunes.apple.com/%s/rss/toppaidapplications/limit=100/genre=6014/json" % COUNTRY,
            "https://itunes.apple.com/%s/rss/topfreeapplications/limit=100/json" % COUNTRY,
        ]
        for u in charts:
            try:
                j = apple_json(u)
                for e in j.get("feed", {}).get("entry", []):
                    add(e.get("id", {}).get("attributes", {}).get("im:id", ""))
            except Exception as e:
                log("chart skipped:", e)
            time.sleep(3)
        try:
            j = apple_json("https://rss.applemarketingtools.com/api/v2/%s/apps/top-free/100/apps.json" % COUNTRY)
            for e in j.get("feed", {}).get("results", []):
                add(e.get("id", ""))
        except Exception as e:
            log("marketing-tools chart skipped:", e)
    found = {}
    for t in TERMS:
        q = urllib.parse.urlencode({"term": t, "entity": "software", "country": COUNTRY, "limit": 200})
        try:
            j = apple_json("https://itunes.apple.com/search?" + q)
            for r in j.get("results", []):
                if r.get("trackId"):
                    found[str(r["trackId"])] = r
                    add(r["trackId"])
        except Exception as e:
            log("search skipped (%s): %s" % (t, e))
        time.sleep(3.5)  # Apple limits roughly 20 calls/minute
    return ids, found


def lookup(ids, cache):
    out = dict(cache)
    need = [i for i in ids if i not in out]
    for k in range(0, len(need), 100):
        chunk = need[k:k + 100]
        q = urllib.parse.urlencode({"id": ",".join(chunk), "country": COUNTRY, "entity": "software"})
        try:
            j = apple_json("https://itunes.apple.com/lookup?" + q)
            for r in j.get("results", []):
                if r.get("trackId") and r.get("kind", "software") in ("software", "mac-software") or r.get("wrapperType") == "software":
                    out[str(r["trackId"])] = r
        except Exception as e:
            log("lookup skipped:", e)
        time.sleep(3)
    return out


# ---------------------------------------------------------------- images

def fetch_image(url, cap):
    if not url or not re.match(r"^https://[a-z0-9.-]+\.mzstatic\.com/", url, re.I):
        return None
    try:
        status, body = http(url, headers={"Accept": "image/*"}, timeout=40, tries=2)
    except Exception:
        return None
    if status != 200 or len(body) < 400 or len(body) > cap:
        return None
    return base64.b64encode(body).decode("ascii")


def build_item(app, with_images):
    item = {k: app[k] for k in KEEP if k in app}
    item["screenshotUrls"] = list(item.get("screenshotUrls") or [])[:SHOTS]
    item["ipadScreenshotUrls"] = list(item.get("ipadScreenshotUrls") or [])[:1]
    if with_images:
        item["icon_b64"] = fetch_image(app.get("artworkUrl512") or app.get("artworkUrl100"), 1_500_000)
        shots = []
        for u in (app.get("screenshotUrls") or [])[:SHOTS]:
            b = fetch_image(u, 1_800_000)
            if b:
                shots.append(b)
        item["shots_b64"] = shots
        if not item["icon_b64"]:
            return None  # the panel needs an icon; try again next run
    return item


# ---------------------------------------------------------------- sending

def post_batch(items):
    body = json.dumps({"items": items, "token": TOKEN, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    h.update(AUTH)
    status, resp = http(IMPORT_URL, data=body, headers=h, timeout=150, tries=3, method="POST")
    return status, resp


# ---------------------------------------------------------------- repo mode (pull by WordPress)

def resized(url, size):
    return re.sub(r"/\d+x\d+(bb|ac|sr)?(-\d+)?\.(jpg|png|webp)$", "/%s.jpg" % size, url or "")


def save_img(url, size, path, cap):
    for u in ([resized(url, size)] if resized(url, size) != url else []) + [url]:
        b64 = fetch_image(u, cap)
        if b64:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(b64))
            return True
    return False


def write_repo(ids, apps):
    """Writes catalog/manifest.json + catalog/img/<id>/... into the checked-out repo.
    The Hamon Film host then pulls them from GitHub / jsDelivr (no inbound connection needed)."""
    img_root = os.path.join(CATALOG_DIR, "img")
    old = {}
    mp = os.path.join(CATALOG_DIR, "manifest.json")
    if os.path.exists(mp):
        try:
            with open(mp, encoding="utf-8") as fh:
                for it in json.load(fh).get("items", []):
                    old[str(it.get("trackId"))] = it
        except Exception as e:
            log("old manifest unreadable:", e)
    items, keep = [], set()
    for i in ids:
        if len(items) >= REPO_MAX:
            break
        a = apps.get(i)
        if not a or not a.get("trackViewUrl") or not a.get("trackName"):
            continue
        if FREE_ONLY and float(a.get("price") or 0) > 0:
            continue
        it = {k: a[k] for k in KEEP if k in a}
        it.pop("screenshotUrls", None)
        it.pop("ipadScreenshotUrls", None)
        it.pop("artworkUrl512", None)
        it.pop("artworkUrl100", None)
        d = os.path.join(img_root, i)
        icon_rel = "%s/img/%s/icon.jpg" % (CATALOG_DIR, i)
        have = old.get(i) or {}
        if not (os.path.exists(os.path.join(d, "icon.jpg")) and have.get("icon")):
            if not save_img(a.get("artworkUrl512") or a.get("artworkUrl100"), "256x256bb", os.path.join(d, "icon.jpg"), 900_000):
                log("  no icon, skipped:", i)
                continue
        it["icon"] = icon_rel
        shots = []
        for n, u in enumerate((a.get("screenshotUrls") or [])[:SHOTS], 1):
            p = os.path.join(d, "s%d.jpg" % n)
            if os.path.exists(p) or save_img(u, "392x696bb", p, 900_000):
                shots.append("%s/img/%s/s%d.jpg" % (CATALOG_DIR, i, n))
        it["shots"] = shots
        items.append(it)
        keep.add(i)
    # prune images of apps that left the manifest
    if os.path.isdir(img_root):
        import shutil
        for name in os.listdir(img_root):
            if name not in keep:
                shutil.rmtree(os.path.join(img_root, name), ignore_errors=True)
    os.makedirs(CATALOG_DIR, exist_ok=True)
    with open(mp, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "country": COUNTRY, "items": items},
                  fh, ensure_ascii=False, separators=(",", ":"))
    log("Repo catalog written: %d apps" % len(items))


def main():
    have_push = bool(IMPORT_URL and TOKEN)
    mode = MODE if MODE in ("push", "repo", "both") else ("both" if have_push else "repo")
    if mode in ("push", "both") and not have_push:
        if mode == "push":
            raise SystemExit("Secrets HAMON_IMPORT_URL and HAMON_IMPORT_TOKEN are required for push mode.")
        mode = "repo"
    if mode in ("repo", "both"):
        ids0, cache0 = discover_ids()
        log("Discovered ids:", len(ids0))
        write_repo(ids0, lookup(ids0, cache0))
        if mode == "repo":
            return
        try:
            push_main()
        except (SystemExit, Exception) as e:  # the host may block GitHub (timeouts too); the repo catalog is already written
            log("Push skipped (WordPress pulls from the repo instead):", e)
        return
    push_main()


def push_main():
    ping = hamon_get("ping")
    post_max = int(ping.get("post_max") or 0)
    per_req = max(1, min(int(ping.get("max_items") or 6), 6))
    budget = 2_500_000 if post_max <= 0 else max(600_000, min(int(post_max * 0.55), 3_500_000))
    log("Connected to Hamon (panel %s). Request budget %d bytes, %d items per request." % (ping.get("version"), budget, per_req))

    known = (hamon_get("known") or {}).get("items") or {}
    log("Already in WordPress:", len(known))

    ids, cache = discover_ids()
    log("Discovered ids:", len(ids))
    apps = lookup(ids, cache)

    new_ids, upd_ids = [], []
    for i in ids:
        a = apps.get(i)
        if not a or not a.get("trackViewUrl") or not a.get("trackName"):
            continue
        if FREE_ONLY and float(a.get("price") or 0) > 0:
            continue
        if i not in known:
            new_ids.append(i)
        elif a.get("version") and known.get(i) and str(known[i]) != str(a["version"]):
            upd_ids.append(i)
    log("New: %d (limit %d), version updates: %d" % (len(new_ids), MAX_NEW, len(upd_ids)))
    queue = [(i, True) for i in new_ids[:MAX_NEW]] + [(i, False) for i in upd_ids[:100]]

    totals = {"created": 0, "updated": 0, "dup": 0, "fail": 0, "same": 0}
    batch, size, failed_requests = [], 0, 0

    def flush():
        nonlocal batch, size, failed_requests
        if not batch:
            return
        items = batch
        batch, size = [], 0
        status, resp = post_batch(items)
        if status == 413 and len(items) > 1:  # too big for the host: split in two
            half = len(items) // 2
            for part in (items[:half], items[half:]):
                st, rs = post_batch(part)
                report(st, rs, part)
            return
        report(status, resp, items)

    def report(status, resp, items):
        nonlocal failed_requests
        text = resp.decode("utf-8", "replace")
        if status in (401, 403):
            raise SystemExit("Hamon rejected the token (HTTP %s): %s" % (status, text[:200]))
        if status != 200:
            failed_requests += 1
            log("  request failed HTTP %s: %s" % (status, text[:300]))
            return
        j = json.loads(text)
        for k, v in (j.get("summary") or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
        for r in j.get("results", []):
            log("  %s %s %s" % (r.get("id"), r.get("status"), r.get("msg") or ""))

    for i, with_images in queue:
        item = build_item(apps[i], with_images)
        if not item:
            log("  skipped (no icon):", i)
            continue
        approx = len(json.dumps(item, ensure_ascii=False)) + 64
        if batch and (size + approx > budget or len(batch) >= per_req):
            flush()
        batch.append(item)
        size += approx
    flush()

    log("Done:", json.dumps(totals))
    if failed_requests:
        raise SystemExit("%d request(s) failed; see the log above." % failed_requests)


if __name__ == "__main__":
    main()
