"""Shared SerpApi data layer (used by BOTH the Checker and the Copilot).

- Live mode  : set SERPAPI_KEY  -> real Google Search / News / Maps / Reviews / Trends / Extract
- Mock mode  : no key (or MOCK=1) -> realistic sample data so the demo always works
- Every live call is cached on disk for 24h so repeated demos don't burn credits.
"""
import atexit
import hashlib
import json
import logging
import os
import random
import shlex
import threading
import time

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger("serp")

BASE = "https://serpapi.com/search.json"
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".cache.json")
CACHE_TTL = 24 * 3600
TTL_BY_ENGINE = {"google_news": 6 * 3600, "extract": 3600}   # news changes fast; pages change; the rest is stable
MAX_CACHE_ENTRIES = 500
RETRY_STATUS = {429, 500, 502, 503, 504}

SCAM_HINTS = ["lottery", "kbc", "prize", "winner", "reward", "refund", "kyc", "freegift",
              "free-gift", "quickcash", "fastloan", "loan-fast", "fake", "claim", "jackpot",
              "earnmoney", "double", "cashback-claim"]


def _query_label(params):
    return (params.get("q") or params.get("url") or params.get("data_id") or "")[:100]


def _result_n(engine, data):
    if not isinstance(data, dict):
        return 0
    for key in ("organic_results", "news_results", "local_results", "reviews"):
        if data.get(key):
            return len(data[key])
    if data.get("place_results"):
        return 1
    tl = (data.get("interest_over_time") or {}).get("timeline_data") or []
    if tl:
        return len(tl)
    md = data.get("markdown") or data.get("markdown_content") or ""
    if md:
        return len(md)
    return 0


def _curl_line(params):
    """Copy-pasteable SerpApi request (api_key redacted)."""
    parts = ["curl", "-sG", shlex.quote(BASE)]
    for k, v in sorted(params.items()):
        val = "***" if k == "api_key" else str(v)
        parts.append(f"--data-urlencode {shlex.quote(f'{k}={val}')}")
    return " ".join(parts)


def _response_preview(engine, data, max_items=3):
    """Short JSON-ish preview for logs."""
    if not isinstance(data, dict):
        return []
    if engine == "google":
        return [{"title": (x.get("title") or "")[:90], "link": (x.get("link") or "")[:80]}
                for x in (data.get("organic_results") or [])[:max_items]]
    if engine == "google_news":
        return [{"title": (x.get("title") or "")[:90], "source": x.get("source")}
                for x in (data.get("news_results") or [])[:max_items]]
    if engine == "google_maps":
        raw = data.get("local_results") or ([data["place_results"]] if data.get("place_results") else [])
        return [{"title": x.get("title"), "rating": x.get("rating"), "reviews": x.get("reviews")}
                for x in raw[:max_items]]
    if engine == "google_maps_reviews":
        return [{"rating": x.get("rating"), "snippet": (x.get("snippet") or "")[:70]}
                for x in (data.get("reviews") or [])[:max_items]]
    if engine == "google_trends":
        tl = (data.get("interest_over_time") or {}).get("timeline_data") or []
        if tl:
            return [{"points": len(tl), "last": tl[-1].get("date")}]
        return []
    if engine == "extract":
        md = (data.get("markdown") or data.get("markdown_content") or "")[:200]
        return [{"markdown_chars": len(md), "head": md[:120]}] if md else []
    return [{"keys": list(data.keys())[:8]}]


def _slim(engine, d):
    """Keep ONLY the fields the app uses. Raw SerpApi responses are 50-150 KB each (AI-overview tokens, thumbnails,
    review images...). Slim copies are ~1-5 KB, so the cache file stays tiny and loads/saves fast."""
    if not isinstance(d, dict):
        return d
    out = {}
    if d.get("error"):
        out["error"] = d["error"]
    if engine == "google":
        out["organic_results"] = [{k: x.get(k, "") for k in ("title", "link", "snippet", "displayed_link")}
                                  for x in (d.get("organic_results") or [])]
    elif engine == "google_news":
        out["news_results"] = [{"title": x.get("title", ""), "link": x.get("link", ""), "date": x.get("date", ""),
                                "source": {"name": (x.get("source") or {}).get("name", "") if isinstance(x.get("source"), dict) else str(x.get("source") or "")}}
                               for x in (d.get("news_results") or [])]
    elif engine == "google_maps":
        keep = ("title", "rating", "reviews", "address", "open_state", "hours", "data_id", "type")
        if d.get("local_results"):
            out["local_results"] = [{k: x.get(k) for k in keep} for x in d["local_results"]]
        if d.get("place_results"):
            out["place_results"] = {k: d["place_results"].get(k) for k in keep}
    elif engine == "google_maps_reviews":
        out["reviews"] = [{"rating": x.get("rating"),
                           "snippet": (x.get("snippet") or (x.get("extracted_snippet") or {}).get("original") or "")[:400]}
                          for x in (d.get("reviews") or [])[:20]]
    elif engine == "google_trends":
        tl = (d.get("interest_over_time") or {}).get("timeline_data") or []
        out["interest_over_time"] = {"timeline_data": [
            {"date": p.get("date"), "values": [{"extracted_value": (p.get("values") or [{}])[0].get("extracted_value")}]} for p in tl]}
    elif engine == "extract":
        md = d.get("markdown") or d.get("markdown_content") or ""
        if md:
            out["markdown"] = md[:6000]
        else:
            out["page_information"] = {"title": (d.get("page_information") or {}).get("title", "")}
            out["headings"] = [{"text": (h.get("text", "") if isinstance(h, dict) else str(h))} for h in (d.get("headings") or [])[:20]]
    else:
        return d
    return out


class Serp:
    def __init__(self):
        self.key = os.getenv("SERPAPI_KEY", "").strip()
        self.mock = (not self.key) or os.getenv("MOCK", "") == "1"
        self.calls = 0          # real HTTP calls (= credits spent) this run
        self.hits = 0           # served from cache (credits saved)
        self._lock = threading.Lock()
        self._flight_lock = threading.Lock()
        self._inflight = {}
        self._save_timer = None
        # one pooled session: keeps TLS connections to serpapi.com alive (saves ~100-300 ms per call)
        self.http = requests.Session()
        self.http.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))
        self.cache = self._load_cache()
        atexit.register(self._flush)
        log.info("serp mode=%s cache_entries=%d", "mock" if self.mock else "live", len(self.cache))

    # ---------------------------------------------------------------- cache io
    def _load_cache(self):
        try:
            with open(CACHE_FILE) as f:
                raw = json.load(f)
        except Exception:
            return {}
        now, out = time.time(), {}
        for ck, e in raw.items():
            try:
                eng = e.get("e") or (e["d"].get("search_parameters") or {}).get("engine", "")   # old entries: engine inside raw body
                if now - e["t"] < TTL_BY_ENGINE.get(eng, CACHE_TTL):
                    out[ck] = {"t": e["t"], "e": eng, "d": _slim(eng, e["d"])}                   # shrink old fat entries too
            except Exception:
                continue
        return dict(sorted(out.items(), key=lambda kv: kv[1]["t"])[-MAX_CACHE_ENTRIES:])

    def _schedule_save(self):
        """Debounced: many calls in one request cause ONE file write, not one per call."""
        with self._lock:
            if self._save_timer is None:
                self._save_timer = threading.Timer(1.5, self._flush)
                self._save_timer.daemon = True
                self._save_timer.start()

    def _flush(self):
        with self._lock:
            self._save_timer = None
            if len(self.cache) > MAX_CACHE_ENTRIES:
                keep = sorted(self.cache.items(), key=lambda kv: kv[1]["t"])[-MAX_CACHE_ENTRIES:]
                self.cache = dict(keep)
            snap = json.dumps(self.cache)
        try:
            tmp = CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(snap)
            os.replace(tmp, CACHE_FILE)          # atomic: a crash can never leave a half-written cache
        except Exception as e:
            log.warning("cache save failed: %s", e)

    @staticmethod
    def _cache_key(params):
        p = dict(params)
        if isinstance(p.get("q"), str):
            p["q"] = " ".join(p["q"].lower().split())      # "Sweet  Shop" and "sweet shop" are the same query
        return hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------ core
    def _cached(self, ck, engine, q):
        hit = self.cache.get(ck)
        if hit and time.time() - hit["t"] < TTL_BY_ENGINE.get(engine, CACHE_TTL):
            self.hits += 1
            log.info("serp data_source=cache engine=%s n=%d q=%r preview=%s", engine, _result_n(engine, hit["d"]), q,
                     _response_preview(engine, hit["d"]))
            return hit["d"]
        return None

    def _get(self, **params):
        engine = params.get("engine", "?")
        q = _query_label(params)
        ck = self._cache_key(params)
        data = self._cached(ck, engine, q)
        if data is not None:
            return data
        # single-flight: if the same call is already running (two users, same query) wait for it instead of paying twice
        with self._flight_lock:
            lk = self._inflight.setdefault(ck, threading.Lock())
        with lk:
            data = self._cached(ck, engine, q)
            if data is not None:
                return data
            try:
                return self._fetch(ck, engine, q, params)
            finally:
                with self._flight_lock:
                    self._inflight.pop(ck, None)

    def _fetch(self, ck, engine, q, params):
        params = dict(params, api_key=self.key)
        t0 = time.perf_counter()
        log.info("serp data_source=http engine=%s q=%r", engine, q)
        log.info("serp curl: %s", _curl_line(params))
        r, last = None, None
        for attempt in (1, 2, 3):                       # retry transient failures only (network, 429, 5xx)
            try:
                r = self.http.get(BASE, params=params, timeout=(5, 30))
                if r.status_code in RETRY_STATUS and attempt < 3:
                    log.warning("serp engine=%s got HTTP %d, retry %d", engine, r.status_code, attempt)
                    time.sleep(0.4 * attempt)
                    continue
                r.raise_for_status()
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                if attempt < 3:
                    log.warning("serp engine=%s network error (%s), retry %d", engine, type(e).__name__, attempt)
                    time.sleep(0.4 * attempt)
                    continue
                log.error("serp http FAILED engine=%s q=%r after %dms err=%s", engine, q, (time.perf_counter() - t0) * 1000, e)
                raise
            except requests.RequestException as e:      # 4xx (bad key, no credits, bad params): do not retry
                body = getattr(getattr(e, "response", None), "text", "") or ""
                log.error("serp http FAILED engine=%s q=%r after %dms err=%s body=%s", engine, q, (time.perf_counter() - t0) * 1000, e, body[:400])
                raise
        raw = r.json()
        if raw.get("error"):
            msg = str(raw.get("error"))
            lvl = logging.INFO if "hasn't returned any results" in msg else logging.ERROR  # empty result is normal
            log.log(lvl, "serp api %s engine=%s msg=%s", "empty" if lvl == logging.INFO else "error", engine, msg)
        log.info("serp response engine=%s n=%d ms=%d preview=%s", engine, _result_n(engine, raw),
                 (time.perf_counter() - t0) * 1000, _response_preview(engine, raw))
        if log.isEnabledFor(logging.DEBUG):
            log.debug("serp response_body=%s", json.dumps(raw, default=str)[:8000])
        data = _slim(engine, raw)
        with self._lock:
            self.calls += 1
            self.cache[ck] = {"t": time.time(), "e": engine, "d": data}
        self._schedule_save()
        return data

    def _mock_log(self, op, q, rows):
        q = (q if isinstance(q, str) else str(q))[:100]
        if isinstance(rows, list):
            n = len(rows)
            if rows and isinstance(rows[0], dict):
                prev = [{k: (str(v)[:70] if v is not None else "") for k, v in list(r.items())[:3]}
                        for r in rows[:3]]
            elif rows and isinstance(rows[0], (int, float)):
                n, prev = len(rows), [{"trend_points": n, "last3": rows[-3:]}]
            else:
                prev = [(rows[:120] + "…") if isinstance(rows, str) and len(rows) > 120 else rows]
        elif isinstance(rows, str):
            n = len(rows)
            prev = [{"text_len": n, "head": rows[:120]}]
        else:
            n, prev = 0, []
        log.info("serp data_source=mock op=%s n=%d q=%r preview=%s", op, n, q, prev)

    # ------------------------------------------------------------ public API
    def search(self, q, num=10):
        """Google Search -> [{title, snippet, link, source}]"""
        if self.mock:
            out = _mock_search(q)
            self._mock_log("search", q, out)
            return out
        d = self._get(engine="google", q=q, gl="in", hl="en", num=num)
        return [{"title": x.get("title", ""), "snippet": x.get("snippet", ""),
                 "link": x.get("link", ""), "source": x.get("displayed_link", "")}
                for x in d.get("organic_results", [])]

    def news(self, q):
        """Google News -> [{title, link, source, date}]"""
        if self.mock:
            out = _mock_news(q)
            self._mock_log("news", q, out)
            return out
        d = self._get(engine="google_news", q=q, gl="in", hl="en")
        out = []
        for x in d.get("news_results", []):
            src = x.get("source") or {}
            out.append({"title": x.get("title", ""), "link": x.get("link", ""),
                        "source": src.get("name", "") if isinstance(src, dict) else str(src),
                        "date": x.get("date", "")})
        return out

    def maps(self, q):
        """Google Maps -> [{title, rating, reviews, address, hours, data_id, type}]"""
        if self.mock:
            out = _mock_maps(q)
            self._mock_log("maps", q, out)
            return out
        d = self._get(engine="google_maps", type="search", q=q, hl="en", gl="in")
        out = []
        for x in d.get("local_results", []) or ([d["place_results"]] if d.get("place_results") else []):
            out.append({"title": x.get("title", ""), "rating": x.get("rating"),
                        "reviews": x.get("reviews", 0) or 0, "address": x.get("address", ""),
                        "hours": x.get("open_state") or x.get("hours") or "",
                        "data_id": x.get("data_id", ""), "type": x.get("type", "")})
        return out

    def reviews(self, data_id, title=""):
        """Google Maps Reviews -> [{rating, snippet}]"""
        if self.mock:
            out = _mock_reviews(title or data_id)
            self._mock_log("reviews", title or data_id, out)
            return out
        d = self._get(engine="google_maps_reviews", data_id=data_id, hl="en", sort_by="ratingLow")
        return [{"rating": x.get("rating"), "snippet": (x.get("snippet") or x.get("extracted_snippet", {}).get("original", "") or "")}
                for x in d.get("reviews", [])]

    def trends(self, q):
        """Google Trends interest over time (India, last 3 months) -> [int]"""
        if self.mock:
            out = _mock_trends(q)
            self._mock_log("trends", q, out)
            return out
        d = self._get(engine="google_trends", q=q, geo="IN", data_type="TIMESERIES", date="today 3-m")
        vals = []
        for p in d.get("interest_over_time", {}).get("timeline_data", []):
            try:
                vals.append(int(p["values"][0]["extracted_value"]))
            except Exception:
                pass
        return vals

    def extract(self, url):
        """SerpApi Extract API -> page text as Markdown (truncated)"""
        if self.mock:
            out = _mock_extract(url)
            self._mock_log("extract", url, out)
            return out
        d = self._get(engine="extract", url=url)
        text = d.get("markdown") or d.get("markdown_content") or ""
        if not text:  # fall back to whatever readable fields exist
            info = d.get("page_information", {}) or {}
            heads = d.get("headings", []) or []
            text = (info.get("title", "") + "\n" +
                    "\n".join(h.get("text", "") if isinstance(h, dict) else str(h) for h in heads))
        return text[:6000]


# ====================================================================== MOCKS
def _seed(s):
    return int(hashlib.md5(s.lower().encode()).hexdigest(), 16)


def _is_scammy(s):
    s = s.lower()
    return any(h in s for h in SCAM_HINTS)


def _ident_of(q):
    """First token of a mock query = the ID/domain being checked (so mock evidence names it, like real results)."""
    toks = q.strip().split()
    return toks[0].strip('"') if toks else q


def _mock_search(q):
    ql = q.lower()
    ident = _ident_of(ql)
    name = ql.replace("scam", "").replace("fraud", "").replace("complaint", "").replace("reviews", "").strip()
    if _is_scammy(ql):
        return [
            {"title": f"{name} - Is it a scam? 38 complaints | ConsumerComplaints", "snippet": f"{ident}: many users lost money after paying a 'processing fee'. Beware, never delivered the prize.",
             "link": "https://www.consumercomplaints.in/x", "source": "consumercomplaints.in"},
            {"title": f"r/IndiaScams: got duped by {ident}, lost money", "snippet": "Fake lottery winner message asking for advance payment. Total fraud.",
             "link": "https://www.reddit.com/r/IndiaScams/x", "source": "reddit.com"},
            {"title": f"Cyber cell warns about fake reward and KYC messages ({ident})", "snippet": "Police advisory: phishing links asking for OTP and UPI PIN.",
             "link": "https://timesofindia.indiatimes.com/x", "source": "timesofindia.indiatimes.com"},
            {"title": f"Quora: Is {ident} genuine? Looks like a fraud", "snippet": "Victims report blocked after payment. Complaint filed on cybercrime.gov.in",
             "link": "https://www.quora.com/x", "source": "quora.com"},
            {"title": f"ScamAdviser: {ident} has a low trust score", "snippet": "This website has a very low trust score and may be a scam.",
             "link": "https://www.scamadviser.com/x", "source": "scamadviser.com"},
            {"title": f"MouthShut reviews of {ident}: avoid, fake offers", "snippet": "Not genuine. Duped customers of Rs 2000 each.",
             "link": "https://www.mouthshut.com/x", "source": "mouthshut.com"},
        ]
    if any(k in ql for k in ["sharma", "sweet", "cafe", "kirana", "verified", "paytm", "bakery", "mart"]):
        return [
            {"title": f"{name.title()} - 4.5 stars, trusted local shop", "snippet": "Genuine products, good service and recommended by many customers.",
             "link": "https://www.justdial.com/x", "source": "justdial.com"},
            {"title": f"{name.title()} reviews | Trustpilot", "snippet": "Legit seller with verified reviews and quick delivery.",
             "link": "https://www.trustpilot.com/x", "source": "trustpilot.com"},
        ]
    return []  # unknown -> no reports at all


def _mock_news(q):
    ql = q.lower()
    ident = _ident_of(ql)
    if _is_scammy(ql):
        return [{"title": f"Police arrest gang running fake lottery and KYC scam via UPI ({ident})", "link": "https://www.ndtv.com/x",
                 "source": "NDTV", "date": "2 days ago"},
                {"title": f"Beware: fraud messages from {ident} claiming reward are doing rounds", "link": "https://www.hindustantimes.com/x",
                 "source": "Hindustan Times", "date": "1 week ago"}]
    if "festival" in ql or "market" in ql or "demand" in ql:
        return [{"title": "Winter wedding and festive season boosts local retail footfall", "link": "https://economictimes.indiatimes.com/x",
                 "source": "Economic Times", "date": "3 days ago"}]
    return []


CATS = {
    "sweet": ["Sharma Sweets", "Bikaner Mithai Ghar", "Haldiram's Outlet", "Gupta Misthan", "Annapurna Sweets", "Ghasitaram Corner"],
    "cafe": ["Brew & Bean", "Chai Tapri Co.", "The Daily Grind", "Cafe Mocha Street", "Blue Tokai Corner", "Third Wave Lane"],
    "kirana": ["Gupta General Store", "Raj Kirana", "Fresh Mart", "Apna Bazaar", "Daily Needs Store", "Sai Provision"],
    "default": ["City Choice", "Super Value Store", "Metro Traders", "Royal Enterprises", "Happy Customers Hub", "Lakshmi Stores"],
}


def _cat(q):
    ql = q.lower()
    for k in CATS:
        if k in ql:
            return k
    if "mithai" in ql:
        return "sweet"
    if "coffee" in ql or "tea" in ql:
        return "cafe"
    if "grocery" in ql or "general store" in ql:
        return "kirana"
    return "default"


def _mock_maps(q):
    names = CATS[_cat(q)]
    rnd = random.Random(_seed(q))
    out = []
    for i, n in enumerate(names):
        out.append({"title": n, "rating": round(rnd.uniform(3.2, 4.7), 1), "reviews": rnd.randint(40, 900),
                    "address": "Near " + rnd.choice(["Main Market", "Station Road", "Ring Road", "Metro Gate 2", "Old Bazaar"]),
                    "hours": rnd.choice(["Open ⋅ Closes 10 PM", "Open ⋅ Closes 9 PM", "Closed ⋅ Opens 9 AM", "Open 24 hours"]),
                    "data_id": f"mock-{_seed(n) % 10**8}", "type": _cat(q)})
    return out


REVIEW_POOL = [
    (1, "Delivery was very late, waited over an hour and food came cold."),
    (2, "Packaging was poor, box leaked and spilled everywhere."),
    (2, "Quite expensive for the quantity, overpriced compared to nearby shops."),
    (1, "Staff was rude and ignored us at the counter."),
    (3, "Long wait at the counter, very slow billing and no UPI QR working."),
    (2, "Not very clean, hygiene needs improvement."),
    (3, "Taste was okay but items were a bit stale."),
    (5, "Great taste and friendly owner!"),
    (4, "Good quality, reasonable price."),
    (5, "Loved the sweets, fresh and tasty."),
    (1, "Delivery delayed again, late by 90 minutes. Never ordering."),
    (2, "Box was crushed, packaging is bad for gifting."),
]


def _mock_reviews(title):
    rnd = random.Random(_seed(title))
    pool = REVIEW_POOL[:]
    rnd.shuffle(pool)
    return [{"rating": r, "snippet": s} for r, s in pool[:8]]


def _mock_trends(q):
    rnd = random.Random(_seed(q))
    base = rnd.randint(35, 50)
    vals = [base + rnd.randint(-4, 4) for _ in range(9)]
    vals += [base + 8 + rnd.randint(0, 4), base + 15 + rnd.randint(0, 4), base + 22 + rnd.randint(0, 4), base + 28 + rnd.randint(0, 4)]
    return [max(0, min(100, v)) for v in vals]


def _mock_extract(url):
    if _is_scammy(url):
        return ("# Congratulations! You are today's WINNER!\n\nClaim your prize of Rs 25,00,000 now. "
                "Limited time offer - act now! To receive your reward, pay a small processing fee of Rs 999 "
                "and enter your OTP and UPI PIN to verify KYC. This is Paytm official lottery. "
                "Click here to verify your account immediately or it will be blocked.")
    return ("# Sharma Sweets\n\nFresh mithai made daily since 1985. Visit us at Main Market. "
            "Call 011-2345678 for bulk orders. Cash on delivery available. Email: care@sharmasweets.in")


serp = Serp()
