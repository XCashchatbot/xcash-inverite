# === loan_analyzer.py ===
import os
import json
import re
from collections import Counter
from string import Template

from dotenv import load_dotenv
from openai import OpenAI

# ──────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────
load_dotenv()
# Optional: still fine to rely only on OpenAI() which reads env
os.environ.get("OPENAI_API_KEY")  # ensure it exists
client = OpenAI()


# ──────────────────────────────────────────────
# Helper: Safe JSON parsing for AI output
# ──────────────────────────────────────────────
def safe_parse_json(message: str):
    """
    Cleans and parses AI JSON output, even if wrapped in code fences or text.
    Always returns a dict with at least keys: decision, approved_amount, rationale.
    """
    if message is None:
        return {
            "decision": "Error",
            "approved_amount": None,
            "rationale": "No content returned from model.",
        }

    # Remove code fences like ```json ... ``` and stray backticks
    cleaned = message.strip()
    cleaned = re.sub(r"^```json", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"^```|```$", "", cleaned).strip()
    cleaned = cleaned.replace("```", "").strip()

    # Extract JSON object if embedded in other text
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        parsed = json.loads(cleaned)
        # Ensure we always return a dict
        if isinstance(parsed, dict):
            return parsed
        else:
            return {
                "decision": "Error",
                "approved_amount": None,
                "rationale": f"Non-object JSON returned by model: {parsed}",
            }
    except Exception:
        return {
            "decision": "Error",
            "approved_amount": None,
            "rationale": message,
        }


# ──────────────────────────────────────────────
# Gambling pre-scan (regex-based)
# ──────────────────────────────────────────────
GAMBLING_KEYWORDS = [
    r"gambl", r"casino", r"\blotto\b", r"lottery", r"sportsbook", r"\bbet\b", r"betting",
]
GAMBLING_BRANDS = [
    "betano", "bet365", "betmgm", "draftkings", "fanduel", "thescore", "northstar", "bet99",
    "tonybet", "betway", "unibet", "pinnacle", "sports interaction", "rivalry", "powerplay",
    "leovegas", "888", "32red", "stake", "pokerstars", "partypoker", "ggpoker",
    "olg", "proline", "mise-o-jeu", "loto-québec", "alc", "wclc", "playnow",
    "great canadian", "gateway", "fallsview", "caesars windsor", "casino rama",
]


def extract_gambling_stats(report_text: str):
    """
    Quick heuristic scan of raw Inverite text lines to count gambling activity.
    Counts a line as gambling if it contains:
      - a known brand, or
      - a gambling keyword, or
      - a category cue like 'entertainment/gambling'.
    Also tries to pull a numeric amount from the line for totals/max.
    """
    if not report_text:
        return {
            "gambling_txn_count_30d": 0,
            "gambling_total_amount_30d": 0.0,
            "gambling_unique_merchants": 0,
            "gambling_max_single_amount": 0.0,
            "gambling_detected": False,
            "gambling_example_lines": [],
        }

    lines = [ln.strip() for ln in report_text.splitlines() if ln.strip()]
    gambling_lines = []
    merchants = Counter()
    amounts = []

    brand_pattern = r"|".join([re.escape(b.lower()) for b in GAMBLING_BRANDS])
    keyword_pattern = r"|".join(GAMBLING_KEYWORDS)
    category_pattern = r"entertainment\s*/\s*gambling|/gambling\b"

    combined = re.compile(rf"({brand_pattern}|{keyword_pattern}|{category_pattern})", re.I)

    amount_rx = re.compile(r"(?<!\d)(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{2})?)")
    merchant_rx = re.compile(
        r"(?:purchase|payment|pos|pos -|debit|credit)\s+(.+?)\s*(?:\d|$)", re.I
    )

    for ln in lines:
        if combined.search(ln):
            gambling_lines.append(ln)
            # merchant guess
            m = merchant_rx.search(ln)
            if m:
                merchants[m.group(1).strip().lower()] += 1
            # amount guess (take last number on line)
            am = re.findall(amount_rx, ln.replace(",", ""))
            if am:
                try:
                    amounts.append(abs(float(am[-1])))
                except Exception:
                    pass

    return {
        "gambling_txn_count_30d": len(gambling_lines),
        "gambling_total_amount_30d": round(sum(amounts), 2) if amounts else 0.0,
        "gambling_unique_merchants": len(merchants),
        "gambling_max_single_amount": max(amounts) if amounts else 0.0,
        "gambling_detected": len(gambling_lines) > 0,
        "gambling_example_lines": gambling_lines[:5],
    }


# ──────────────────────────────────────────────
# Main: AI loan decision analyzer
# ──────────────────────────────────────────────
def analyze_bank_statement(report_text: str, loan_amount: str):
    """
    Analyze Inverite bank report using OpenAI and return a JSON decision.
    Includes:
      - pre-scan for gambling (regex)
      - prompt injection of precomputed stats
      - post-parse guardrails to avoid contradiction
    """

    # 1) Pre-scan with safe defaults
    base_g = {
        "gambling_txn_count_30d": 0,
        "gambling_total_amount_30d": 0.0,
        "gambling_unique_merchants": 0,
        "gambling_max_single_amount": 0.0,
        "gambling_detected": False,
    }
    try:
        detected = extract_gambling_stats(report_text)
        for k in base_g:
            base_g[k] = detected.get(k, base_g[k])
    except Exception:
        pass

    # Normalize for template
    g_detected = str(bool(base_g["gambling_detected"])).lower()  # "true"/"false"
    g_count = int(base_g["gambling_txn_count_30d"])
    g_total = float(base_g["gambling_total_amount_30d"])
    g_merchants = int(base_g["gambling_unique_merchants"])
    g_max = float(base_g["gambling_max_single_amount"])

    tmpl = Template(
        """
You are a **senior loan underwriter** at Xcash Financial Services.

Your task is to review the applicant’s full **Inverite bank report** and determine whether they qualify
for a payday loan of $loan_amount. The report may include:
- Multiple connected bank accounts (e.g., chequing, savings, or credit)
- Income summaries for the past 12 months
- Account statistics such as deposits, withdrawals, NSFs, overdrafts, and balances
- Detailed transaction descriptions

You must evaluate the applicant’s **financial behavior, income stability, repayment capacity, and existing loan exposure.**

---

### Inverite Payday Loan Logic (Important – Follow Exactly)

Only classify payday loan activity when **Inverite explicitly flags it**:

A transaction qualifies as a payday loan transaction if **any** of the following are true:
- Transaction has the flag `is_payday`
- Transaction has the flag `is_loan`
- Transaction category includes: `fees_and_charges/loans/payday`

Do **not** infer payday loans using keywords, merchant names, or guessing — use only Inverite classifications.

When `is_loan` is present, it must be treated **exactly the same as** `is_payday` for counting new loans and deductions.

#### Loan Transaction Direction Rules
Use transaction polarity to classify:
- **CREDIT** (`e-Transfer received`, `deposit`, `Autodeposit`) → **New loan received**
- **DEBIT** (`PAD`, `pre-authorized debit`, `payment`, `withdrawal`, `e-Transfer sent`) → **Loan deduction (repayment)**
- Ignore the **balance column** – it does not determine transaction direction.

#### Required Loan Counts (last 30 days)
You must calculate and mention all of these:
- `new_loans_received_count` – Number of **CREDIT** payday transactions
- `loan_deductions_count` – Number of **DEBIT** payday transactions
- `distinct_lenders_count` – Unique payday lenders involved
- `existing_loans_count` – Active payday loans (recurring deductions to the same lender = 1 active loan)

---

### Underwriting Guidelines

💰 **Income & Employment Stability**
- Detect payroll using Inverite’s `is_payroll` flag (do **not** guess).
- Consider if income is predictable (biweekly, monthly, semimonthly).
- Strong stability = regular income for at least 2 months.

💸 **Expenses, Cash Flow & Loan Deductions**
- Compare deposits vs. withdrawals — does the account recover after payday?
- If multiple loan deductions are present (≥2) or large portions of payroll go to lenders → high risk.
- If repayments stopped but NSF/overdraft rose → financial distress.

🚨 **Financial Risk Factors**
- NSF/overdraft frequency and minimum balances:
  - 0 NSF & 0 overdrafts = low risk
  - 1–2 = moderate
  - Repeated = high risk

🏦 **Multiple Accounts**
- Treat all accounts as one picture.

---

### ✅ Precomputed Signals (must be trusted)
The following values were computed outside the model by deterministic regex rules.
**You must use these exact values in your decision and rationale** and not contradict them.

PRECOMPUTED_GAMBLING = {
  "gambling_detected": $g_detected,
  "gambling_txn_count_30d": $g_count,
  "gambling_total_amount_30d": $g_total,
  "gambling_unique_merchants": $g_merchants,
  "gambling_max_single_amount": $g_max
}

If PRECOMPUTED_GAMBLING.gambling_detected is true, state that gambling is present and include the counts above.
If gambling_txn_count_30d ≥ 3, or there are 2+ gambling days, or gambling_max_single_amount ≥ 150, you must decline.

---

### 💸 E-Transfer Frequency
- Count both sent and received e-Transfers in the last 30 days.
- 0–10 normal, 11–25 moderate concern, >25 high concern and a negative factor.

---

📈 **Affordability & Decision Logic**
- Approve full amount only with steady income, minimal NSF/overdrafts, ≤1 active payday loan, and healthy balances.
- Approve lower amount if borderline.
- Decline if 3+ active loans, frequent overdrafts/NSFs, or chronically low/negative balances.
- Decline if heavy gambling (see rules above).
- Decline if transactions tagged `is_bankruptcy_trustee`.

---

### Output Format (return only JSON)
{
  "decision": "Approved" | "Approved for Lower Amount" | "Declined",
  "approved_amount": <numeric dollar amount or null>,
  "rationale": "2–5 sentences covering income stability, loan deductions/new loans/active loans/distinct lenders, NSF/overdrafts, gambling (with counts if present), and the reason for the decision."
}

--- BANK REPORT TEXT (truncated for processing) ---
$report_text
"""
    )

    # 2) Build prompt (include all g_* fields!)
    prompt = tmpl.safe_substitute(
        loan_amount=str(loan_amount),
        report_text=str(report_text)[:12000],
        g_detected=g_detected,
        g_count=g_count,
        g_total=g_total,
        g_merchants=g_merchants,
        g_max=g_max,
    )

    # 3) Call the model
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior underwriting officer at Xcash Financial Services. "
                        "Return only valid JSON, without markdown or commentary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.15,
        )
        content = response.choices[0].message.content.strip()
        result = safe_parse_json(content)

        # Safety: if something weird slipped through, normalize to dict
        if not isinstance(result, dict):
            result = {
                "decision": "Error",
                "approved_amount": None,
                "rationale": str(result),
            }

        # 4) Post-parse guardrail: cannot contradict precomputed gambling
        if base_g["gambling_detected"]:
            thresh_decline = (g_count >= 3) or (g_max >= 150)
            add = (
                f" Gambling detected: {g_count} txn(s), total ${g_total}, "
                f"max single ${g_max}."
            )
            result["rationale"] = (result.get("rationale", "").rstrip() + add).strip()
            if thresh_decline:
                result["decision"] = "Declined"
                result["approved_amount"] = None

        # Ensure required keys exist
        result.setdefault("decision", "Error")
        result.setdefault("approved_amount", None)
        result.setdefault("rationale", "No rationale returned.")

        return result

    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        return {
            "decision": "Error",
            "approved_amount": None,
            "rationale": str(e),
        }
