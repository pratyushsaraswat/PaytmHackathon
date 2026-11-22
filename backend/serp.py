"""Shared SerpApi data layer (used by BOTH the Checker and the Copilot).

- Live mode  : set SERPAPI_KEY  -> real Google Search / News / Maps / Reviews / Trends / Extract
- Mock mode  : no key (or MOCK=1) -> realistic sample data so the demo always works
- Every live call is cached on disk for 24h so repeated demos don't burn credits.
"""
import hashlib
import json
import logging
import os
import random
import shlex
import threading
import time

import requests

log = logging.getLogger("serp")

BASE = "https://serpapi.com/search.json"
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".cache.json")
CACHE_TTL = 24 * 3600

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


class Serp:
    def __init__(self):
        self.key = os.getenv("SERPAPI_KEY", "").strip()
        self.mock = (not self.key) or os.getenv("MOCK", "") == "1"
        self.calls = 0
        self._lock = threading.Lock()
        try:
            with open(CACHE_FILE) as f:
                self.cache = json.load(f)
        except Exception:
            self.cache = {}
        log.info("serp mode=%s cache_entries=%d", "mock" if self.mock else "live", len(self.cache))

    # ------------------------------------------------------------------ core
    def _get(self, **params):
        engine = params.get("engine", "?")
        q = _query_label(params)
        ck = hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()
        hit = self.cache.get(ck)
        if hit and time.time() - hit["t"] < CACHE_TTL:
            n = _result_n(engine, hit["d"])
            prev = _response_preview(engine, hit["d"])
            log.info("serp data_source=cache engine=%s n=%d q=%r preview=%s", engine, n, q, prev)
            return hit["d"]
        params["api_key"] = self.key
        log.info("serp data_source=http engine=%s q=%r", engine, q)
        log.info("serp curl: %s", _curl_line(params))
        r = requests.get(BASE, params=params, timeout=45)
        r.raise_for_status()
        data = r.json()
        n = _result_n(engine, data)
        prev = _response_preview(engine, data)
        log.info("serp response engine=%s n=%d preview=%s", engine, n, prev)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("serp response_body=%s", json.dumps(data, default=str)[:8000])
        with self._lock:
            self.calls += 1
            self.cache[ck] = {"t": time.time(), "d": data}
            try:
                with open(CACHE_FILE, "w") as f:
                    json.dump(self.cache, f)
            except Exception:
                pass
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


def _mock_search(q):
    ql = q.lower()
    name = ql.replace("scam", "").replace("fraud", "").replace("complaint", "").replace("reviews", "").strip()
    if _is_scammy(ql):
        return [
            {"title": f"{name} - Is it a scam? 38 complaints | ConsumerComplaints", "snippet": "Many users lost money after paying a 'processing fee'. Beware, never delivered the prize.",
             "link": "https://www.consumercomplaints.in/x", "source": "consumercomplaints.in"},
            {"title": "r/IndiaScams: got duped by this UPI ID, lost money", "snippet": "Fake lottery winner message asking for advance payment. Total fraud.",
             "link": "https://www.reddit.com/r/IndiaScams/x", "source": "reddit.com"},
            {"title": "Cyber cell warns about fake reward and KYC messages", "snippet": "Police advisory: phishing links asking for OTP and UPI PIN.",
             "link": "https://timesofindia.indiatimes.com/x", "source": "timesofindia.indiatimes.com"},
            {"title": "Quora: Is this offer genuine? Looks like a fraud", "snippet": "Victims report blocked after payment. Complaint filed on cybercrime.gov.in",
             "link": "https://www.quora.com/x", "source": "quora.com"},
            {"title": "ScamAdviser: low trust score", "snippet": "This website has a very low trust score and may be a scam.",
             "link": "https://www.scamadviser.com/x", "source": "scamadviser.com"},
            {"title": "MouthShut reviews: avoid, fake offers", "snippet": "Not genuine. Duped customers of Rs 2000 each.",
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
    if _is_scammy(ql):
        return [{"title": "Police arrest gang running fake lottery and KYC scam via UPI", "link": "https://www.ndtv.com/x",
                 "source": "NDTV", "date": "2 days ago"},
                {"title": "Beware: fraud messages claiming reward claim are doing rounds", "link": "https://www.hindustantimes.com/x",
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
