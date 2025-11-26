# backend/app/main.py
import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from decimal import Decimal
from typing import List, Optional
from .db import SessionLocal, engine, Base
from .models import Provider
from .services import get_live_rates, compute_for_provider, _normalize, compute_financial_score
from .llm_router import call_all_models, deterministic_merge
from fastapi.middleware.cors import CORSMiddleware

# Ensure DB tables exist
Base.metadata.create_all(bind=engine)

app = FastAPI()

# allow all origins for POC (fine for POC; lock this in production)
app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

# Serve frontend static files from ../frontend/dist if present
DIST_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(DIST_PATH):
    app.mount("/", StaticFiles(directory=DIST_PATH, html=True), name="frontend")

class QuoteRequest(BaseModel):
    from_currency: str
    to_currency: str
    amount: Decimal
    criteria: Optional[List[str]] = ["best_landing", "lowest_fees"]
    top_n: Optional[int] = 3
    weights: Optional[dict] = None

class AIRequest(BaseModel):
    quote_payload: dict
    user_intent: Optional[str] = "Please recommend the best channel and explain why."
    provider_codes: Optional[List[str]] = None

@app.post("/quote")
def quote(req: QuoteRequest):
    from_cur = req.from_currency.upper()
    to_cur = req.to_currency.upper()
    amount = req.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")

    db = SessionLocal()
    providers = db.query(Provider).all()
    if not providers:
        raise HTTPException(status_code=500, detail="no providers available")

    fx_json = get_live_rates(base=from_cur)
    rates = fx_json.get("rates", {})
    if to_cur not in rates:
        raise HTTPException(status_code=400, detail=f"currency pair {from_cur}->{to_cur} not available")
    rate = Decimal(str(rates[to_cur]))

    numeric_results = []
    for p in providers:
        res = compute_for_provider(amount, rate, p.fee_rules)
        numeric_results.append({
            "provider_id": p.id,
            "code": p.code,
            "name": p.name,
            "country": p.country,
            "landing": res["landing"],
            "effective_rate": res["effective_rate"],
            "fees": res["fees"],
            "fee_breakdown": res["fee_breakdown"],
            "notes": p.notes,
        })

    default_weights = {
        "fees_fx": 0.5,
        "trust": 0.15,
        "service": 0.15,
        "customer_satisfaction": 0.1,
        "reliability": 0.06,
        "speed": 0.04,
    }
    req_weights = req.weights if isinstance(req.weights, dict) else None
    if req_weights:
        merged = default_weights.copy()
        for k, v in req_weights.items():
            if k in merged:
                merged[k] = float(v)
        total = sum(merged.values()) or 1.0
        weights = {k: v / total for k, v in merged.items()}
    else:
        weights = default_weights

    best_landing = max(r["landing"] for r in numeric_results) if numeric_results else 0.0
    best_fees = min(r["fees"] for r in numeric_results) if numeric_results else 0.0
    if best_fees == 0:
        best_fees = min((r["fees"] for r in numeric_results if r["fees"]>0), default=1.0)

    augmented = []
    for p in providers:
        match = next((x for x in numeric_results if x["code"] == p.code), None)
        if not match:
            continue
        landing = match["landing"]
        fees = match["fees"]

        financial_score = compute_financial_score(landing, fees, best_landing, best_fees)
        trust_n = _normalize(p.trust_score)
        service_n = _normalize(p.service_quality)
        cust_n = _normalize(p.customer_satisfaction)
        reliability_n = _normalize(p.reliability)
        speed_n = _normalize(p.speed_score)

        composite = (
            weights["fees_fx"] * financial_score +
            weights["trust"] * trust_n +
            weights["service"] * service_n +
            weights["customer_satisfaction"] * cust_n +
            weights["reliability"] * reliability_n +
            weights["speed"] * speed_n
        )

        augmented.append({
            **match,
            "component_scores": {
                "financial": round(financial_score, 6),
                "trust": round(trust_n, 6),
                "service": round(service_n, 6),
                "customer_satisfaction": round(cust_n, 6),
                "reliability": round(reliability_n, 6),
                "speed": round(speed_n, 6),
            },
            "composite_score": round(composite, 6),
        })

    results_sorted = sorted(augmented, key=lambda x: -x["composite_score"])
    top = results_sorted[: req.top_n]
    return {"from": from_cur, "to": to_cur, "amount": float(amount), "rate": float(rate), "results": top, "weights": weights}

@app.post("/ai/recommend")
def ai_recommend(req: AIRequest):
    quote = req.quote_payload or {}
    from_cur = quote.get("from")
    to_cur = quote.get("to")
    amount = quote.get("amount")
    results = quote.get("results", [])

    summary_lines = [f"Transfer {amount} {from_cur} -> {to_cur}. Providers:"]
    for r in results:
        summary_lines.append(f"- {r.get('code')} | landing={r.get('landing')} {to_cur} | fees={r.get('fees')} | fx={r.get('effective_rate')}")
    summary_text = "\n".join(summary_lines)
    prompt = f"""You are an assistant that compares transfer channels.
User intent: {req.user_intent}

Numeric summary:
{summary_text}

Please:
1) Recommend the best channel (single-line).
2) Give a brief explanation of tradeoffs (2-4 bullets).
3) If applicable, call out any assumptions.

Answer succinctly.
"""
    model_results = call_all_models(prompt)
    merged = deterministic_merge(model_results)
    return {"model_results": model_results, "merged": merged}
