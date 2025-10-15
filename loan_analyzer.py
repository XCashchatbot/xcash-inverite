# === loan_analyzer.py ===
import os
import json
import re
import openai
from dotenv import load_dotenv

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
def analyze_bank_statement(report_text: str, loan_amount: str):
    """
    Analyze Inverite bank report using OpenAI and return a JSON decision.
    """

    prompt = f"""
    You are a **senior loan underwriter** at Xcash Financial Services.

    Your task is to review the applicant’s full **Inverite bank report** and determine whether they qualify
    for a payday loan of ${loan_amount}. The report may include:
    - Multiple connected bank accounts (e.g., chequing, savings, or credit)
    - Income summaries for the past 12 months
    - Account statistics such as deposits, withdrawals, NSFs, overdrafts, and balances
    - Detailed transaction descriptions

    You must evaluate the applicant’s **financial behavior, income stability, and repayment capacity**.

    ---

    **Follow these decision guidelines carefully:**

    💰 **Income & Employment Stability**
    - Look for consistent employment income (recurring payroll deposits of similar amounts).
    - Consider whether income arrives on a predictable schedule (e.g., biweekly, monthly).
    - If income is stable and above average expenses, mark as strong income stability.
    - If one or more accounts receive regular payroll, count them collectively.

    💸 **Expenses & Cash Flow**
    - Compare average deposits vs. withdrawals — do balances recover after payday?
    - Check if expenses repeatedly exceed income or if the account remains near zero.
    - Identify recurring obligations such as rent, loan payments, or subscriptions.

    🚨 **Financial Risk Factors**
    - Use NSF (Non-Sufficient Funds) and overdraft counts as high-risk indicators.
    - If the report explicitly shows “0 NSF” or “no overdrafts,” treat that as **low risk**.
    - If overdrafts exist but are infrequent and small, consider them **moderate risk**.
    - Pay attention to the lowest balance values — repeated negatives indicate instability.

    🏦 **Multiple Accounts**
    - Treat multiple accounts as part of one financial profile.
    - If one account is mainly for payroll and another for bills or withdrawals, combine them in your analysis.
    - Consider the total combined balances and total income across accounts.

    📈 **Affordability & Loan Decision**
    - Determine if the applicant can comfortably manage the requested amount.
    - If income is solid but the balance is tight or slightly unstable, you may **approve a smaller amount**.
    - Approve the full request only when cash flow and income clearly support repayment.
    - Decline if income is too inconsistent or balances are chronically negative.
      
      Additionally, the report may contain:
     - A **Pay Schedule** section that lists frequency, next pay date, and total monthly income. Treat this as highly reliable evidence of stable employment.
      - A **Transaction Flags Summary**, showing counts of payrolls, payday loans, overdrafts, or returned payments. Use these to assess reliability and risk behavior.

    ---

    **Return your evaluation strictly as valid JSON (no code blocks, no text outside JSON):**

    {{
      "decision": "Approved" | "Approved for Lower Amount" | "Declined",
      "approved_amount": <numeric dollar amount or null>,
      "rationale": "A concise explanation (2–4 sentences) summarizing income stability, overdraft/NSF history, average balances, and the reason for the approval or decline."
    }}

    ---

    **Important logic rules:**
    - Do **not invent numbers**; rely only on explicit values from the bank report.
    - If NSF = 0 and Overdraft = 0, clearly state that as a positive factor.
    - Consider deposits labeled as “Payroll,” “Salary,” or “Employment” as verified income.
    - Prioritize fairness — approve when data supports reasonable repayment confidence.

    --- BANK REPORT TEXT (truncated for processing) ---
    {report_text[:12000]}
    """

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
        temperature=0.15,  # lower for more consistent judgments
    )

    message = response.choices[0].message["content"].strip()
    parsed = safe_parse_json(message)
    return parsed
