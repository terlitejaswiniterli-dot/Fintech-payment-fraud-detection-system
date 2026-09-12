"""
fraud_engine.py — Layers 4 & 5: "Inline Fraud Screening" + "Hybrid Fraud Engine".

Two scorers are blended:
  1. Rules engine       - explicit, auditable thresholds (amount, new device,
                          unusual location, velocity, merchant risk).
  2. Lightweight model  - a fixed-weight logistic function over normalized
                          features, standing in for a trained ML model. Kept
                          dependency-free (numpy only) so the whole thing runs
                          offline, but the interface (extract -> score) is
                          exactly where a real model would be swapped in.

Both scorers emit per-factor contributions so every score is explainable —
judges / ops can see *why* a transaction was flagged, not just the number.
"""
import time
import numpy as np

from db import get_conn

HIGH_AMOUNT_THRESHOLD = 50000.0
VELOCITY_WINDOW_SECONDS = 60
MERCHANT_RISK = {
    # merchant_name: base risk 0-1 (demo blocklist/allowlist)
    "TrustedMart": 0.0,
    "QuickCash Traders": 0.6,
    "Unknown Merchant": 0.35,
}


def extract_features(user_row, amount, device_id, location, merchant):
    known_devices = set(filter(None, (user_row["known_devices"] or "").split(",")))
    known_locations = set(filter(None, (user_row["known_locations"] or "").split(",")))

    conn = get_conn()
    cutoff = time.time() - VELOCITY_WINDOW_SECONDS
    velocity = conn.execute(
        "SELECT COUNT(*) AS c FROM ledger WHERE user_id = ? AND created_at >= ?",
        (user_row["user_id"], cutoff),
    ).fetchone()["c"]
    conn.close()

    avg_amount = user_row["avg_amount"] or 0.0
    return {
        "amount": amount,
        "new_device": device_id not in known_devices,
        "unusual_location": location not in known_locations,
        "velocity_1min": velocity,
        "merchant_risk": MERCHANT_RISK.get(merchant, 0.4),
        "amount_vs_avg": (amount / avg_amount) if avg_amount > 0 else (3.0 if amount > 0 else 0.0),
    }


def rule_score(features):
    """Explicit, auditable rules -> (score 0-100, [(factor, contribution, detail), ...])"""
    contributions = []
    score = 0.0

    if features["amount"] > HIGH_AMOUNT_THRESHOLD:
        c = 25.0
        score += c
        contributions.append(("high_amount", c, f"Amount ₹{features['amount']:,.0f} exceeds ₹50,000 threshold"))

    if features["new_device"]:
        c = 20.0
        score += c
        contributions.append(("new_device", c, "Device not previously seen for this user"))

    if features["unusual_location"]:
        c = 15.0
        score += c
        contributions.append(("unusual_location", c, "Location not in user's known locations"))

    if features["velocity_1min"] >= 3:
        c = 25.0
        score += c
        contributions.append(("velocity", c, f"{features['velocity_1min']} transactions in last 60s"))
    elif features["velocity_1min"] >= 1:
        c = 8.0
        score += c
        contributions.append(("velocity", c, f"{features['velocity_1min']} prior transaction in last 60s"))

    if features["merchant_risk"] > 0:
        c = features["merchant_risk"] * 20.0
        score += c
        contributions.append(("merchant_risk", c, "Merchant flagged as elevated risk"))

    if features["amount_vs_avg"] >= 3:
        c = 15.0
        score += c
        contributions.append(("behavior_deviation", c,
                               f"Amount is {features['amount_vs_avg']:.1f}x user's historical average"))

    return min(score, 100.0), contributions


def ml_score(features):
    """
    Stand-in for a trained model: a fixed-weight logistic regression over
    normalized features. Weights are hand-set to mirror what a model trained
    on card-fraud data typically learns to weight most heavily (velocity and
    novelty signals dominate; amount matters but less than people expect).
    """
    x = np.array([
        min(features["amount"] / HIGH_AMOUNT_THRESHOLD, 3.0),   # normalized amount
        1.0 if features["new_device"] else 0.0,
        1.0 if features["unusual_location"] else 0.0,
        min(features["velocity_1min"] / 3.0, 2.0),
        features["merchant_risk"],
        min(features["amount_vs_avg"] / 3.0, 2.0),
    ])
    weights = np.array([0.9, 1.4, 1.1, 1.8, 1.2, 1.0])
    bias = -2.6

    z = float(np.dot(x, weights) + bias)
    prob = 1.0 / (1.0 + np.exp(-z))
    return prob * 100.0


def score_transaction(user_row, amount, device_id, location, merchant):
    features = extract_features(user_row, amount, device_id, location, merchant)
    r_score, factors = rule_score(features)
    m_score = ml_score(features)

    # Blend: rules are auditable/deterministic, model captures nonlinear interactions.
    final_score = round(0.55 * r_score + 0.45 * m_score, 1)
    final_score = max(0.0, min(100.0, final_score))

    factors.append(("ml_model", round(m_score, 1), "Hybrid model's independent risk estimate"))
    return final_score, factors, features