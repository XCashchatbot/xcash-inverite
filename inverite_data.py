# === inverite_data.py ===
import os
import requests
import json
import time
from dotenv import load_dotenv

# ───────────────────────────────
# Setup
# ───────────────────────────────
load_dotenv()
INVERITE_API_KEY = os.getenv("INVERITE_API_KEY")
BASE_URL = "https://www.inverite.com/api/v2"

if not INVERITE_API_KEY:
    raise EnvironmentError("❌ INVERITE_API_KEY not found in environment variables.")


# ───────────────────────────────
# 1️⃣ Fetch full report by GUID
# ───────────────────────────────
def fetch_report(guid: str, retries: int = 5, delay: int = 5):
    """
    Fetch the full Inverite report (including pay schedule, flags, statistics).
    Retries automatically if Inverite has not finished processing the report yet.
    """
    url = f"{BASE_URL}/fetch/{guid}"
    headers = {"Auth": INVERITE_API_KEY}

    print(f"🔍 Fetching Inverite report: {url}")

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"🔁 Attempt {attempt}/{retries} - Status: {response.status_code}")

            # Inverite sometimes returns 202 Accepted or empty data while report finalizes
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data and "accounts" in data:
                        print(f"✅ Inverite report ready ({len(str(data))} chars).")
                        return data
                except Exception:
                    print("⚠️ JSON parsing failed, retrying...")
            else:
                print(f"⚠️ Non-200 response ({response.status_code}), retrying...")

        except requests.RequestException as e:
            print(f"⚠️ Network error during fetch attempt {attempt}: {e}")

        # Wait before retrying
        time.sleep(delay)

    raise RuntimeError(f"❌ Inverite report not ready after {retries} attempts for GUID {guid}.")


# ───────────────────────────────
# 2️⃣ Convert Inverite JSON → text
# ───────────────────────────────
def convert_to_text(report_json: dict) -> str:
    sections = []

    applicant = report_json.get("applicant", {})
    accounts = report_json.get("accounts", [])
    summary = report_json.get("summary", {})
    transactions = report_json.get("transactions", [])
    report_date = report_json.get("created_at", "N/A")

    # Header Section
    sections.append("🏦 BANK REPORT SUMMARY")
    sections.append(f"Name: {applicant.get('first_name', '')} {applicant.get('last_name', '')}")
    sections.append(f"Email: {applicant.get('email', 'N/A')}")
    sections.append(f"Report Date: {report_date}")
    sections.append("-" * 40)

    # Connected Accounts Section
    sections.append("📂 CONNECTED ACCOUNTS:")
    if isinstance(accounts, list) and accounts:
        for acc in accounts:
            if isinstance(acc, dict):
                sections.append(
                    f"- Bank: {acc.get('institution', 'N/A')} | Type: {acc.get('type', 'N/A')} | "
                    f"Transit: {acc.get('transit', '')} | Account: {acc.get('account', '')} | "
                    f"Balance: ${acc.get('current_balance', 'N/A')}"
                )
    else:
        sections.append("No account information available.")
    sections.append("-" * 40)

    # Summary Metrics
    if isinstance(summary, dict) and summary:
        sections.append("📊 OVERALL ACCOUNT METRICS:")
        for key, val in summary.items():
            if isinstance(val, (int, float, str)):
                sections.append(f"- {key.replace('_', ' ').title()}: {val}")
    else:
        sections.append("No summary data available.")
    sections.append("-" * 40)

    # Account-Specific Details
    if isinstance(accounts, list) and accounts:
        for acc in accounts:
            acc_id = acc.get("account", "N/A")
            acc_type = acc.get("type", "N/A")
            sections.append(f"📈 Account Statistics for {acc_id} ({acc_type})")

            stats = acc.get("statistics", {})
            if isinstance(stats, dict) and stats:
                for key, value in stats.items():
                    if isinstance(value, (int, float, str)):
                        sections.append(f"  - {key.replace('_', ' ').title()}: {value}")
                    else:
                        sections.append(f"  - {key}: [Invalid data]")
            else:
                sections.append("  No statistics available.")
            sections.append("-" * 40)

            pay_schedule = acc.get("pay_schedule", [])
            if pay_schedule:
                sections.append("💵 PAY SCHEDULE:")
                for ps in pay_schedule:
                    freq = ps.get("frequency", "N/A")
                    income_type = ps.get("income_type", "N/A")
                    monthly_income = ps.get("monthly_income", "N/A")
                    details = ps.get("details", "N/A")
                    future = ", ".join(ps.get("future_payments", [])) if ps.get("future_payments") else "N/A"
                    sections.append(
                        f"  - Frequency: {freq}, Income Type: {income_type}, "
                        f"Monthly Income: ${monthly_income}, Employer: {details}, "
                        f"Next Payments: {future}"
                    )
            else:
                sections.append("No pay schedule detected.")
            sections.append("-" * 40)

            flags = acc.get("flags_summary", {})
            if flags:
                sections.append("🚩 TRANSACTION FLAGS SUMMARY:")
                for flag, count in flags.items():
                    sections.append(f"  - {flag.replace('_', ' ').title()}: {count}")
            else:
                sections.append("No transaction flags summary available.")
            sections.append("-" * 40)

    else:
        sections.append("No accounts available.")
    sections.append("-" * 40)

    # Recent Transactions
    sections.append("📜 RECENT TRANSACTIONS (first 50):")
    if isinstance(transactions, list) and transactions:
        for tx in transactions[:50]:
            if isinstance(tx, dict):
                date = tx.get("date", "N/A")
                desc = tx.get("description", "")
                amt = tx.get("amount", 0)
                ttype = tx.get("type", "")
                try:
                    formatted_amt = f"${float(amt):,.2f}"
                except (ValueError, TypeError):
                    formatted_amt = f"${amt}"
                sections.append(f"{date}: {desc} | {ttype} | {formatted_amt}")
            else:
                sections.append("Invalid transaction format.")
    else:
        sections.append("No transactions available.")

    return "\n".join(sections)
