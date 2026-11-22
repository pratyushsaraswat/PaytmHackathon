"""Merchant side: 'Grow My Business' - Merchant Growth Copilot for Paytm for Business."""
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import ai
import db
import trust
from serp import serp

log = logging.getLogger("copilot")

THEMES = {
    "delivery":  (r"deliver|late by|delay|waited .{0,12}(hour|min)", "delivery speed",
                  "Promise delivery in 45 minutes and show it on your Paytm QR / Soundbox message"),
    "packaging": (r"packag|box|spill|leak|crush", "packaging",
                  "Use sturdy gift boxes and advertise 'spill-proof packing' - offer a ₹20 gift-box add-on"),
    "price":     (r"expensive|overpriced|costly|too much", "pricing",
                  "Launch value combos or a 'pay with Paytm, get 5% off' deal"),
    "quality":   (r"stale|taste|not fresh|bad food|old", "freshness/quality",
                  "Put 'made fresh today' with the date on your counter - customers notice"),
    "service":   (r"rude|staff|behaviou?r|ignored", "staff behaviour",
                  "Train one friendly 'greeter' for peak hours - it is the cheapest win"),
    "wait":      (r"queue|long wait|slow|crowd", "waiting time / billing",
                  "Put a Paytm QR + Soundbox at a second counter so billing is instant"),
    "hygiene":   (r"dirty|hygien|not clean|cockroach|flies", "cleanliness",
                  "Display a daily cleaning checklist and photos - trust grows fast"),
    "payment":   (r"upi|qr|cash only|payment|card", "digital payment options",
                  "Show a big Paytm QR at the counter and offer cashback for the first UPI payment"),
}

FESTIVALS = [(1, 1, "New Year"), (1, 14, "Makar Sankranti / Pongal"), (1, 26, "Republic Day"),
             (2, 14, "Valentine's Day"), (12, 25, "Christmas")]


def next_festival(today=None):
    today = today or date.today()
    best = None
    for m, d, name in FESTIVALS:
        for yr in (today.year, today.year + 1):
            dt = date(yr, m, d)
            if dt >= today and (best is None or dt < best[0]):
                best = (dt, name)
    return best[1], (best[0] - today).days


def themes_in(reviews, max_rating=3):
    c = Counter()
    for r in reviews:
        if r.get("rating") is not None and r["rating"] <= max_rating and r.get("snippet"):
            for k, (pat, _, _) in THEMES.items():
                if re.search(pat, r["snippet"], re.I):
                    c[k] += 1
    return c


def _trend_change(vals):
    if len(vals) < 8:
        return 0
    recent = sum(vals[-4:]) / 4
    before = sum(vals[:-4]) / max(1, len(vals) - 4)
    return round((recent - before) / before * 100) if before else 0


def run(business, category, location):
    area = f"{category} near {location}"
    errors = []

    # ---- stage 1: maps for competitors + own shop, trends, news (parallel)
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_comp = ex.submit(serp.maps, area)
        f_own = ex.submit(serp.maps, f"{business} {location}")
        f_tr = ex.submit(serp.trends, category)
        f_news = ex.submit(serp.news, f"{category} market demand {location}")
        f_scam = ex.submit(serp.search, f"{business} {location} scam OR fraud OR complaint")

        def get(f, default):
            try:
                return f.result()
            except Exception as e:
                errors.append(str(e))
                return default

        comps_raw, own_raw = get(f_comp, []), get(f_own, [])
        trend, news, scam_res = get(f_tr, []), get(f_news, []), get(f_scam, [])

    own = next((p for p in own_raw if business.lower().split()[0] in p["title"].lower()), None)
    comps = [c for c in comps_raw if not own or c["title"] != own["title"]][:6]
    comps = [c for c in comps if c.get("rating")]

    # ---- stage 2: reviews (low-rated first) for top competitors + own shop
    targets = sorted(comps, key=lambda c: -c["reviews"])[:3]
    rev = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {c["title"]: ex.submit(serp.reviews, c["data_id"], c["title"]) for c in targets if c.get("data_id")}
        if own and own.get("data_id"):
            futs["__own__"] = ex.submit(serp.reviews, own["data_id"], own["title"])
        for k, f in futs.items():
            try:
                rev[k] = f.result()
            except Exception as e:
                errors.append(str(e))
                rev[k] = []

    comp_reviews = [r for k, v in rev.items() if k != "__own__" for r in v]
    comp_themes = themes_in(comp_reviews)
    own_themes = themes_in(rev.get("__own__", []))
    n_checked = len([k for k in rev if k != "__own__"])

    # ---- Trust Score for the merchant (same engine the customer-side checker uses)
    t = trust.compute(scam_res, [], [], own)
    avg_rating = round(sum(c["rating"] for c in comps) / len(comps), 1) if comps else None

    # ---- build tips (rule-based, always works)
    tips, nudges = [], []
    top = comp_themes.most_common(2)
    for key, cnt in top[:1]:
        _, label, action = THEMES[key]
        tips.append(f"{cnt} low-rated reviews across the {n_checked} biggest {category} places near you complain about {label}. "
                    f"Chance for you: {action}.")
    if len(top) > 1:
        _, label, action = THEMES[top[1][0]]
        tips.append(f"Second gap: {label} ({top[1][1]} complaints near you). {action}.")

    chg = _trend_change(trend)
    if trend and chg >= 10:
        tips.append(f"Searches for '{category}' in India are up {chg}% in the last 4 weeks. Stock up early and push it at your counter.")
        nudges.append({"icon": "💰", "title": "Stock-up loan, pre-approved",
                       "body": f"Demand for {category} is rising {chg}%. Get a Paytm business loan to buy stock before rivals do.",
                       "cta": "See offer"})
    elif trend and chg <= -10:
        tips.append(f"Searches for '{category}' dipped {abs(chg)}% recently. Run a small QR cashback offer to keep footfall steady.")

    fest, days = next_festival()
    if days <= 60:
        tips.append(f"{fest} is in {days} days. Customers search early - put up a festive offer now and share your QR on WhatsApp status.")
        if not any(n["icon"] == "💰" for n in nudges):
            nudges.append({"icon": "💰", "title": f"{fest} stock-up loan",
                           "body": f"{fest} is {days} days away. Stock up with a quick Paytm business loan.", "cta": "See offer"})

    if own and avg_rating:
        diff = round(own["rating"] - avg_rating, 1)
        if diff < 0:
            tips.append(f"Your rating is {own['rating']}★ vs {avg_rating}★ average nearby. Ask happy customers to review you right after Paytm payment.")
        else:
            tips.append(f"Nice - your {own['rating']}★ beats the {avg_rating}★ nearby average. Put it on a poster next to your QR.")
    if own_themes:
        k = own_themes.most_common(1)[0][0]
        tips.append(f"Your own low reviews mention {THEMES[k][1]}. Fixing this raises your Trust Score fastest.")
    if news:
        tips.append(f"In the news: “{news[0]['title'][:90]}” - use it in your offer this week.")
    if comp_themes.get("payment") or comp_themes.get("wait"):
        nudges.append({"icon": "📣", "title": "Soundbox + QR welcome offer",
                       "body": "Rivals lose customers at billing. Offer ₹10 cashback on the first 50 Paytm QR payments.", "cta": "Create offer"})
    if not nudges:
        nudges.append({"icon": "📣", "title": "QR cashback offer",
                       "body": "Attract new customers: ₹10 cashback on the first 50 Paytm QR payments.", "cta": "Create offer"})

    db.add_score(business, location, t["trust"])
    hist = db.history(business, location)

    ai_tips = ai.polish_tips(business, tips) if tips else None

    # ---- trust improvement plan
    plan = []
    if not own:
        plan.append("Claim your Google Business profile so customers (and the Checker) can find and verify you")
    if own and own["reviews"] < 50:
        plan.append(f"Collect more reviews: you have {own['reviews']}, aim for 50+")
    if own_themes:
        plan.append(f"Reply to and fix complaints about {THEMES[own_themes.most_common(1)[0][0]][1]}")
    plan.append("Keep a visible Paytm QR so customers see a verified payment identity")

    log.info(
        "copilot serp=%s trust=%s competitors=%d tips=%d llm=%s errors=%d",
        "mock" if serp.mock else "live", t["trust"], len(comps), len(tips),
        "yes" if ai_tips else "no", len(errors),
    )
    return {
        "business": business, "category": category, "location": location,
        "mode": "mock" if serp.mock else "live", "ai_polished": bool(ai_tips),
        "trust": {**t, "own_found": bool(own), "own": own, "plan": plan},
        "history": hist,
        "tips": ai_tips or tips, "raw_tips": tips, "nudges": nudges,
        "competitors": [{"title": c["title"], "rating": c["rating"], "reviews": c["reviews"],
                         "hours": c["hours"], "address": c["address"]} for c in comps],
        "avg_rating": avg_rating,
        "gaps": [{"theme": THEMES[k][1], "count": v} for k, v in comp_themes.most_common(4)],
        "trend": {"values": trend, "change_pct": chg, "keyword": category},
        "festival": {"name": fest, "days": days},
        "news": news[:3], "errors": errors,
    }
