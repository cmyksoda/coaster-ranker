import difflib
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import uvicorn

from PIL import Image, ImageOps

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

CACHE_DIR = os.environ.get("CACHE_DIR", "cache")
IMAGE_DIR = os.path.join(CACHE_DIR, "images")
SOURCE_DIR = os.path.join(CACHE_DIR, "source")
DB_PATH = os.path.join(CACHE_DIR, "index.db")
DIST_DIR = os.path.realpath(os.environ.get("DIST_DIR", "dist"))

# source | webp-lossless | webp-q95 | webp-q90 | ...
IMAGE_FORMAT = os.environ.get("IMAGE_FORMAT", "webp-q95")
WEBP_METHOD = int(os.environ.get("WEBP_METHOD", "6"))
# 0 keeps whatever resolution the source came at. 1600 is ~3x the widest
# the card ever renders, and ~9x smaller than lossless at full size.
MAX_WIDTH = int(os.environ.get("MAX_WIDTH", "1600"))

# Seconds between outbound requests to any one host. Everyone's imports share
# this, so a public instance can't machine-gun RCDB from a single IP.
MIN_INTERVAL = float(os.environ.get("MIN_INTERVAL", "0.34"))
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "20"))

# "*" keeps the old behaviour; set to your tunnel origin to lock the API down.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
RCDB = "https://rcdb.com"

for d in (CACHE_DIR, IMAGE_DIR, SOURCE_DIR):
    os.makedirs(d, exist_ok=True)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    # Credentials + wildcard origin is rejected by browsers anyway, and this API
    # has no notion of a session, so there is nothing to send credentials for.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Cache index (sqlite). WAL so warm_cache.py can write while the app reads.
# --------------------------------------------------------------------------

_db_lock = threading.Lock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA busy_timeout=10000")
_db.executescript("""
CREATE TABLE IF NOT EXISTS lookup (
    qkey    TEXT PRIMARY KEY,   -- normalised "name|park"
    ckey    TEXT,               -- image cache key, NULL if no image exists
    ctype   TEXT,
    rcdb    TEXT,
    updated INTEGER
);
CREATE TABLE IF NOT EXISTS images (
    ckey    TEXT PRIMARY KEY,
    src     TEXT,
    ext     TEXT,
    bytes   INTEGER,
    width   INTEGER,
    height  INTEGER,
    updated INTEGER
);
CREATE TABLE IF NOT EXISTS parks (
    pkey    TEXT PRIMARY KEY,
    path    TEXT,
    updated INTEGER
);
-- Images rejected on review (a sign, a mural, a rider close-up, the wrong
-- ride). Never picked again, so re-resolving finds the next-best shot.
CREATE TABLE IF NOT EXISTS rejects (
    src     TEXT PRIMARY KEY,
    reason  TEXT,
    added   INTEGER
);
""")
_db.commit()


def db_query(sql, args=()):
    with _db_lock:
        return _db.execute(sql, args).fetchall()


def db_write(sql, args=()):
    with _db_lock:
        _db.execute(sql, args)
        _db.commit()


# --------------------------------------------------------------------------
# Outbound HTTP, paced per host
# --------------------------------------------------------------------------

_pace_lock = threading.Lock()
_last_hit = {}


def _pace(host):
    """Serialise outbound requests per host with a minimum gap between them."""
    if MIN_INTERVAL <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        wait = _last_hit.get(host, 0.0) + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_hit[host] = now


def _get(url, timeout=None):
    _pace(urllib.parse.urlparse(url).netloc)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": RCDB + "/"})
    with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
        return r.read()


def _get_text(url, timeout=None):
    return _get(url, timeout).decode("utf-8", errors="replace")


def rcdb_instant_search(query, retries=3):
    boundary = "----CRBound" + str(int(time.time() * 1000))
    fields = [("q", query), ("s", "1"), ("w", "1280"), ("h", "720"), ("r", "1")]
    body = "".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
        for k, v in fields
    ) + f"--{boundary}--\r\n"
    for attempt in range(retries):
        try:
            _pace("rcdb.com")
            req = urllib.request.Request(
                RCDB + "/iqs.json", data=body.encode("utf-8"),
                headers={"User-Agent": UA, "Origin": RCDB, "Referer": RCDB + "/",
                         "Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
    return {"results": []}


def rcdb_full_results(query_squashed, retries=3):
    url = f"{RCDB}/qs.htm?qs={urllib.parse.quote(query_squashed)}"
    for attempt in range(retries):
        try:
            html = _get_text(url)
            return re.findall(
                r"href=(/\d+\.htm)>([^<]+)</a>\s*-\s*<a\s+href=/\d+\.htm>([^<]+)</a>", html)
        except Exception:
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
    return []


# --------------------------------------------------------------------------
# RCDB scraping
# --------------------------------------------------------------------------

# The card image slot is `aspect-ratio: 16/10` with `object-fit: cover`, so a
# portrait photo loses ~60% of its height to the crop - usually the coaster.
# RCDB pages carry dozens of pictures and the first one is often the portrait,
# so pick deliberately rather than taking pictures[0].
TARGET_AR = 16 / 10


def _pic_score(w, h):
    if not w or not h:
        return float("-inf")
    ar = w / h
    # Portrait is close to disqualifying: only wins if nothing else exists.
    score = 0.0 if ar >= 1.0 else -100.0
    # Symmetric in log space, so 2.56 is penalised like 1.0 is.
    score -= abs(math.log(ar / TARGET_AR)) * 12.0
    # Mild nudge toward resolution, saturating so a huge panorama can't
    # outrank a well-framed shot.
    score += min(w * h, 4_000_000) / 4_000_000 * 2.0
    return score


def extract_best_image(coaster_html, exclude=()):
    """(url, width, height) of the most landscape-friendly picture on the page.

    `exclude` holds full source URLs rejected on review, so a re-resolve picks
    the next-best shot instead of the same bad one.
    """
    m = re.search(r"id=pic_json>(\{.*?\})</script>", coaster_html, re.DOTALL)
    if not m:
        return None, 0, 0
    try:
        pics = json.loads(m.group(1)).get("pictures", [])
    except Exception:
        return None, 0, 0

    best = None
    best_score = float("-inf")
    for pic in pics:
        sizes = [s for s in pic.get("sizes", [])
                 if s.get("url") and (RCDB + s["url"]) not in exclude]
        if not sizes:
            continue
        # Every size of one picture shares an aspect ratio; judge on the largest.
        largest = max(sizes, key=lambda s: (s.get("width", 0) or 0) * (s.get("height", 0) or 0))
        score = _pic_score(largest.get("width"), largest.get("height"))
        if score > best_score:
            best_score = score
            best = largest
    if not best:
        return None, 0, 0
    return best["url"], best.get("width", 0), best.get("height", 0)


def extract_type(coaster_html):
    make_match = re.search(r"Make:\s*<a[^>]+>([^<]+)</a>", coaster_html)
    model_match = re.search(r"Model:(.*?)</p>", coaster_html)

    make = make_match.group(1).strip() if make_match else ""
    model = ""
    if model_match:
        models = re.findall(r"<a[^>]+>([^<]+)</a>", model_match.group(1))
        if models:
            model = models[0].strip()

    if make == "Bolliger & Mabillard":
        make = "B&M"
    elif make == "Rocky Mountain Construction":
        make = "RMC"

    res = f"{make} {model}".strip()
    return res if res else "Unknown"


def _norm(s):
    # Fold accents rather than dropping them: without this "Último" became
    # "ltimo" and "México" became "mxico", so anyone typing the unaccented
    # spelling missed the cache entirely.
    folded = unicodedata.normalize("NFKD", (s or "").lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", folded)


# Visitors type the park name they know; the warm cache is keyed by RCDB's
# official one. Without these, importing "Magic Kingdom" or "Islands of
# Adventure" misses the entire warmed cache and re-scrapes RCDB per coaster.
PARK_ALIASES = {
    # Walt Disney World
    "magickingdom": "waltdisneyworldmagickingdom",
    "wdwmagickingdom": "waltdisneyworldmagickingdom",
    "epcot": "waltdisneyworldepcot",
    "hollywoodstudios": "waltdisneyworlddisneyshollywoodstudios",
    "disneyshollywoodstudios": "waltdisneyworlddisneyshollywoodstudios",
    "animalkingdom": "waltdisneyworlddisneysanimalkingdom",
    "disneysanimalkingdom": "waltdisneyworlddisneysanimalkingdom",
    # Disneyland Resort
    "disneylandpark": "disneyland",
    "disneylandanaheim": "disneyland",
    "disneylandcalifornia": "disneyland",
    "californiaadventure": "disneycaliforniaadventurepark",
    "disneycaliforniaadventure": "disneycaliforniaadventurepark",
    "dca": "disneycaliforniaadventurepark",
    # Disneyland Paris
    "disneylandparis": "disneylandparisdisneylandpark",
    "parcdisneyland": "disneylandparisdisneylandpark",
    "waltdisneystudios": "disneylandpariswaltdisneystudiospark",
    "waltdisneystudiospark": "disneylandpariswaltdisneystudiospark",
    "disneyadventureworld": "disneylandpariswaltdisneystudiospark",
    # Universal
    "islandsofadventure": "universalsislandsofadventure",
    "universalislandsofadventure": "universalsislandsofadventure",
    "ioa": "universalsislandsofadventure",
    "epicuniverse": "universalepicuniverse",
    "universalsepicuniverse": "universalepicuniverse",
    # Six Flags / misc spelling
    "greatescape": "sixflagsgreatescape",
    "thegreatescape": "sixflagsgreatescape",
    # Busch Gardens / SeaWorld
    "buschgardenstampa": "buschgardenstampabay",
    "buschgardensvirginia": "buschgardenswilliamsburg",
    "sesameplace": "sesameplacephiladelphia",
}


def canonical_park(park):
    p = _norm(park)
    return PARK_ALIASES.get(p, p)


def resolve_park_id(park_name):
    pkey = canonical_park(park_name)
    if not pkey:
        return None
    rows = db_query("SELECT path FROM parks WHERE pkey=?", (pkey,))
    if rows:
        return rows[0][0]

    found = None
    j = rcdb_instant_search(park_name)
    for r in j.get("results", []):
        link = r.get("l", "")
        if re.fullmatch(r"/\d+\.htm", link) and park_name.lower() in (r.get("t", "") or "").lower():
            found = link
            break
    if not found:
        for r in j.get("results", []):
            if re.fullmatch(r"/\d+\.htm", r.get("l", "")):
                found = r["l"]
                break
    if found:
        db_write("INSERT OR REPLACE INTO parks VALUES (?,?,?)", (pkey, found, int(time.time())))
    return found


def park_coasters(park_name):
    """Every operating roller coaster at a park, via RCDB's own filtered listing."""
    pid = resolve_park_id(park_name)
    if not pid:
        return []
    park_num = re.sub(r"\D", "", pid)
    try:
        html = _get_text(f"{RCDB}/r.htm?ot=2&st=93&pk={park_num}")
        return re.findall(r'<td><a href="?(/\d+\.htm)"?>([^<]+)</a>', html)
    except Exception:
        return []


def park_matches(candidate_text, park):
    if not (park or "").strip():
        return True
    c = _norm(candidate_text)
    p = _norm(park)
    if p and p in c:
        return True
    # Every significant word must appear, not just the first. Checking only the
    # first made "Kings Dominion" and "Kings Island" match each other on
    # "kings", so the two parks shared photos for their same-named rides.
    words = [w for w in re.split(r"[^a-z0-9]+", park.lower()) if len(w) > 3]
    if not words:
        return False
    return all(_norm(w) in c for w in words)


def pick_from_instant(results, park):
    real = [r for r in results if not (r.get("l") or "").startswith("qs.htm")]
    for r in real:
        if park_matches((r.get("s") or "") + " " + (r.get("t") or ""), park):
            return r["l"]
    return None


def pick_from_full(rows, park):
    for path, name, rpark in rows:
        if park_matches(rpark, park):
            return path
    return None


def pick_from_park_page(name, park):
    rows = park_coasters(park)
    if not rows:
        return None
    names = [n for _, n in rows]
    best = difflib.get_close_matches(name, names, n=1, cutoff=0.6)
    if best:
        for path, n in rows:
            if n == best[0]:
                return path
    nn = _norm(name)
    for path, n in rows:
        if nn and (nn in _norm(n) or _norm(n) in nn):
            return path
    return None


def find_image_path(name, park):
    path = pick_from_instant(rcdb_instant_search(name).get("results", []), park)
    if path:
        return path
    path = pick_from_full(rcdb_full_results(_norm(name)), park)
    if path:
        return path
    path = pick_from_park_page(name, park)
    if path:
        return path
    if park:
        return find_image_path(name, "")
    return None


def coasterpedia_image_url(name, park):
    query = f"{name} {park}".strip()
    try:
        for q in (query, name):
            url = ("https://coasterpedia.net/w/api.php?action=query&list=search"
                   f"&srsearch={urllib.parse.quote(q)}&format=json")
            search = json.loads(_get_text(url)).get("query", {}).get("search", [])
            if not search:
                continue
            title = search[0]["title"]
            url2 = ("https://coasterpedia.net/w/api.php?action=query&prop=pageimages"
                    f"&titles={urllib.parse.quote(title)}&format=json&pithumbsize=2000")
            pages = json.loads(_get_text(url2)).get("query", {}).get("pages", {})
            for _pid, pdata in pages.items():
                if "thumbnail" in pdata:
                    return pdata["thumbnail"]["source"]
    except Exception:
        pass
    return None


def wikipedia_image_url(name, park):
    query = f"{name} roller coaster {park}".strip()
    try:
        url = ("https://en.wikipedia.org/w/api.php?action=query&list=search"
               f"&srsearch={urllib.parse.quote(query)}&utf8=&format=json")
        search = json.loads(_get_text(url)).get("query", {}).get("search", [])
        if not search:
            return None
        title = search[0]["title"]
        url2 = ("https://en.wikipedia.org/w/api.php?action=query&prop=pageimages"
                f"&titles={urllib.parse.quote(title)}&format=json&pithumbsize=2000")
        pages = json.loads(_get_text(url2)).get("query", {}).get("pages", {})
        for _pid, pdata in pages.items():
            if "thumbnail" in pdata:
                return pdata["thumbnail"]["source"]
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Image cache
# --------------------------------------------------------------------------

_MAGIC = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF8", "gif"),
]


def _sniff_ext(raw):
    for magic, ext in _MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return "bin"


def _encode(raw):
    """Return (bytes, ext) in the configured delivery format."""
    if IMAGE_FORMAT == "source":
        return raw, _sniff_ext(raw)
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        if MAX_WIDTH and img.width > MAX_WIDTH:
            h = max(1, round(img.height * MAX_WIDTH / img.width))
            img = img.resize((MAX_WIDTH, h), Image.LANCZOS)
        buf = io.BytesIO()
        if IMAGE_FORMAT == "webp-lossless":
            img.save(buf, format="WEBP", lossless=True, quality=100, method=WEBP_METHOD)
        else:
            m = re.fullmatch(r"webp-q(\d{1,3})", IMAGE_FORMAT)
            img.save(buf, format="WEBP", quality=int(m.group(1)) if m else 90,
                     method=WEBP_METHOD)
        return buf.getvalue(), "webp"
    except Exception:
        # An image we can't decode is still better served as-is than dropped.
        return raw, _sniff_ext(raw)


def _cache_key(src_url):
    return hashlib.sha1(src_url.encode("utf-8")).hexdigest()[:20]


def _cached_path(ckey, ext):
    return os.path.join(IMAGE_DIR, f"{ckey}.{ext}")


def cache_image(src_url):
    """Download + encode once. Returns the cache key, or None."""
    ckey = _cache_key(src_url)
    rows = db_query("SELECT ext FROM images WHERE ckey=?", (ckey,))
    if rows and os.path.isfile(_cached_path(ckey, rows[0][0])):
        return ckey

    raw = _get(src_url)
    if not raw:
        return None

    # Keep the untouched original so the delivery format can be changed later
    # without going back to RCDB for every single image.
    try:
        src_ext = _sniff_ext(raw)
        with open(os.path.join(SOURCE_DIR, f"{ckey}.{src_ext}"), "wb") as f:
            f.write(raw)
    except Exception:
        pass

    data, ext = _encode(raw)
    width = height = 0
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except Exception:
        pass

    path = _cached_path(ckey, ext)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    db_write("INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?)",
             (ckey, src_url, ext, len(data), width, height, int(time.time())))
    return ckey


def image_url(ckey):
    rows = db_query("SELECT ext FROM images WHERE ckey=?", (ckey,))
    if not rows:
        return None
    return f"/api/image/{ckey}.{rows[0][0]}"


# --------------------------------------------------------------------------
# Resolution, with per-coaster de-duplication
# --------------------------------------------------------------------------

_inflight_lock = threading.Lock()
_inflight = {}


def _qkey(name, park):
    return f"{_norm(name)}|{canonical_park(park)}"


def resolve_coaster(name, park, fallback_type=""):
    """(image_url, type). Hits the network only on a cache miss."""
    qkey = _qkey(name, park)

    rows = db_query("SELECT ckey, ctype FROM lookup WHERE qkey=?", (qkey,))
    if rows:
        ckey, ctype = rows[0]
        return (image_url(ckey) if ckey else None), (ctype or fallback_type or "Unknown")

    # One thread does the work; the rest wait and then read the cache.
    with _inflight_lock:
        lock = _inflight.get(qkey)
        first = lock is None
        if first:
            lock = _inflight[qkey] = threading.Lock()
    if not first:
        with lock:
            pass
        rows = db_query("SELECT ckey, ctype FROM lookup WHERE qkey=?", (qkey,))
        if rows:
            ckey, ctype = rows[0]
            return (image_url(ckey) if ckey else None), (ctype or fallback_type or "Unknown")

    with lock:
        try:
            ctype = fallback_type or "Unknown"
            src_url = None
            rcdb_path = None

            rejected = {r[0] for r in db_query("SELECT src FROM rejects")}

            q = re.sub(r"^\s*the\s+", "", name, flags=re.I)
            q = re.sub(r"\s+the\s*$", "", q, flags=re.I) or name
            rcdb_path = find_image_path(q, park)

            if rcdb_path:
                try:
                    html = _get_text(RCDB + rcdb_path)
                    extracted = extract_type(html)
                    if extracted and extracted != "Unknown":
                        ctype = extracted
                    img_path, _w, _h = extract_best_image(html, exclude=rejected)
                    if img_path:
                        src_url = RCDB + img_path
                except Exception:
                    pass

            if not src_url:
                src_url = coasterpedia_image_url(name, park)
            if not src_url:
                src_url = wikipedia_image_url(name, park)
            if src_url in rejected:
                src_url = None

            ckey = None
            if src_url:
                try:
                    ckey = cache_image(src_url)
                except Exception:
                    ckey = None

            db_write("INSERT OR REPLACE INTO lookup VALUES (?,?,?,?,?)",
                     (qkey, ckey, ctype, rcdb_path, int(time.time())))
            return (image_url(ckey) if ckey else None), ctype
        finally:
            with _inflight_lock:
                _inflight.pop(qkey, None)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

class CoasterRequest(BaseModel):
    id: int
    name: str
    park: str
    type: str = ""


# NOTE: these are plain `def`, not `async def`. FastAPI then runs them in a
# worker thread, so a slow RCDB fetch can't stall the event loop (and with it
# every other request, including static files).
@app.post("/api/fetch_coaster")
def fetch_coaster(req: CoasterRequest):
    try:
        url, ctype = resolve_coaster(req.name, req.park, req.type)
    except Exception:
        url, ctype = None, (req.type or "Unknown")
    return {"id": req.id, "name": req.name, "park": req.park, "type": ctype, "image": url}


@app.get("/api/cache/stats")
def cache_stats():
    n_img, total = db_query("SELECT COUNT(*), COALESCE(SUM(bytes),0) FROM images")[0]
    n_look, n_hit = db_query(
        "SELECT COUNT(*), COALESCE(SUM(ckey IS NOT NULL),0) FROM lookup")[0]
    n_parks = db_query("SELECT COUNT(*) FROM parks")[0][0]
    return {
        "images": n_img,
        "bytes": total,
        "megabytes": round(total / 1048576, 1),
        "coasters_known": n_look,
        "coasters_with_image": n_hit,
        "parks_known": n_parks,
        "format": IMAGE_FORMAT,
    }


_KEY_RE = re.compile(r"^[0-9a-f]{6,40}\.(webp|jpg|png|gif)$")
_MIME = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


@app.get("/api/image/{filename}")
def serve_image(filename: str):
    # Strict allow-list: the key is hex and the extension is one of four. There
    # is no way to express a path here, traversal or otherwise.
    if not _KEY_RE.match(filename):
        raise HTTPException(status_code=404)
    path = os.path.join(IMAGE_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=_MIME[filename.rsplit(".", 1)[1]],
        # Keys are derived from the source URL, so a given key's bytes never change.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _safe_dist_path(rel):
    """Resolve `rel` inside dist/, or None if it escapes (or isn't a file)."""
    if not rel or "\x00" in rel:
        return None
    candidate = os.path.realpath(os.path.join(DIST_DIR, rel))
    if candidate != DIST_DIR and not candidate.startswith(DIST_DIR + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


@app.get("/{catchall:path}")
def serve_react_app(catchall: str):
    path = _safe_dist_path(catchall)
    if path:
        return FileResponse(path)
    index = os.path.join(DIST_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return Response("dist/index.html missing - run `npm run build`", status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8192)
