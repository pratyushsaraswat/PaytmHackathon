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
- **AI (optional):** OpenAI only (`LLM_PROVIDER=openai`), for friendlier wording of summaries and tips
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
│   └── ai.py         # Optional OpenAI wording layer (LLM_PROVIDER=openai)
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
LLM_PROVIDER=off            # set to openai to enable LLM summaries/tips
OPENAI_API_KEY=             # required when LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4o-mini    # optional
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

## Logging

Every line carries a **request id** that follows one request through the API, the parallel SerpApi calls, the LLM and the database:

```
13:15:24 INFO  [a1b2c3d4] api: api /check IN query='kbc-winner-claim@okaxis'
13:15:24 INFO  [a1b2c3d4] serp: serp data_source=http engine=google q='...'
13:15:25 INFO  [a1b2c3d4] serp: serp response engine=google n=10 ms=912 preview=[...]
13:15:25 INFO  [a1b2c3d4] checker: check kind=upi trust=17 verdict=risky ... dropped_irrelevant=(6, 0) sqlite_reports=3 summary_from=openai errors=0 total_ms=1320
13:15:25 INFO  [a1b2c3d4] api: REQ POST /api/check -> 200 in 1324ms
```

- The same id is returned in the `X-Request-ID` response header. Send your own with `-H "x-request-id: demo1"` and grep for it.
- `data_source=http|cache|mock` shows where every result came from; `summary_from=openai|rule_based` shows whether the LLM was used.
- `dropped_irrelevant=(a, b)` = search/news results ignored because they did not mention the thing being checked.
- Secrets are redacted automatically (`api_key=***`, `Bearer ***`, `sk-***`); urllib3 debug logging is disabled because it prints API keys in URLs.
- Errors: failed jobs log `job=... FAILED`, SerpApi/LLM failures log latency + reason, crashes log a full traceback and return `{"detail": "Internal error", "request_id": "..."}`.
- `LOG_LEVEL=DEBUG` adds full SerpApi JSON, the full LLM reply and per-job timings. `LOG_FILE=app.log` adds a rotating file.
- Find one request: `grep a1b2c3d4 app.log`. On Render: Logs tab, search the id.

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


## Performance & credit savings

| What | Effect |
|---|---|
| Slim cache (only used fields stored) | cache entry ~48 KB -> ~2 KB; file loads and saves fast |
| Atomic, debounced cache save | one write per request, never a corrupt file |
| Case/space-insensitive cache keys | "KBC  Scam" and "kbc scam" share one credit |
| Per-engine TTL | news 6 h, extract 1 h, everything else 24 h |
| Single-flight | identical simultaneous requests share one SerpApi call |
| Pooled HTTP session + retry | reuses TLS; retries network errors, 429 and 5xx (not 4xx) |
| Hard deadline (`JOB_TIMEOUT`, default 25 s) | one slow Google call cannot stall the request |
| Dropped redundant UPI "reviews" search | 1 credit saved per UPI check |
| gzip responses, SQLite WAL | smaller payloads, readers do not block writers |

`/api/health` now also shows `serp_cache_hits` (credits saved this run).


## New tabs: Smart Deals, Travel, Money

| Tab | Endpoint | SerpApi engines | Credits/run (live) |
|---|---|---|---|
| Smart Deals | `POST /api/deals {"product"}` | `google_shopping`, `amazon` | 2 |
| Travel | `POST /api/travel {"origin","destination","depart"?,"nights"?}` | `google_flights`, `google_hotels`, `google_maps`, `google_news` | 4 |
| Money | `POST /api/money {"stock"}` | `google_finance`, `google_news`, `extract` | 3 |

```bash
curl -s -X POST localhost:8000/api/deals  -H "Content-Type: application/json" -d '{"product":"iPhone 15 128GB"}'
curl -s -X POST localhost:8000/api/travel -H "Content-Type: application/json" -d '{"origin":"Delhi","destination":"Goa","nights":3}'
curl -s -X POST localhost:8000/api/money  -H "Content-Type: application/json" -d '{"stock":"Reliance"}'
```

- **Paytm offers are a demo table** (`backend/offers.py`): Paytm has no public offers API. Replace `best_offer()` with Paytm's offers service in production. The UI says so.
- **Travel "book now or wait"** comes from Google Flights' own price insight (low / typical / high vs the route's typical range). Itinerary stops are ordered by distance (GPS from Maps). Booking and Postpaid buttons are demo hooks.
- **Money** is educational: it says what the headlines mention, never what *caused* a move, never buy/sell advice, and always shows the "not financial advice" disclaimer. Optional OpenAI wording uses only the fetched facts.
- Cache lifetimes: finance 15 min, flights 3 h, hotels/shopping 6 h.
