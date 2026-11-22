"""Paytm offers for the Smart Deals tab.

IMPORTANT: Paytm has no public offers API, so this is an ILLUSTRATIVE rule table (cashback %, bank offers,
Postpaid EMI). In production replace `best_offer()` with a call to Paytm's offers service. The UI labels it as demo.
"""
import math
import re

# store key -> (cashback %, cap in Rs).  Applies when paying with Paytm UPI / wallet.
OFFERS = {
    "amazon": (5, 750), "flipkart": (4, 600), "croma": (6, 1000),
    "reliance digital": (5, 800), "tata cliq": (5, 500), "vijay sales": (4, 400),
}
DEFAULT = (2, 200)
BANK = {"min": 10000, "pct": 10, "cap": 1500, "label": "10% instant discount on Paytm-partner bank cards (up to ₹1,500)"}
BANK_STORES = {"flipkart", "reliance digital", "tata cliq", "vijay sales"}   # stores running the card offer in this demo
EMI_MIN = 5000


def store_key(name):
    n = re.sub(r"\.(in|com|co\.in)$", "", (name or "").strip().lower())
    return re.sub(r"\s+", " ", n)


def inr(n):
    """Indian digit grouping: 1234567 -> ₹12,34,567"""
    n = int(round(n))
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + "₹" + s


def best_offer(store, price):
    pct, cap = OFFERS.get(store_key(store), DEFAULT)
    cash = int(min(price * pct / 100, cap))
    bank = int(min(price * BANK["pct"] / 100, BANK["cap"])) if price >= BANK["min"] and store_key(store) in BANK_STORES else 0
    if bank > cash:
        kind, saving, label = "bank", bank, BANK["label"]
    else:
        kind, saving, label = "cashback", cash, f"{pct}% Paytm cashback (up to {inr(cap)})"
    emi = None
    if price >= EMI_MIN:
        emi = {"months": 3, "monthly": math.ceil((price - saving) / 3), "label": "No-cost EMI on Paytm Postpaid"}
    return {"kind": kind, "saving": saving, "label": label, "effective": int(price - saving), "emi": emi}
