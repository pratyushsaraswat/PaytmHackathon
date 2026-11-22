# Paytm Trust & Growth  (SerpApi track)

One Trust Score engine, two apps:
- **Pay Safely** - check a UPI ID / link / shop / message before paying (Search + News + Extract + Maps + community reports)
- **Grow My Business** - Merchant Growth Copilot (Maps + Reviews + Trends + News), with Trust Score history
- **SQLite** stores scam reports and merchant score history (seeded with sample data on startup)

## Run locally
    pip install -r requirements.txt
    cp .env.example .env          # add SERPAPI_KEY (no key = DEMO data mode)
    cd backend && DEMO_REPEAT=1 uvicorn app:app --port 8000     # Windows: set DEMO_REPEAT=1 first
    open http://localhost:8000

## Run with Docker
    docker compose up --build     # reads .env, http://localhost:8000

## Deploy (Render)
1. Push to GitHub (.env is git-ignored - never commit keys).
2. Render -> New -> Blueprint (uses render.yaml) or Web Service -> Docker.
3. Add SERPAPI_KEY in the Render dashboard -> Environment.
4. Open the link ~5 min before presenting (free tier sleeps).

## Demo script (3 minutes)
1. Merchant: Grow tab -> "Get this week's tips" -> Trust Score, WhatsApp tips, Paytm nudges, score history.
2. Customer: Pay tab -> "Fake lottery UPI" -> Risky + evidence (seeded with 3 community reports).
3. Community loop: "Brand-new UPI" -> "be careful, no reports" -> tap Report -> score drops ("Reported by 1 user").
   Report twice more -> becomes Risky. Reset between rehearsals: curl -X POST <url>/api/demo/reset
4. Close: same engine protects customers and helps merchants; scales via cache + Kafka.

## Notes
- DEMO_REPEAT=1 allows repeat reports and the reset endpoint; remove it for real use (then 1 report per user).
- Free hosts wipe disk on restart; sample data is re-seeded automatically.
