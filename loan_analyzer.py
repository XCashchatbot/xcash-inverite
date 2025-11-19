import os
import json
import re
from typing import Dict, Any
from dotenv import load_dotenv
from openai import OpenAI
from report_features import extract_critical_signals

# Load .env and check OpenAI key
load_dotenv()
if not os.environ.get("OPENAI_API_KEY"):
    raise ValueError("OPENAI_API_KEY is missing from environment")
client = OpenAI()

# ──────────────────────────────────────────────
# Safe JSON parser
# ──────────────────────────────────────────────
def safe_parse_json(message: str) -> Dict[str, Any]:
    if not message:
        return {"decision": "Error", "approved_amount": None, "rationale": "No content returned."}
    cleaned = re.sub(r"^```json|```$", "", message.strip(), flags=re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        return {"decision": "Error", "approved_amount": None, "rationale": "Invalid JSON object."}
    except Exception:
        return {"decision": "Error", "approved_amount": None, "rationale": message}

# ──────────────────────────────────────────────
# LLM Summary Agent
# ──────────────────────────────────────────────
def summarize_features_with_llm(features: Dict[str, Any]) -> str:
    feats_json = json.dumps(features, indent=2)
    system_msg = (
        "You are an AI assistant that helps underwriters by summarizing financial reports. "
        "You will be given structured financial features from a bank statement. "
        "Your job is to summarize these features accurately — do not guess or add extra data."
    )
    user_msg = f"""
Below are the structured features extracted from a client's full Inverite bank report:

{feats_json}

Write 3–6 short paragraphs summarizing:
- Income level & consistency
- Payday loan activity (new loans, deductions, active loans, lenders)
- NSF/overdraft behaviour
- Gambling activity (if present)
- E-transfer volume
- Overall affordability or risk

Do NOT fabricate values. Use only what's in the features.
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    )
    return response.choices[0].message.content.strip()

# ──────────────────────────────────────────────
# Final decision agent (includes payroll, gambling rules, etc)
# ──────────────────────────────────────────────
def make_underwriting_decision(
    features: Dict[str, Any],
    ai_summary: str,
    report_text: str,
    loan_amount: float,
) -> Dict[str, Any]:
    feats_json = json.dumps(features, indent=2)
    gambling = features.get("gambling") or features.get("gambling_summary") or {}
    g_detected = bool(gambling.get("gambling_detected", False))
    g_count = int(gambling.get("gambling_txn_count_30d", 0))
    g_total = float(gambling.get("gambling_total_amount_30d", 0.0))
    g_merchants = int(gambling.get("gambling_unique_merchants", 0))
    g_max = float(gambling.get("gambling_max_single_amount", 0.0))
    report_trunc = report_text[:10000] if report_text else ""

    user_msg = f"""
You are an AI underwriter for Xcash Financial Services.

Your job is to make a final payday loan decision based on:
- Precomputed structured features (trusted)
- Summary from an AI assistant (also trusted)
- Strict underwriting and risk rules

You must include the **payroll frequency and average net pay amount** if available, and specify whether income appears stable.

The applicant is requesting ${loan_amount:.2f}.

FEATURES:
{feats_json}

SUMMARY:
{ai_summary}

TRUNCATED BANK TEXT (context only):
{report_trunc}

GAMBLING FLAGS:
- gambling_detected: {g_detected}
- gambling_txn_count_30d: {g_count}
- gambling_max_single_amount: {g_max}

STRICT RULES:
- Decline if gambling_txn_count_30d ≥ 3 OR gambling_max_single_amount ≥ 150
- Decline if 3+ active payday loans
- Decline if repeated NSF or overdrafts
- Approve full only if income is stable, low NSF, ≤5 active loan, healthy balance
- Approve lower if borderline
- Include gambling counts in rationale if present

Include in your rationale:
- Income frequency (weekly, biweekly, semimonthly, monthly, unknown)
- Typical payroll amount per pay period
- New loans received and deductions
- Number of active payday loans
- NSF/overdraft status
- Gambling activity (include exact numbers if present)
- Final reason for decision

Return ONLY this JSON:
{{
  "decision": "Approved" | "Approved for Lower Amount" | "Declined",
  "approved_amount": <numeric or null>,
  "rationale": "Short explanation of income, loans, NSF, gambling, decision reason"
}}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.1,
            messages=[
                {"role": "system", "content": "You are a strict, accurate loan underwriter. Follow rules. Return JSON only."},
                {"role": "user", "content": user_msg},
            ],
        )
        result = safe_parse_json(response.choices[0].message.content)

        # Gambling override rule
        if g_detected and (g_count >= 3 or g_max >= 150):
            result["decision"] = "Declined"
            result["approved_amount"] = None
            result["rationale"] = (result.get("rationale", "") + f" Gambling detected: {g_count} txns, max ${g_max}.").strip()

        result.setdefault("decision", "Error")
        result.setdefault("approved_amount", None)
        result.setdefault("rationale", "No rationale returned.")
        return result
    except Exception as e:
        return {
            "decision": "Error",
            "approved_amount": None,
            "rationale": str(e),
        }

# ──────────────────────────────────────────────
# Public entry: called by process_pending
# ──────────────────────────────────────────────
def analyze_bank_statement(report_text: str, loan_amount: float) -> Dict[str, Any]:
    features = extract_critical_signals(report_text)
    summary = summarize_features_with_llm(features)
    decision_raw = make_underwriting_decision(
        features=features,
        ai_summary=summary,
        report_text=report_text,
        loan_amount=loan_amount,
    )

    # --- NORMALIZE RETURN TO ALWAYS BE DICT ---
    if isinstance(decision_raw, dict):
        decision = decision_raw
    else:
        try:
            parsed = json.loads(decision_raw)
            decision = parsed if isinstance(parsed, dict) else {
                "decision": "Error",
                "approved_amount": None,
                "rationale": f"Invalid return: {parsed}"
            }
        except:
            decision = {
                "decision": "Error",
                "approved_amount": None,
                "rationale": f"Non-dict decision: {decision_raw}"
            }

    decision["features"] = features
    decision["summary"] = summary
    return decision

def make_loan_decision(report_text: str, loan_amount: float) -> Dict[str, Any]:
    return analyze_bank_statement(report_text, loan_amount)
