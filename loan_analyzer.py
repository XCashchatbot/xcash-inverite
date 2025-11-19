import os
import json
import re
from typing import Dict, Any
from dotenv import load_dotenv
import OpenAI
from report_features import extract_critical_signals

# Load .env and check OpenAI key
load_dotenv()
if not os.environ.get("OPENAI_API_KEY"):
    raise ValueError("OPENAI_API_KEY is missing from environment")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# Safe JSON parser
# ============================================================
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

# ============================================================
# Summary Agent
# ============================================================
def summarize_features_with_llm(features: Dict[str, Any]) -> str:
    feats_json = json.dumps(features, indent=2)
    system_msg = (
        "You are an AI assistant that helps underwriters by summarizing financial reports. "
        "You only use the provided features—never guess."
    )
    user_msg = f"""
Below are the structured features extracted from a client's Inverite bank report:

{feats_json}

Write 3–6 short paragraphs summarizing:
- Income level & consistency
- Payday loan activity
- NSF/overdrafts
- Gambling activity (if present)
- E-transfer volume
- Overall affordability

Do NOT fabricate values.
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

# ============================================================
# Decision Agent
# ============================================================
def make_underwriting_decision(
    features: Dict[str, Any],
    ai_summary: str,
    report_text: str,
    loan_amount: float,
) -> Dict[str, Any]:

    feats_json = json.dumps(features, indent=2)

    gambling = features.get("gambling") or {}
    g_detected = bool(gambling.get("gambling_detected", False))
    g_count = int(gambling.get("gambling_txn_count_30d", 0))
    g_max = float(gambling.get("gambling_max_single_amount", 0.0))

    report_trunc = report_text[:8000] if report_text else ""

    user_msg = f"""
You are an AI underwriter for Xcash.

FEATURES:
{feats_json}

SUMMARY:
{ai_summary}

APPLICANT REQUESTED: ${loan_amount}

RULES:
- DECLINE if gambling_txn_count_30d ≥ 3 or max_single ≥ 150
- DECLINE if 3+ active payday loans
- DECLINE if repeated NSF/overdrafts
- APPROVE FULL if income stable, low NSF, ≤5 loans
- APPROVE LOWER if borderline

Return ONLY JSON:
{{
  "decision": "...",
  "approved_amount": ...,
  "rationale": "..."
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.1,
        messages=[
            {"role": "system", "content": "You are a strict loan underwriter. Return JSON only."},
            {"role": "user", "content": user_msg},
        ],
    )

    result = safe_parse_json(response.choices[0].message.content)

    # Hard override for gambling
    if g_detected and (g_count >= 3 or g_max >= 150):
        result["decision"] = "Declined"
        result["approved_amount"] = None
        result["rationale"] = f"Gambling detected: {g_count} txns, max ${g_max}"

    return result

# ============================================================
# Main Analysis Function (FULL WORKING VERSION)
# ============================================================
def analyze_bank_statement(report_dict, report_text, loan_amount):
    try:
        # Extract features
        features = extract_critical_signals(report_dict)

        # Summarize using LLM agent
        ai_summary = summarize_features_with_llm(features)

        # Final underwriting decision
        decision = make_underwriting_decision(
            features=features,
            ai_summary=ai_summary,
            report_text=report_text,
            loan_amount=loan_amount,
        )

        # Attach context for logging
        decision["features"] = features
        decision["summary"] = ai_summary
        return decision

    except Exception as e:
        return {
            "decision": "Error",
            "approved_amount": None,
            "rationale": f"Analyzer crashed: {e}",
        }

# ============================================================
# Backwards compatibility wrapper
# ============================================================
def make_loan_decision(report_dict, report_text, loan_amount):
    return analyze_bank_statement(report_dict, report_text, loan_amount)
