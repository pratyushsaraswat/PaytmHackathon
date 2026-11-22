"""Paytm Smart Deals: live prices (Google Shopping + Amazon via SerpApi) matched with Paytm offers."""
import logging
import re
import statistics
import time

import offers
from parallel import run_parallel
from serp import serp

log = logging.getLogger("deals")


def _relevant(title, q):
    """Keep a result only if it names most of the words searched (drops cases, chargers, unrelated items)."""
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2]
    if not words:
        return True
    t = title.lower()
    return sum(w in t for w in words) >= max(1, (len(words) + 1) // 2)


def run(product):
    t0 = time.perf_counter()
    log.info("deals IN product=%r serp=%s engines=google_shopping,amazon offers=local_demo llm=none",
             product, "mock" if serp.mock else "live")
    r, errors = run_parallel({"g": lambda: serp.shopping(product), "a": lambda: serp.amazon(product)})
    raw = r["g"] + r["a"]
    items = [x for x in raw if x.get("price") and _relevant(x["title"], product)]
    if len(items) >= 3:  # accessories (cases, cables) are far cheaper than the product: drop outliers
        med = statistics.median(x["price"] for x in items)
        items = [x for x in items if x["price"] >= 0.4 * med]
    log.info("deals product=%r raw=%d relevant=%d", product, len(raw), len(items))

    by_store = {}
    for x in items:
        k = offers.store_key(x["store"])
        if k and (k not in by_store or x["price"] < by_store[k]["price"]):
            by_store[k] = x
    stores = []
    for x in by_store.values():
        o = offers.best_offer(x["store"], x["price"])
        stores.append({"store": x["store"], "title": x["title"], "price": int(x["price"]), "link": x["link"],
                       "rating": x["rating"], "reviews": x["reviews"], "offer": o})
    stores.sort(key=lambda s: s["offer"]["effective"])

    out = {"product": product, "mode": "mock" if serp.mock else "live", "offers_source": "demo", "stores": stores,
           "errors": errors, "headline": f"No prices found for “{product}”. Try a more specific product name.",
           "cheapest_sticker": None, "best": None, "extra_saving": 0}
    if not stores:
        log.info("deals done serp=%s stores=0 offers=local_demo llm=none errors=%d ms=%d",
                 out["mode"], len(errors), (time.perf_counter() - t0) * 1000)
        return out
    cs = min(stores, key=lambda s: s["price"])
    be = stores[0]
    out["cheapest_sticker"], out["best"] = cs["store"], be["store"]
    out["extra_saving"] = cs["price"] - be["offer"]["effective"]
    kind = "cashback" if be["offer"]["kind"] == "cashback" else "bank offer"
    if be["store"] == cs["store"]:
        out["headline"] = (f"Best price: {offers.inr(cs['price'])} on {cs['store']}. Pay with Paytm and it effectively "
                           f"comes to {offers.inr(be['offer']['effective'])} — {be['offer']['label']}.")
    else:
        out["headline"] = (f"Cheapest sticker price is {offers.inr(cs['price'])} on {cs['store']}, but with the Paytm {kind} "
                           f"it's {offers.inr(be['offer']['effective'])} on {be['store']} ({be['offer']['label']}).")
    log.info("deals done serp=%s stores=%d best=%s offers=local_demo llm=none errors=%d ms=%d",
             out["mode"], len(stores), out.get("best"), len(errors), (time.perf_counter() - t0) * 1000)
    return out
