# === loan_analyzer.py ===
import os
import json
import re
import openai
from dotenv import load_dotenv
from string import Template

# Load .env and set API key
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")


# ──────────────────────────────────────────────
# Helper: Safe JSON parsing for AI output
# ──────────────────────────────────────────────
def safe_parse_json(message: str):
    """
    Cleans and parses AI JSON output, even if wrapped in code fences or text.
    """
    # Remove code fences like ```json ... ```
    cleaned = re.sub(r"^```json|```$", "", message.strip(), flags=re.IGNORECASE).strip()

    # Remove stray backticks or whitespace
    cleaned = cleaned.replace("```", "").strip()

    # Extract JSON content if embedded in other text
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        cleaned = match.group(0)

    # Try to parse
    try:
        return json.loads(cleaned)
    except Exception:
        return {"decision": "Error", "rationale": message}


# ──────────────────────────────────────────────
# Main: AI loan decision analyzer
# ──────────────────────────────────────────────
from string import Template
import openai

def analyze_bank_statement(report_text: str, loan_amount: str):
    """
    Analyze Inverite bank report using OpenAI and return a JSON decision.
    """

    tmpl = Template("""
You are a **senior loan underwriter** at Xcash Financial Services.

Your task is to review the applicant’s full **Inverite bank report** and determine whether they qualify
for a payday loan of $loan_amount. The report may include:
- Multiple connected bank accounts (e.g., chequing, savings, or credit)
- Income summaries for the past 12 months
- Account statistics such as deposits, withdrawals, NSFs, overdrafts, and balances
- Detailed transaction descriptions

You must evaluate the applicant’s **financial behavior, income stability, repayment capacity, and existing loan exposure.**

---

### ✅ Inverite Payday Loan Logic (Important – Follow Exactly)

Only classify payday loan activity when **Inverite explicitly flags it**:
- Transaction has the flag `is_payday`, **or**
- Category includes: `fees_and_charges/loans/payday`

Do **not** infer payday loans using keywords or guessing — use only Inverite classifications.

#### Loan Transaction Direction Rules
Use transaction polarity to classify:
- **CREDIT** (`e-Transfer received`, `deposit`, `Autodeposit`) → **New loan received**
- **DEBIT** (`PAD`, `pre-authorized debit`, `payment`, `withdrawal`, `e-Transfer sent`) → **Loan deduction (repayment)**
- Ignore the **balance column** – it does not determine transaction direction.

#### Required Loan Counts (last 30 days)
You must calculate and mention all of these:
- `new_loans_received_count` – Number of **CREDIT** payday transactions (money in from payday lenders)
- `loan_deductions_count` – Number of **DEBIT** payday transactions (money out to payday lenders)
- `distinct_lenders_count` – Number of **unique payday lenders** involved
- `existing_loans_count` – Number of **active payday loans** (recurring deductions to the same payday lender = 1 active loan)

#### Payday Classification Rules
- Inverite payday + CREDIT → Count as **new payday loan received**
- Inverite payday + DEBIT → Count as **loan deduction/repayment**
- If same lender appears in **2 or more payday deductions** → count as **1 active payday loan**
- Count **distinct lender names** from both credits + deductions


---

### Underwriting Guidelines

💰 **Income & Employment Stability**
- Identify consistent employment income (recurring payroll deposits with similar amounts or same employer in "details").
- Consider if income arrives predictably (e.g., biweekly, monthly, semimonthly).
- Strong stability = regular income with minimal fluctuation and no interruptions for at least 2 months.
- Combine income across all active accounts.

💸 **Expenses, Cash Flow & Loan Deductions**
- Compare total deposits vs. withdrawals — does the account recover after payday?
- Detect recurring deductions related to **existing loans, payday lenders, or finance companies**. (Use Inverite payday flags only.)
- If multiple loan deductions are present (e.g., 2 or more), or if large portions of payroll are going to lenders, treat as **high-risk**.
- If loan repayments have recently stopped but NSF or overdraft activity increased, assume **financial distress**.

🚨 **Financial Risk Factors**
- Evaluate NSF (Non-Sufficient Funds), overdraft frequency, and minimum balances.
- 0 NSF and no overdrafts = **low risk**.
- 1–2 NSFs or occasional overdrafts = **moderate risk**.
- Repeated NSF or negative balances = **high risk**.

🏦 **Multiple Accounts**
- Treat all accounts as one financial picture.

📈 **Affordability & Decision Logic**
- Approve full amount only when income is steady, NSF/overdrafts are minimal, loan deductions are limited (0–1 active loans), and balances trend net positive.
- Approve a lower amount if income is stable but there are 2 loan deductions or borderline affordability.
- Decline if 3+ active loans or high recurring lender deductions, frequent overdrafts/NSFs, or chronically low/negative balances.

---

### Output Format
Return only valid JSON (no text or markdown outside JSON):

{
  "decision": "Approved" | "Approved for Lower Amount" | "Declined",
  "approved_amount": <numeric dollar amount or null>,
  "rationale": "A concise explanation (2–5 sentences) summarizing income stability, loan deductions found, new loans received, active loans, distinct lenders, NSF/overdraft history, and the reason for the approval or decline."
}

---

### Important Logic Rules
- Never invent numbers; rely only on explicit information in the report.
- Clearly mention if **no loan deductions were found**, or how many were found.
- If NSF = 0 and no loan deductions, emphasize as a positive factor.
- If multiple loans or repeated deductions found, clearly state that as the main reason for decline or reduced approval.
- Prioritize fair, responsible lending decisions that minimize default risk.

--- BANK REPORT TEXT (truncated for processing) ---
$report_text
""")

    prompt = tmpl.substitute(
        loan_amount=str(loan_amount),
        report_text=str(report_text)[:12000]
    )

    response = openai.ChatCompletion.create(
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

    message = response.choices[0].message["content"].strip()
    parsed = safe_parse_json(message)
    return parsed
