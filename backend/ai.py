"""Optional Claude layer. Works fully WITHOUT a key (rule-based text is already good);
with ANTHROPIC_API_KEY it rewrites the output in friendly plain language."""
import json
import os

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")


def _client():
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None


def enabled():
    return _client() is not None


def _ask(system, user, max_tokens=700):
    c = _client()
    if not c:
        return None
    try:
        r = c.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                              messages=[{"role": "user", "content": user}])
        return r.content[0].text.strip()
    except Exception:
        return None


def explain_check(query, result):
    """One short plain-language paragraph for the customer."""
    out = _ask(
        "You are Paytm's safety assistant. In 2 short sentences, tell a regular Indian user whether to pay, "
        "using ONLY the evidence given. Never say 'safe' with certainty; if there is no evidence say so. "
        "No markdown.",
        json.dumps({"query": query, "verdict": result["verdict"], "trust": result["trust"],
                    "reasons": [r["text"] for r in result["reasons"]]}))
    return out


def polish_tips(business, tips):
    """Rewrite the rule-based tips as a friendly WhatsApp message. Returns list[str] or None."""
    out = _ask(
        "You write weekly WhatsApp tips for small Indian shopkeepers on Paytm for Business. "
        "Rewrite each tip in simple, warm, direct English (max 2 short sentences, 1 emoji, keep every number). "
        "Return ONLY a JSON list of strings, same length and order.",
        json.dumps({"business": business, "tips": tips}))
    if not out:
        return None
    try:
        s = out[out.index("["): out.rindex("]") + 1]
        res = json.loads(s)
        return res if isinstance(res, list) and len(res) == len(tips) else None
    except Exception:
        return None
