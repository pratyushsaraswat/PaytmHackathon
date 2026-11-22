"""Paytm Travel Planner: Google Flights + Hotels + Maps + News (SerpApi) -> itinerary and a 'book now or wait' signal."""
import logging
import math
import time
from datetime import date, timedelta

import offers
from parallel import run_parallel
from serp import serp

log = logging.getLogger("travel")

IATA = {"delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM", "bengaluru": "BLR", "bangalore": "BLR", "hyderabad": "HYD",
        "chennai": "MAA", "kolkata": "CCU", "goa": "GOI", "jaipur": "JAI", "pune": "PNQ", "ahmedabad": "AMD",
        "kochi": "COK", "lucknow": "LKO", "varanasi": "VNS", "udaipur": "UDR", "srinagar": "SXR", "chandigarh": "IXC",
        "guwahati": "GAU", "patna": "PAT", "bhubaneswar": "BBI", "indore": "IDR", "nagpur": "NAG", "amritsar": "ATQ",
        "leh": "IXL", "port blair": "IXZ", "thiruvananthapuram": "TRV", "coimbatore": "CJB", "dehradun": "DED"}
NEWS_KW = [("strike", "🚧 Strike"), ("protest", "🚧 Strike"), ("bandh", "🚧 Strike"),
           ("rain", "🌧️ Weather"), ("storm", "🌧️ Weather"), ("flood", "🌧️ Weather"), ("cyclone", "🌧️ Weather"),
           ("fog", "🌫️ Weather"), ("heatwave", "🌧️ Weather"), ("weather", "🌧️ Weather"),
           ("flight", "✈️ Flights"), ("airport", "✈️ Flights"), ("delay", "✈️ Flights"), ("cancel", "✈️ Flights")]


def resolve(city):
    c = (city or "").strip().lower()
    if c in IATA:
        return IATA[c], c.title()
    if len(c) == 3 and c.isalpha():
        return c.upper(), c.upper()
    raise ValueError(f"Unknown city “{city}”. Try: " + ", ".join(sorted({k.title() for k in IATA})[:12]) + " …")


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def signal(insight, price):
    level, typ = insight.get("level"), insight.get("typical") or []
    if not level and price and len(typ) == 2:
        level = "low" if price <= typ[0] else "high" if price >= typ[1] else "typical"
    rng = f" (typical {offers.inr(typ[0])}–{offers.inr(typ[1])})" if len(typ) == 2 else ""
    if level == "low":
        return {"action": "BOOK NOW", "tone": "good", "why": f"{offers.inr(price)} is a LOW fare for this route{rng}. Prices rarely stay this low."}
    if level == "high":
        return {"action": "WAIT", "tone": "bad", "why": f"{offers.inr(price)} is HIGH for this route{rng}. Try other dates or check again in a few days."}
    if level == "typical":
        return {"action": "FAIR PRICE", "tone": "warn", "why": f"{offers.inr(price)} is a typical fare{rng}. Book if your dates are fixed."}
    return {"action": "NO SIGNAL", "tone": "warn", "why": "Google has no price insight for this route and date."}


def _alerts(news, city):
    out, c = [], city.lower()
    for n in news:
        t = n["title"].lower()
        if c not in t:  # same lesson as the scam checker: ignore headlines that do not name the place
            continue
        tag = next((lab for kw, lab in NEWS_KW if kw in t), None)
        out.append({"tag": tag or "ℹ️ Info", "warn": bool(tag), "title": n["title"], "source": n.get("source", ""), "link": n.get("link", "")})
    out.sort(key=lambda a: not a["warn"])
    return out[:4]


def _itinerary(places, nights, depart_text, flight):
    ps = [p for p in places if p.get("gps") and p.get("rating")]
    ps.sort(key=lambda p: (-p["rating"], -p["reviews"]))
    need = 1 + 2 * max(0, nights - 1)          # arrival day 1 stop, middle days 2 stops, departure day 0
    pool, order = ps[:max(need, 1)], []
    if pool:                                   # nearest-neighbour order so each day's stops are close together
        cur = pool.pop(0)
        order.append((cur, None))
        while pool:
            nxt = min(pool, key=lambda p: km(cur["gps"], p["gps"]))
            order.append((nxt, round(km(cur["gps"], nxt["gps"]), 1)))
            pool.remove(nxt)
            cur = nxt
    days, i = [], 0
    for d in range(nights + 1):
        n = 1 if d == 0 else 0 if d == nights else 2
        stops = [{"name": p["title"], "rating": p["rating"], "km_from_prev": dist,
                  "cab_min": int(dist * 3 + 5) if dist else None} for p, dist in order[i:i + n]]
        i += n
        title = "Fly out & settle in" if d == 0 else "Fly back" if d == nights else f"Explore · day {d + 1}"
        if d == 0 and flight:
            title += f" · {flight['airline']}, departs {flight['dep'][-5:]}"
        days.append({"day": d + 1, "title": title, "stops": stops})
    return days


def run(origin, dest, depart=None, nights=3):
    o_code, o_name = resolve(origin)
    d_code, d_name = resolve(dest)
    if o_code == d_code:
        raise ValueError("Origin and destination are the same city.")
    nights = max(1, min(int(nights or 3), 14))
    dep = date.fromisoformat(depart) if depart else date.today() + timedelta(days=30)
    if dep < date.today():
        raise ValueError("Departure date is in the past.")
    ret = dep + timedelta(days=nights)
    t0 = time.perf_counter()
    sm = "mock" if serp.mock else "live"
    log.info("travel IN %s->%s depart=%s nights=%d serp=%s engines=google_flights,google_hotels,google_maps,google_news llm=none",
             o_code, d_code, dep, nights, sm)

    r, errors = run_parallel({
        "fl": lambda: serp.flights(o_code, d_code, dep.isoformat(), ret.isoformat()),
        "ho": lambda: serp.hotels(d_name, dep.isoformat(), ret.isoformat()),
        "pl": lambda: serp.places(d_name),
        "nw": lambda: serp.travel_news(d_name),
    }, defaults={"fl": {"options": [], "insight": {}}})
    fl = r["fl"]
    opts = fl.get("options", [])
    hotels = sorted([h for h in r["ho"] if h.get("per_night")], key=lambda h: h["per_night"])
    good = [h for h in hotels if (h.get("rating") or 0) >= 3.8] or hotels
    best_value = good[0] if good else None
    top_rated = max(hotels, key=lambda h: (h.get("rating") or 0, h.get("reviews", 0))) if hotels else None
    picks = []
    for h in (best_value, top_rated):
        if h and h not in picks:
            picks.append(h)
    for h in hotels:
        if len(picks) >= 3:
            break
        if h not in picks:
            picks.append(h)

    out = {"origin": o_name, "destination": d_name, "from": o_code, "to": d_code, "depart": dep.isoformat(), "return": ret.isoformat(),
           "nights": nights, "mode": "mock" if serp.mock else "live", "flights": opts[:3], "signal": None,
           "price_history": (fl.get("insight") or {}).get("history", []), "hotels": picks, "alerts": _alerts(r["nw"], d_name),
           "itinerary": _itinerary(r["pl"], nights, dep.isoformat(), opts[0] if opts else None), "cost": None, "errors": errors}
    if opts:
        out["signal"] = signal(fl.get("insight") or {}, opts[0]["price"])
    if opts and best_value:
        total = 2 * opts[0]["price"] + best_value["per_night"] * nights     # 2 travellers, 1 room
        out["cost"] = {"travellers": 2, "flights": 2 * opts[0]["price"], "hotel": best_value["per_night"] * nights, "total": total,
                       "postpaid_emi": math_ceil3(total), "hotel_name": best_value["name"], "flight_name": opts[0]["airline"]}
    log.info(
        "travel done serp=%s route=%s->%s flights=%d hotels=%d signal=%s offers=local_postpaid_demo llm=none errors=%d ms=%d",
        sm, o_code, d_code, len(out["flights"]), len(out["hotels"]),
        (out["signal"] or {}).get("action"), len(errors), (time.perf_counter() - t0) * 1000,
    )
    return out


def math_ceil3(total):
    return math.ceil(total / 3)
