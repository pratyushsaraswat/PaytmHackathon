"""Trust Score engine - the single brain shared by the Checker (customers) and the Copilot (merchants).

Score is 0-100, HIGHER = SAFER.  Start at 60 ("unknown") and move up/down on evidence.
It is deliberately rule-based + explainable; the LLM only writes the plain-language wording.
"""
import re

SCAM_WORDS = re.compile(
    r"scam|fraud|fake|cheat|phishing|ponzi|lost (my )?money|complaint|beware|duped|looted|"
    r"blacklist|not genuine|never delivered|avoid", re.I)
GOOD_WORDS = re.compile(r"genuine|trusted|legit|verified|good service|recommended|reliable", re.I)
CREDIBLE = ("consumercomplaints", "mouthshut", "trustpilot", "reddit", "quora", "consumerhelpline",
            "cybercrime", "gov.in", "timesofindia", "hindustantimes", "ndtv", "indiatoday",
            "economictimes", "thehindu", "livemint", "news18", "scamadviser", "scamdoc")

# red flags inside a page / message
PAGE_FLAGS = [
    (r"\botp\b|\bupi pin\b|\bpin\b.*\benter|enter.*\bpin\b|\bcvv\b", "Asks for your OTP / UPI PIN (genuine services never do)"),
    (r"processing fee|advance (payment|fee)|registration fee|pay .{0,25}to (receive|claim|get)", "Asks you to PAY first to receive money or a prize"),
    (r"winner|congratulations|lottery|jackpot|you have won|claim your (prize|reward)", "Fake prize / lottery language"),
    (r"limited time|act now|immediately|within \d+ ?(min|hour)|will be blocked|expires", "Fake urgency to rush you"),
    (r"kyc.{0,30}(update|verify|expire)|verify your account", "Fake KYC / account-verification pressure"),
]
BRANDS = ["paytm", "sbi", "hdfc", "icici", "phonepe", "google pay", "gpay", "amazon", "flipkart", "npci", "rbi"]


def _domain(link):
    m = re.search(r"https?://([^/]+)", link or "")
    return (m.group(1) if m else link or "").lower()


def _credible(link, source=""):
    s = (_domain(link) + " " + (source or "")).lower()
    return any(c in s for c in CREDIBLE)


def page_flags(text, url=""):
    """Scan page/message text -> list of red-flag strings."""
    flags = [msg for pat, msg in PAGE_FLAGS if re.search(pat, text, re.I)]
    dom = _domain(url)
    if dom:
        for b in BRANDS:
            if b in text.lower() and b.replace(" ", "") not in dom:
                flags.append(f"Claims to be '{b.title()}' but the domain ({dom}) doesn't match")
                break
    return flags


def compute(search_results=(), news_results=(), flags=(), maps_place=None, kind="name", user_reports=0):
    """-> dict(trust, risk, verdict, headline, complaints, reasons[], evidence[])"""
    score = 60
    reasons, evidence = [], []
    complaints = 0

    # 1) scam / complaint mentions in Google results
    s_pen = 0
    good_hits = 0
    for r in search_results:
        text = f"{r['title']} {r['snippet']}"
        if SCAM_WORDS.search(text):
            complaints += 1
            cred = _credible(r["link"], r.get("source", ""))
            s_pen += 9 if cred else 6
            evidence.append({"title": r["title"], "link": r["link"], "tag": "complaint", "credible": cred})
        elif GOOD_WORDS.search(text):
            good_hits += 1
            evidence.append({"title": r["title"], "link": r["link"], "tag": "positive", "credible": _credible(r["link"])})
    s_pen = min(s_pen, 45)
    score -= s_pen
    if complaints:
        reasons.append({"type": "bad", "text": f"{complaints} scam/complaint reports found in web results"})

    # 2) news
    n_hits = [n for n in news_results if SCAM_WORDS.search(n["title"])]
    if n_hits:
        complaints += len(n_hits)
        score -= min(12 * len(n_hits), 25)
        reasons.append({"type": "bad", "text": f"{len(n_hits)} news article(s) warn about this: “{n_hits[0]['title'][:80]}”"})
        for n in n_hits:
            evidence.append({"title": n["title"], "link": n["link"], "tag": "news", "credible": True})

    # 2b) reports from Paytm users (community loop, stored in SQLite)
    if user_reports:
        complaints += user_reports
        score -= min(10 * user_reports, 40)
        reasons.append({"type": "bad", "text": f"Reported as a scam by {user_reports} Paytm user(s)"})

    # 3) page / message red flags
    if flags:
        score -= min(14 * len(flags), 45)
        for f in flags:
            reasons.append({"type": "bad", "text": f})

    # 4) positives
    if good_hits:
        score += min(5 * good_hits, 12)
        reasons.append({"type": "good", "text": f"{good_hits} independent source(s) describe it as genuine/trusted"})
    if maps_place and maps_place.get("rating"):
        rt, rv = maps_place["rating"], maps_place.get("reviews", 0)
        if rt >= 4.2 and rv >= 50:
            score += 20
            reasons.append({"type": "good", "text": f"Real business on Google Maps: {rt}★ from {rv} reviews"})
        elif rt >= 3.8:
            score += 10
            reasons.append({"type": "good", "text": f"Listed on Google Maps: {rt}★ ({rv} reviews)"})
        elif rt < 3.0:
            score -= 10
            reasons.append({"type": "bad", "text": f"Poor Google Maps rating: {rt}★"})

    # 5) nothing found at all -> be honest, never say "safe"
    if not (search_results or news_results or flags or maps_place or user_reports):
        reasons.append({"type": "info", "text": "No reports found online. That does NOT prove it is safe - new scam IDs have no history yet."})
        score = min(score, 55)
    elif not reasons:
        reasons.append({"type": "info", "text": "No scam reports found in web results or news."})

    score = max(0, min(100, score))
    if score >= 70:
        verdict, headline = "safe", "Looks safe"
    elif score >= 40:
        verdict, headline = "caution", "Be careful"
    else:
        verdict, headline = "risky", "Risky - do not pay"
    if verdict == "risky" and complaints:
        headline = f"Risky: {complaints} complaints found"
    elif verdict == "caution" and not (search_results or news_results or flags or maps_place or user_reports):
        headline = "No reports found - stay cautious"

    return {"trust": score, "risk": 100 - score, "verdict": verdict, "headline": headline,
            "complaints": complaints, "reasons": reasons, "evidence": evidence[:6]}
