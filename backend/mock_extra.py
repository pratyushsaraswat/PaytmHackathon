"""Deterministic sample data for the Deals / Travel / Money tabs (used when SERPAPI_KEY is missing or MOCK=1)."""
import hashlib
import random
import re
from datetime import date

MOCK_FIN = "mock-finance"


def _seed(s):
    return int(hashlib.md5(str(s).lower().encode()).hexdigest(), 16)


# ------------------------------------------------------------------ shopping
PRODUCT_BASE = [("iphone", 79900), ("macbook", 99900), ("laptop", 55000), ("television", 32000), (" tv", 32000),
                ("phone", 18000), ("headphone", 3500), ("earbuds", 2500), ("watch", 9000), ("refrigerator", 28000),
                ("washing machine", 26000), (" ac", 36000), ("camera", 45000), ("shoes", 3500), ("tablet", 25000)]
STORES = ["Amazon.in", "Flipkart", "Croma", "Reliance Digital", "Tata CLiQ", "Vijay Sales"]


def _base_price(q):
    ql = " " + q.lower()
    for k, v in PRODUCT_BASE:
        if k in ql:
            return v
    return 1500 + _seed(q) % 40000


def _p(base, rnd):
    return int(round(base * rnd.uniform(0.94, 1.08) / 100) * 100 - 1)


def _fmt(n):
    return "₹" + f"{n:,}"


def shopping(q):
    rnd, base = random.Random(_seed("sh" + q)), _base_price(q)
    out = []
    for store in STORES[1:]:
        p = _p(base, rnd)
        out.append({"title": f"{q.title()} (Black)", "source": store, "price": _fmt(p), "extracted_price": p,
                    "link": f"https://example.com/{store.split()[0].lower()}/{q.replace(' ', '-')}",
                    "rating": round(rnd.uniform(4.0, 4.7), 1), "reviews": rnd.randint(200, 9000)})
    acc = max(199, base // 40)   # an accessory result: the app must ignore it
    out.append({"title": f"Protective case cover for {q.title()}", "source": "Flipkart", "price": _fmt(acc),
                "extracted_price": acc, "link": "https://example.com/case", "rating": 4.1, "reviews": 3000})
    return out


def amazon(q):
    rnd, base = random.Random(_seed("am" + q)), _base_price(q)
    p = _p(base, rnd)
    return [{"title": f"{q.title()} (Black, Latest Model)", "source": "Amazon.in", "price": _fmt(p), "extracted_price": p,
             "link": f"https://example.com/amazon/{q.replace(' ', '-')}", "rating": round(rnd.uniform(4.1, 4.6), 1),
             "reviews": rnd.randint(500, 20000)}]


# -------------------------------------------------------------------- travel
def flights(o, d, outbound, ret):
    rnd = random.Random(_seed(f"fl{o}{d}"))
    base = rnd.randint(3200, 8800) * (2 if ret else 1)
    air = ["IndiGo", "Air India", "Akasa Air", "SpiceJet", "Air India Express"]
    opts = []
    for i in range(5):
        h, m = rnd.randint(5, 21), rnd.choice([0, 15, 30, 45])
        dur = rnd.randint(85, 190)
        stops = 0 if rnd.random() < 0.8 else 1
        opts.append({"airline": air[i], "price": int(base * rnd.uniform(0.9, 1.25) / 10) * 10, "duration_min": dur + stops * 70,
                     "stops": stops, "dep": f"{outbound} {h:02d}:{m:02d}",
                     "arr": f"{outbound} {(h + (dur + m) // 60) % 24:02d}:{(m + dur) % 60:02d}",
                     "from": o, "to": d, "flight_no": f"{air[i][:2].upper()} {rnd.randint(100, 999)}"})
    opts.sort(key=lambda x: x["price"])
    level = rnd.choice(["low", "typical", "high"])
    lo, hi = int(base * 0.9), int(base * 1.2)
    price = {"low": int(base * 0.82), "typical": base, "high": int(base * 1.32)}[level]
    opts[0]["price"] = price
    return {"options": opts, "insight": {"lowest": price, "level": level, "typical": [lo, hi],
                                         "history": [int(base * rnd.uniform(0.85, 1.25)) for _ in range(20)] + [price]}}


def hotels(city, ci, co):
    rnd = random.Random(_seed("ho" + city))
    names = ["Grand Residency", "Heritage Palace Inn", "City Centre Suites", "Lakeview Resort", "Budget Stay", "Royal Orchid"]
    out = []
    for n in names:
        night = rnd.randint(1400, 9500)
        out.append({"name": f"{city.title()} {n}", "rating": round(rnd.uniform(3.4, 4.7), 1), "reviews": rnd.randint(80, 4000),
                    "stars": rnd.choice([2, 3, 3, 4, 5]), "per_night": night, "link": "https://example.com/hotel"})
    return out


def places(city):
    rnd = random.Random(_seed("pl" + city))
    lat, lng = 12 + (_seed(city) % 1800) / 100, 73 + (_seed(city[::-1]) % 1500) / 100
    names = ["Old Fort", "Central Museum", "Lakeside Viewpoint", "Heritage Market", "City Gardens", "Riverfront Promenade",
             "Ancient Temple", "Sunset Point"]
    return [{"title": f"{n} ({city.title()})", "rating": round(rnd.uniform(4.0, 4.8), 1), "reviews": rnd.randint(300, 25000),
             "address": city.title(), "hours": "Open ⋅ Closes 6 PM", "data_id": f"mock-pl-{i}", "type": "Tourist attraction",
             "gps": (round(lat + rnd.uniform(-.04, .04), 5), round(lng + rnd.uniform(-.04, .04), 5))}
            for i, n in enumerate(names)]


def travel_news(city):
    rnd = random.Random(_seed("tn" + city + str(date.today())))
    pool = [f"{city.title()}: dense fog disrupts morning flights, several delayed",
            f"Heavy rain alert issued for {city.title()} over the weekend",
            f"{city.title()} tourism picks up ahead of the holiday season",
            f"Taxi union strike in {city.title()} called off after talks",
            f"New heritage walk launched in {city.title()} old city"]
    rnd.shuffle(pool)
    return [{"title": t, "link": "https://news.example.com/travel", "source": "Mock News", "date": "today"} for t in pool[:3]]


# --------------------------------------------------------------------- money
def _fin_numbers(sym):
    rnd = random.Random(_seed(f"fin{sym}{date.today()}"))
    price = round(rnd.uniform(120, 3800), 2)
    pct = round(rnd.uniform(-4.2, 4.2), 2)
    return rnd, price, pct


def finance(sym, name):
    rnd, price, pct = _fin_numbers(sym)
    prev = price / (1 + pct / 100)
    vals, v = [], prev * rnd.uniform(0.9, 1.1)
    for _ in range(29):
        v *= rnd.uniform(0.985, 1.015)
        vals.append(round(v, 2))
    vals.append(round(price, 2))
    return {"symbol": sym, "name": name, "price": price, "currency": "₹", "change_pct": pct,
            "movement": "Up" if pct >= 0 else "Down", "graph": vals,
            "stats": [{"label": "Market cap", "value": f"{rnd.randint(20, 900) * 1000} Cr"},
                      {"label": "P/E ratio", "value": str(round(rnd.uniform(12, 60), 1))},
                      {"label": "Dividend yield", "value": f"{round(rnd.uniform(0, 2.5), 2)}%"}],
            "news": []}


def stock_news(name, sym):
    rnd, price, pct = _fin_numbers(sym)
    up = pct >= 0
    slug = re.sub(r"\W+", "-", name.lower())
    items = [
        f"{name} Q2 results: net profit {'rises' if up else 'falls'} {abs(round(pct * 2.5, 1))}% year on year, revenue {'beats' if up else 'misses'} estimates",
        f"Brokerages {'raise' if up else 'cut'} target price on {name} after quarterly update",
        f"{name} {'bags' if up else 'faces delay in'} large order worth ₹{rnd.randint(300, 4000)} crore",
        f"Sensex, Nifty {'edge higher' if up else 'slip'} as FII flows {'turn positive' if up else 'stay weak'}",
        f"{name} board declares interim dividend of ₹{rnd.randint(2, 20)} per share",
    ]
    return [{"title": t, "link": f"https://news.example.com/{MOCK_FIN}/{slug}/{i}?up={int(up)}", "source": rnd.choice(["Mock Business", "Mock Markets"]),
             "date": "today"} for i, t in enumerate(items)]


def announcement(url):
    m = re.search(rf"{MOCK_FIN}/([^/]+)/", url)
    name = (m.group(1) if m else "company").replace("-", " ").title()
    rnd = random.Random(_seed(url))
    up = "up=1" in url
    return (f"# {name} announces results for the quarter ended September 30\n\n"
            f"{name} reported consolidated net profit of ₹{rnd.randint(500, 9000)} crore, {'up' if up else 'down'} "
            f"{rnd.randint(2, 18)}% year on year. Revenue from operations stood at ₹{rnd.randint(8000, 90000)} crore. "
            f"The Board has declared an interim dividend of ₹{rnd.randint(2, 20)} per share. "
            f"Management said demand remained {'strong' if up else 'subdued'} and margins were {'stable' if up else 'under pressure'}.")
