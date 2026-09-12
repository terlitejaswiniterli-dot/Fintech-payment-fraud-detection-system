"""
risk_policy.py — Layer 6: "Risk + Policy Engine".

Pure function: risk score -> decision. Kept separate from the scorer so the
thresholds (a business/compliance decision) can change without touching the
model or rules.
"""

APPROVE_MAX = 30
REVIEW_MAX = 70


def decide(risk_score: float) -> str:
    if risk_score <= APPROVE_MAX:
        return "APPROVE"
    if risk_score <= REVIEW_MAX:
        return "STEP_UP"
    return "DECLINE"