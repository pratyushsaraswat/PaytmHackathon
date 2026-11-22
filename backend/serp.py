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
TTL_BY_ENGINE = {"google_news": 6 * 3600, "extract": 3600, "google_finance": 900, "google_flights": 3 * 3600,
                 "google_hotels": 6 * 3600, "google_shopping": 6 * 3600, "amazon": 6 * 3600}   # news changes fast; pages change; the rest is stable
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
    for key in ("organic_results", "news_results", "local_results", "reviews", "shopping_results", "properties", "best_flights", "graph"):
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
        keep = ("title", "rating", "reviews", "address", "open_state", "hours", "data_id", "type", "gps_coordinates")
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
    elif engine == "google_shopping":
        out["shopping_results"] = [{k: x.get(k) for k in ("title", "link", "product_link", "source", "price", "extracted_price", "rating", "reviews")}
                                   for x in (d.get("shopping_results") or [])[:40]]
    elif engine == "amazon":
        out["organic_results"] = [{k: x.get(k) for k in ("title", "link", "link_clean", "price", "extracted_price", "rating", "reviews")}
                                  for x in (d.get("organic_results") or [])[:20]]
    elif engine == "google_flights":
        def fl(x):
            return {"price": x.get("price"), "total_duration": x.get("total_duration"),
                    "flights": [{"airline": f.get("airline"), "flight_number": f.get("flight_number"), "duration": f.get("duration"),
                                 "departure_airport": {"id": (f.get("departure_airport") or {}).get("id"), "time": (f.get("departure_airport") or {}).get("time")},
                                 "arrival_airport": {"id": (f.get("arrival_airport") or {}).get("id"), "time": (f.get("arrival_airport") or {}).get("time")}}
                                for f in (x.get("flights") or [])]}
        out["best_flights"] = [fl(x) for x in (d.get("best_flights") or [])[:8]]
        out["other_flights"] = [fl(x) for x in (d.get("other_flights") or [])[:8]]
        pi = d.get("price_insights") or {}
        out["price_insights"] = {k: pi.get(k) for k in ("lowest_price", "price_level", "typical_price_range")}
        out["price_insights"]["price_history"] = (pi.get("price_history") or [])[-40:]
    elif engine == "google_hotels":
        out["properties"] = [{"name": x.get("name"), "overall_rating": x.get("overall_rating"), "reviews": x.get("reviews"),
                              "hotel_class": x.get("hotel_class"), "extracted_hotel_class": x.get("extracted_hotel_class"),
                              "link": x.get("link"),
                              "rate_per_night": {"extracted_lowest": (x.get("rate_per_night") or {}).get("extracted_lowest")},
                              "gps_coordinates": x.get("gps_coordinates")}
                             for x in (d.get("properties") or [])[:15]]
    elif engine == "google_finance":
        news = d.get("news_results") or []
        flat = []
        for n in news:
            for it in (n.get("items") if isinstance(n, dict) and n.get("items") else [n]):
                if isinstance(it, dict):
                    flat.append({"title": it.get("title") or it.get("snippet", ""), "link": it.get("link", ""),
                                 "source": it.get("source", ""), "date": it.get("date", "")})
        sm = d.get("summary") or {}
        out["summary"] = {k: sm.get(k) for k in ("title", "stock", "exchange", "price", "extracted_price", "currency")}
        out["summary"]["price_movement"] = sm.get("price_movement")
        out["graph"] = [{"price": p.get("price"), "date": p.get("date")} for p in (d.get("graph") or [])[-250:]]
        stats = (d.get("key_stats") or {}).get("stats") or []
        out["key_stats"] = {"stats": [{"label": x.get("label"), "value": x.get("value")} for x in stats[:12] if isinstance(x, dict)]}
        out["news_results"] = flat[:10]
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
                        "data_id": x.get("data_id", ""), "type": x.get("type", ""),
                        "gps": ((x.get("gps_coordinates") or {}).get("latitude"), (x.get("gps_coordinates") or {}).get("longitude"))
                        if x.get("gps_coordinates") else None})
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

    # ------------------------------------------------------------ Deals / Travel / Money
    def shopping(self, q):
        """Google Shopping -> [{title, store, price, link, rating, reviews}] (India)"""
        if self.mock:
            import mock_extra
            raw = mock_extra.shopping(q)
            self._mock_log("shopping", q, raw)
        else:
            raw = self._get(engine="google_shopping", q=q, gl="in", hl="en").get("shopping_results", [])
        return [{"title": x.get("title") or "", "store": x.get("source") or "", "price": x.get("extracted_price"),
                 "link": x.get("product_link") or x.get("link") or "", "rating": x.get("rating"), "reviews": x.get("reviews") or 0}
                for x in raw if x.get("extracted_price")]

    def amazon(self, q):
        """SerpApi Amazon engine (amazon.in) -> same shape as shopping()"""
        if self.mock:
            import mock_extra
            raw = mock_extra.amazon(q)
            self._mock_log("amazon", q, raw)
        else:
            raw = self._get(engine="amazon", k=q, amazon_domain="amazon.in").get("organic_results", [])
        out = []
        for x in raw:
            price = x.get("extracted_price")
            if price:
                out.append({"title": x.get("title") or "", "store": "Amazon.in", "price": price,
                            "link": x.get("link_clean") or x.get("link") or "", "rating": x.get("rating"),
                            "reviews": x.get("reviews") or 0})
        return out

    def flights(self, origin, dest, outbound, ret=None):
        """Google Flights -> {options:[{airline, price, duration_min, stops, dep, arr, ...}], insight:{lowest, level, typical, history}}"""
        if self.mock:
            import mock_extra
            out = mock_extra.flights(origin, dest, outbound, ret)
            self._mock_log("flights", f"{origin}-{dest} {outbound}", out["options"])
            return out
        params = dict(engine="google_flights", departure_id=origin, arrival_id=dest, outbound_date=outbound,
                      currency="INR", hl="en", gl="in", type=1 if ret else 2)
        if ret:
            params["return_date"] = ret
        d = self._get(**params)
        opts = []
        for x in (d.get("best_flights") or []) + (d.get("other_flights") or []):
            legs = x.get("flights") or []
            if not legs or not x.get("price"):
                continue
            opts.append({"airline": legs[0].get("airline", ""), "price": x["price"], "duration_min": x.get("total_duration"),
                         "stops": len(legs) - 1, "dep": (legs[0].get("departure_airport") or {}).get("time", ""),
                         "arr": (legs[-1].get("arrival_airport") or {}).get("time", ""), "from": origin, "to": dest,
                         "flight_no": legs[0].get("flight_number", "")})
        opts.sort(key=lambda o: o["price"])
        pi = d.get("price_insights") or {}
        hist = [p[1] for p in (pi.get("price_history") or []) if isinstance(p, list) and len(p) > 1]
        return {"options": opts, "insight": {"lowest": pi.get("lowest_price"), "level": pi.get("price_level"),
                                             "typical": pi.get("typical_price_range"), "history": hist}}

    def hotels(self, city, check_in, check_out):
        """Google Hotels -> [{name, rating, reviews, stars, per_night, link}]"""
        if self.mock:
            import mock_extra
            out = mock_extra.hotels(city, check_in, check_out)
            self._mock_log("hotels", city, out)
            return out
        d = self._get(engine="google_hotels", q=f"hotels in {city}", check_in_date=check_in, check_out_date=check_out,
                      adults=2, currency="INR", gl="in", hl="en")
        out = []
        for x in d.get("properties", []):
            night = (x.get("rate_per_night") or {}).get("extracted_lowest")
            if night:
                out.append({"name": x.get("name", ""), "rating": x.get("overall_rating"), "reviews": x.get("reviews") or 0,
                            "stars": x.get("extracted_hotel_class"), "per_night": night, "link": x.get("link", "")})
        return out

    def places(self, city):
        """Top attractions via Google Maps (with GPS for distances)"""
        if self.mock:
            import mock_extra
            out = mock_extra.places(city)
            self._mock_log("places", city, out)
            return out
        return self.maps(f"top tourist attractions in {city}")

    def travel_news(self, city):
        """News about flights / strikes / weather for a destination"""
        if self.mock:
            import mock_extra
            out = mock_extra.travel_news(city)
            self._mock_log("travel_news", city, out)
            return out
        return self.news(f"{city} flights OR strike OR weather OR delay")

    def finance(self, symbol, name):
        """Google Finance quote page -> {price, change_pct, movement, graph[], stats[], news[]}"""
        if self.mock:
            import mock_extra
            out = mock_extra.finance(symbol, name)
            self._mock_log("finance", symbol, [{"price": out["price"], "change_pct": out["change_pct"]}])
            return out
        d = self._get(engine="google_finance", q=symbol, hl="en")
        sm = d.get("summary") or {}
        pm = sm.get("price_movement") or {}
        pct = pm.get("percentage")
        if isinstance(pct, (int, float)) and str(pm.get("movement", "")).lower() == "down":
            pct = -abs(pct)
        graph = [p["price"] for p in (d.get("graph") or []) if isinstance(p.get("price"), (int, float))]
        return {"symbol": symbol, "name": sm.get("title") or name, "price": sm.get("extracted_price"),
                "currency": sm.get("currency") or "₹", "change_pct": pct, "movement": pm.get("movement"), "graph": graph,
                "stats": (d.get("key_stats") or {}).get("stats", []),
                "news": [n for n in d.get("news_results", []) if n.get("title")]}

    def stock_news(self, name, symbol=""):
        if self.mock:
            import mock_extra
            out = mock_extra.stock_news(name, symbol or name)
            self._mock_log("stock_news", name, out)
            return out
        return self.news(f"{name} share price stock")

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
    if "mock-finance" in url:
        import mock_extra
        return mock_extra.announcement(url)
    if _is_scammy(url):
        return ("# Congratulations! You are today's WINNER!\n\nClaim your prize of Rs 25,00,000 now. "
                "Limited time offer - act now! To receive your reward, pay a small processing fee of Rs 999 "
                "and enter your OTP and UPI PIN to verify KYC. This is Paytm official lottery. "
                "Click here to verify your account immediately or it will be blocked.")
    return ("# Sharma Sweets\n\nFresh mithai made daily since 1985. Visit us at Main Market. "
            "Call 011-2345678 for bulk orders. Cash on delivery available. Email: care@sharmasweets.in")


serp = Serp()
