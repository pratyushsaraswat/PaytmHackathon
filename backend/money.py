"""Paytm Money Research Assistant: Google Finance + News + Extract (SerpApi) -> plain-language 'why did it move'.
Educational only. Never gives buy/sell advice."""
import logging
import re
import time

import ai
from parallel import run_parallel
from serp import serp

log = logging.getLogger("money")

DISCLAIMER = ("This is general information to help you understand the news, NOT financial advice or a recommendation to buy, "
              "sell or hold. Prices can fall as well as rise. Do your own research or talk to a SEBI-registered adviser.")

TICKERS = {
    "reliance": ("RELIANCE:NSE", "Reliance Industries"), "tcs": ("TCS:NSE", "TCS"), "infosys": ("INFY:NSE", "Infosys"),
    "hdfc bank": ("HDFCBANK:NSE", "HDFC Bank"), "icici bank": ("ICICIBANK:NSE", "ICICI Bank"), "sbi": ("SBIN:NSE", "State Bank of India"),
    "itc": ("ITC:NSE", "ITC"), "tata motors": ("TATAMOTORS:NSE", "Tata Motors"), "wipro": ("WIPRO:NSE", "Wipro"),
    "adani enterprises": ("ADANIENT:NSE", "Adani Enterprises"), "airtel": ("BHARTIARTL:NSE", "Bharti Airtel"),
    "bharti airtel": ("BHARTIARTL:NSE", "Bharti Airtel"), "zomato": ("ETERNAL:NSE", "Eternal (Zomato)"),
    "paytm": ("PAYTM:NSE", "Paytm (One 97 Communications)"), "one97": ("PAYTM:NSE", "Paytm (One 97 Communications)"),
    "maruti": ("MARUTI:NSE", "Maruti Suzuki"), "hul": ("HINDUNILVR:NSE", "Hindustan Unilever"), "l&t": ("LT:NSE", "Larsen & Toubro"),
}

THEMES = [
    ("results", r"result|earnings|profit|revenue|net income|quarter|q[1-4]\b|ebitda",
     "Quarterly results: the company's 3-monthly report card. If profit or sales beat what analysts expected, the share usually rises; a miss usually pushes it down."),
    ("orders", r"order|contract|deal|acqui|stake|merger|partnership|bags|wins",
     "New orders or deals: more future business for the company, which investors generally like."),
    ("analyst", r"target price|brokerage|upgrade|downgrade|rating|analyst",
     "Analyst views: brokerages changed their estimate of a fair price. Their reports can nudge many people to buy or sell on the same day."),
    ("regulatory", r"sebi|probe|penalt|fine\b|court|ban\b|notice|regulat|investigation",
     "Regulatory or legal news: penalties or investigations create uncertainty, and shares often react quickly."),
    ("dividend", r"dividend|buyback|bonus|split",
     "Dividend or buyback: the company is returning cash to shareholders, usually seen as a positive sign."),
    ("management", r"ceo\b|resign|appoint|chairman|director",
     "Management change: a new or departing leader can change investor confidence."),
]
MARKET_RE = r"sensex|nifty|market|fii|dii|rbi|fed\b|crude|rupee|global"
FACT_RE = r"net profit|profit after tax|revenue|ebitda|dividend|order book|margin|guidance|loss"


def resolve(text):
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not t:
        raise ValueError("Enter a company name or NSE ticker, e.g. Reliance or TCS.")
    if t in TICKERS:
        return TICKERS[t]
    for k, v in TICKERS.items():
        if k in t or t in k:
            return v
    if re.fullmatch(r"[a-z0-9&\-]{2,15}", t):
        return f"{t.upper()}:NSE", t.upper()
    raise ValueError("We couldn't recognise that company. Try a listed company name (e.g. Infosys) or its NSE ticker (e.g. INFY).")


def _themes(texts):
    found = []
    for key, rx, explain in THEMES:
        n = sum(bool(re.search(rx, t, re.I)) for t in texts)
        if n:
            found.append({"key": key, "count": n, "explain": explain})
    found.sort(key=lambda x: -x["count"])
    return found


def _facts(md):
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", md or ""):
        s = s.strip(" #*-")
        if 25 <= len(s) <= 260 and re.search(FACT_RE, s, re.I):
            out.append(s)
    return out[:3]


def run(stock):
    t0 = time.perf_counter()
    sym, name = resolve(stock)
    log.info("money IN sym=%s name=%r serp=%s llm_config=%s engines=google_finance,google_news[,extract]",
             sym, name, "mock" if serp.mock else "live", ai.status_line())
    r, errors = run_parallel({"fin": lambda: serp.finance(sym, name), "news": lambda: serp.stock_news(name, sym)},
                             defaults={"fin": {}})
    fin = r["fin"]
    if not fin or fin.get("price") is None:
        raise ValueError(f"No live quote found for {sym}. Check the spelling or try the NSE ticker.")
    name = fin.get("name") or name
    first = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()[0]
    seen, news = set(), []
    for n in (fin.get("news") or []) + (r["news"] or []):
        k = n["title"].lower()[:60]
        if k not in seen:
            seen.add(k)
            news.append(n)
    company = [n for n in news if first in n["title"].lower() or sym.split(":")[0].lower() in n["title"].lower()][:5]
    market = [n for n in news if n not in company and re.search(MARKET_RE, n["title"], re.I)][:2]
    log.info("money %s news_total=%d company=%d market=%d", sym, len(news), len(company), len(market))

    # read the most relevant announcement/results page with the Extract API
    facts, read_from = [], None
    cand = next((n for n in company if re.search(r"result|earnings|announce|quarter|dividend|order", n["title"], re.I)),
                company[0] if company else None)
    if cand and cand.get("link"):
        er, e2 = run_parallel({"x": lambda: serp.extract(cand["link"])}, timeout=15, defaults={"x": ""})
        errors += e2
        facts = _facts(er["x"])
        read_from = {"title": cand["title"], "link": cand["link"]} if facts else None

    pct, price = fin.get("change_pct"), fin["price"]
    themes = _themes([n["title"] for n in company] + facts)
    if pct is None:
        move = f"{name} is trading at ₹{price:,.2f}."
    else:
        move = f"{name} is {'up' if pct >= 0 else 'down'} {abs(pct):.2f}% today at ₹{price:,.2f}."
    if themes:
        drivers = " Headlines around this move mention: " + ", ".join(t["key"] for t in themes[:3]) + ". This shows what the news flow looks like; it does not prove what caused the move."
    elif market:
        drivers = " We found no company-specific news, so the move may be driven by the overall market (see market headlines below)."
    else:
        drivers = " We found no clear news for today. Small daily moves are normal and often have no single reason."
    summary = move + drivers
    ai_text = ai.explain_stock({"stock": name, "move_pct": pct, "price": price, "headlines": [n["title"] for n in company],
                                "announcement_excerpts": facts})
    log.info(
        "money done serp=%s sym=%s price=%s summary_from=%s llm_config=%s extract=%s errors=%d ms=%d",
        "mock" if serp.mock else "live", sym, price,
        "openai" if ai_text else "rule_based", ai.status_line(), bool(read_from), len(errors),
        (time.perf_counter() - t0) * 1000,
    )
    return {"symbol": sym, "name": name, "mode": "mock" if serp.mock else "live", "price": price, "change_pct": pct,
            "graph": fin.get("graph", []), "stats": (fin.get("stats") or [])[:4], "summary": summary, "summary_ai": ai_text,
            "themes": themes[:4], "news": company, "market_news": market, "announcement": {"facts": facts, "source": read_from},
            "glossary": "New to investing? A share price moves when more people want to buy than sell (up) or sell than buy (down). News changes what people expect about future profits.",
            "disclaimer": DISCLAIMER, "errors": errors}
