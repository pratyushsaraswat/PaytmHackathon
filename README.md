# Paytm Trust & Growth

> One trust engine. Safer payments for customers, more business for merchants.

Built for the **Best Use of SerpApi** track (Paytm hackathon). It uses real-time search data from SerpApi to answer two questions:

- **Customer:** *"Is it safe to pay this person?"*
- **Merchant:** *"How do I get more customers?"*

Both are powered by the same **Trust Score** (0–100, higher is safer), so the product is one platform instead of two separate tools.

---

## Features

### 🛡️ Pay Safely (customer side)
Paste a UPI ID, website link, shop name, or a suspicious message **before paying**.

- Searches Google and Google News for scam reports and complaints
- Reads the suspicious page with the SerpApi Extract API and flags red flags: asking for OTP/PIN, pay-first-to-receive-money, fake urgency, fake KYC pressure, and pages impersonating a brand
- Looks up the business on Google Maps for ratings and reviews
- Includes scam reports from other users (stored in SQLite)
- Returns a Trust Score, plain reasons, and evidence links
- If nothing is found it says *"No reports found – stay cautious"*, never "safe"

### 📈 Grow My Business (merchant side)
A Merchant Growth Copilot for Paytm for Business.

- Finds nearby competitors, ratings, and opening hours (Google Maps)
- Reads low-rated reviews to find what customers complain about at rival shops (Google Maps Reviews)
- Tracks demand with Google Trends and local news, plus upcoming festivals
- Writes WhatsApp-style weekly tips, for example *"5 low-rated reviews at nearby shops complain about packaging – make that your edge"*
- Suggests Paytm actions: stock-up loan, Soundbox + QR cashback offer
- Shows the merchant's own Trust Score, its history over time, and how to raise it

### 🔁 How the two sides connect
```
        ┌──────────── Shared SerpApi data layer ────────────┐
        │ Search · News · Maps · Reviews · Trends · Extract │
        └───────────────┬───────────────────┬───────────────┘
                        │                   │
                Customer Checker     Merchant Copilot
                        └────► TRUST SCORE ◄┘
                                  │
                        SQLite: scam reports + score history
```
Customers see a merchant's Trust Score before paying. Merchants see how to improve it. A better score brings more customers, and community reports make the whole system safer.

---

## SerpApi APIs used

| API | Used for |
|---|---|
| Google Search | Scam/complaint reports, reviews, legitimacy checks |
| Google News | Scam alerts, local demand news |
| Google Maps | Competitors, ratings, hours, business verification |
| Google Maps Reviews | What customers complain about |
| Google Trends | Demand changes for a product category |
| Extract API | Reading suspicious pages and checking for red flags |

---

## Tech stack

- **Backend:** Python, FastAPI
- **Frontend:** single-page HTML/CSS/JS, mobile-first Paytm-style UI
- **Database:** SQLite (scam reports, merchant score history)
- **Data:** SerpApi, with 24-hour on-disk caching to save credits
- **AI (optional):** OpenAI or Claude, for friendlier wording of summaries and tips
- **Deploy:** Docker, Render

---

## Project structure

```
paytm-trust-growth/
├── backend/
│   ├── app.py        # FastAPI routes
│   ├── serp.py       # SerpApi client + cache + demo-data mode
│   ├── trust.py      # Trust Score engine (rule-based, explainable)
│   ├── checker.py    # Customer flow
│   ├── copilot.py    # Merchant flow
│   ├── db.py         # SQLite: reports + score history (seeded)
│   └── ai.py         # Optional LLM wording layer (OpenAI or Claude)
├── static/index.html # UI
├── Dockerfile
├── docker-compose.yml
├── render.yaml
├── requirements.txt
└── .env.example
```

---

## Run locally

**Requirements:** Python 3.9+

```bash
git clone https://github.com/pratyushsaraswat/paytm-trust-growth.git
cd paytm-trust-growth
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Open `.env` and add your key:

```
SERPAPI_KEY=your_serpapi_key
OPENAI_API_KEY=             # optional LLM (or ANTHROPIC_API_KEY)
```

Start the server:

```bash
cd backend
DEMO_REPEAT=1 uvicorn app:app --port 8000
```
On Windows, run `set DEMO_REPEAT=1` on its own line first.

Open **http://localhost:8000**.

> **No SerpApi key?** The app still works in **DEMO data** mode with built-in sample data. The badge at the top shows `DEMO data` or `LIVE data`.

### Run with Docker
```bash
docker compose up --build
```

---

## Deploy (Render)

1. Push this repo to GitHub. `.env` is git-ignored, so never commit keys.
2. On Render choose **New → Blueprint** and select the repo (it reads `render.yaml`).
3. Add `SERPAPI_KEY` in the Render dashboard under **Environment**.
4. Open the live link a few minutes before presenting (free tier sleeps when idle).

---

## Demo script (3 minutes)

1. **Merchant:** *Grow* tab → "Get this week's tips" → Trust Score, history, WhatsApp tips, Paytm nudges.
2. **Scam caught:** *Pay* tab → "Fake lottery UPI" → Risky, with evidence and community reports.
3. **Community loop:** "Brand-new UPI" → cautious, no reports → tap **Report** → the score drops with "Reported by 1 Paytm user". Report twice more and it turns Risky.
4. **Close:** one engine protects customers and helps merchants.

Reset between rehearsals:
```bash
curl -X POST https://your-app-url/api/demo/reset
```

---

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/check` | POST | `{ "query": "<UPI / link / name / message>" }` → verdict, score, reasons, evidence |
| `/api/report` | POST | `{ "query": "...", "reason": "..." }` → saves a scam report |
| `/api/copilot` | POST | `{ "business_name", "category", "location" }` → tips, competitors, trends, Trust Score |
| `/api/health` | GET | Mode (live/demo) and status |
| `/api/demo/reset` | POST | Clears non-seeded data (only when `DEMO_REPEAT=1`) |

---

## How the Trust Score works

Starts at 60 ("unknown") and moves on evidence:

- **Down:** scam/complaint results (more weight for credible sources), news warnings, user reports, page red flags, brand impersonation, poor Maps rating
- **Up:** independent sources calling it genuine, a real Google Maps listing with good ratings

**Verdict:** 70+ looks safe · 40–69 be careful · below 40 risky.

The scoring is rule-based so every score is explainable. With no evidence at all, the app never says "safe".

---

## Limitations

- A search-based check can't prove an ID is safe. A brand-new scam ID has no history yet.
- This is an extra safety layer, not a replacement for Paytm's own fraud detection.
- The Paytm integration (loan offers, payment buttons) is simulated for the prototype.
- User reports need safeguards at scale (minimum independent reports, reporter reliability weighting) to prevent false reports against genuine shops.
- Free hosting wipes the disk on restart. Sample data is re-seeded automatically.

---

## Scaling plan

- **Cache** (Redis): popular IDs and domains are checked once, then served from cache
- **Queue** (Kafka): absorbs traffic spikes; workers scale out
- **Area-level sharing:** merchants in the same area share competitor and trend data, so it's one SerpApi call per area, not per shop
- **PostgreSQL** instead of SQLite for reports and history
- **Kubernetes:** multiple replicas behind a load balancer with autoscaling

---

## Safety note

Never share your OTP or UPI PIN with anyone. Genuine services never ask for them.

---

Built for the Paytm hackathon · Powered by [SerpApi](https://serpapi.com)
