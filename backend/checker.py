"""Customer side: 'Pay Safely' - check a UPI ID / link / merchant name / offer message BEFORE paying."""
import logging
import re
import time

import ai

log = logging.getLogger("checker")
import db
import trust
from parallel import run_parallel
from serp import serp

UPI_RE = re.compile(r"^[a-zA-Z0-9._\-]{2,}@[a-zA-Z]{2,}$")
URL_RE = re.compile(r"(https?://[^\s]+|(?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,}(?:/[^\s]*)?)", re.I)


def detect(q):
    q = q.strip()
    if UPI_RE.match(q) or re.fullmatch(r"[^\s@]+@[^\s@]+", q):  # UPI handle or email-style ID
        return "upi"
    if " " not in q and URL_RE.fullmatch(q):
        return "url"
    if len(q) > 40 and " " in q:
        return "message"
    return "name"


def _norm_url(u):
    return u if u.startswith("http") else "https://" + u


def check(query):
    t_start = time.perf_counter()
    q = query.strip()
    kind = detect(q)
    flags, maps_place, page_text = [], None, ""

    # plan every SerpApi call, then run them in parallel
    jobs = {}
    if kind == "upi":
        jobs["s1"] = lambda: serp.search(f"\"{q}\" scam OR fraud OR complaint")
        jobs["n"] = lambda: serp.news(f"{q} scam")
    elif kind == "url":
        url = _norm_url(q)
        dom = re.sub(r"^https?://|^www\.", "", url).split("/")[0]
        jobs["s1"] = lambda: serp.search(f"{dom} scam OR fraud OR complaint")
        jobs["s2"] = lambda: serp.search(f"{dom} reviews legit")
        jobs["n"] = lambda: serp.news(f"{dom} scam")
        jobs["x"] = lambda: serp.extract(url)
    elif kind == "message":
        m = URL_RE.search(q)
        short = " ".join(q.split()[:10])
        jobs["s1"] = lambda: serp.search(f"\"{short}\" scam")
        jobs["n"] = lambda: serp.news(f"{short} scam alert")
        if m:
            url = _norm_url(m.group(0))
            jobs["x"] = lambda: serp.extract(url)
        page_text = q
    else:  # merchant / seller name
        jobs["s1"] = lambda: serp.search(f"{q} scam OR fraud OR complaint")
        jobs["s2"] = lambda: serp.search(f"{q} reviews")
        jobs["n"] = lambda: serp.news(f"{q} fraud")
        jobs["m"] = lambda: serp.maps(q)

    res, errors = run_parallel(jobs, defaults={"x": ""})

    seen, search_results = set(), []
    for r in (res.get("s1") or []) + (res.get("s2") or []):  # same page can match both queries
        if r["link"] not in seen:
            seen.add(r["link"])
            search_results.append(r)
    news = res.get("n") or []
    # keep only evidence that really mentions what we are checking (no generic scam-awareness pages)
    n_raw = (len(search_results), len(news))
    search_results = [r for r in search_results if trust.relevant(r, q, kind)]
    news = [n for n in news if trust.relevant({"title": n["title"], "snippet": "", "link": n["link"]}, q, kind)]
    dropped = (n_raw[0] - len(search_results), n_raw[1] - len(news))
    if kind == "name" and res.get("m"):
        maps_place = res["m"][0]

    if kind in ("url", "message"):
        extracted = res.get("x") or ""
        page_text = (page_text + "\n" + extracted).strip()
        src_url = _norm_url(q) if kind == "url" else (URL_RE.search(q).group(0) if URL_RE.search(q) else "")
        flags = trust.page_flags(page_text, src_url if kind == "url" else "")
        look = trust.lookalike(src_url) if src_url else None
        if look:
            flags.append(look)

    reports = db.count_reports(q)
    out = trust.compute(search_results, news, flags, maps_place, kind, user_reports=reports)
    brand = trust.known_brand(q) if kind == "url" else None
    if brand:  # official site of a well-known brand: never show it as risky
        out["trust"], out["risk"] = max(out["trust"], 85), min(out["risk"], 15)
        out["verdict"], out["headline"] = "safe", f"Official site of {brand}"
        out["reasons"].insert(0, {"type": "good", "text": f"Domain belongs to a known brand ({brand}). Always check the spelling of the address."})
    out.update({"query": q, "kind": kind, "user_reports": reports, "mode": "mock" if serp.mock else "live",
                "sources_used": sorted(k for k in res), "errors": errors})
    ai_text = ai.explain_check(q, out)
    out["summary"] = ai_text or _fallback_summary(out)
    log.info(
        "check kind=%s trust=%s verdict=%s data: serp=%s jobs=%s search=%d news=%d "
        "dropped_irrelevant=%s sqlite_reports=%d summary_from=%s errors=%d total_ms=%d",
        kind, out["trust"], out["verdict"], "mock" if serp.mock else "live",
        sorted(res.keys()), len(search_results), len(news), dropped, reports,
        "openai" if ai_text else "rule_based", len(errors), (time.perf_counter() - t_start) * 1000,
    )
    if errors:
        log.warning("check serp errors=%s", errors)
    return out


def _fallback_summary(r):
    if r["verdict"] == "risky":
        return "Do not pay. " + (r["reasons"][0]["text"] + ". " if r["reasons"] else "") + "Report it if someone sent you this."
    if r["verdict"] == "caution":
        return "Proceed carefully. Double-check who you are paying and avoid sharing OTP or PIN with anyone."
    return "No scam reports found and the business looks real. Still, never share your OTP or UPI PIN."
