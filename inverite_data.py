# === inverite_data.py ===
import os
import requests
import json
import time
from typing import Any, Dict, List, Tuple, Optional
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
# Helpers (robust parsing)
# ───────────────────────────────
def _get(d: dict, path: List[str], default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

def _norm(s: Optional[str]) -> str:
    return " ".join(str(s or "").strip().split())

def _first_nonempty(*vals):
    for v in vals:
        if isinstance(v, str) and _norm(v):
            return _norm(v)
    return None

def _get_accounts(report: dict) -> List[dict]:
    # Inverite sometimes uses "accounts", some exports may use "bank_accounts"
    accs = report.get("accounts")
    if isinstance(accs, list):
        return [a for a in accs if isinstance(a, dict)]
    accs = report.get("bank_accounts")
    if isinstance(accs, list):
        return [a for a in accs if isinstance(a, dict)]
    return []

def _extract_applicant_info(report: dict) -> Tuple[str, str, str, str]:
    """
    Return (display_name, first_name, last_name, email) with robust fallbacks.
    """
    # 1) identity.full_name or identity.name
    identity = report.get("identity") or {}
    id_full = _first_nonempty(identity.get("full_name"), identity.get("name"))

    # 2) top-level name (portal shows this)
    top_name = _first_nonempty(report.get("name"))

    # 3) any account holder fields
    accs = _get_accounts(report)
    holder_from_acc = None
    for acc in accs:
        holder_from_acc = _first_nonempty(
            acc.get("holder_name"),
            acc.get("account_holder"),
            acc.get("name"),
        )
        if holder_from_acc:
            break

    # 4) request name & email
    req = report.get("request") or {}
    req_first = _norm(req.get("first_name") or "")
    req_last = _norm(req.get("last_name") or "")
    req_email = _norm(req.get("email") or "")

    # 5) applicant block (your current schema)
    applicant = report.get("applicant") or {}
    app_first = _norm(applicant.get("first_name") or "")
    app_last  = _norm(applicant.get("last_name") or "")
    app_email = _norm(applicant.get("email") or "")

    # pick best email
    email = _first_nonempty(app_email, req_email) or ""

    # choose display name by priority
    display_name = (
        id_full
        or top_name
        or holder_from_acc
        or _first_nonempty(f"{req_first} {req_last}".strip())
        or _first_nonempty(f"{app_first} {app_last}".strip())
        or email
        or "Unknown"
    )

    # also pick best first/last
    first_name = _first_nonempty(req_first, app_first, (display_name.split(" ")[0] if display_name and display_name != "Unknown" else ""))
    last_name  = _first_nonempty(req_last, app_last, (" ".join(display_name.split(" ")[1:]) if display_name and " " in display_name else ""))

    return display_name or "", first_name or "", last_name or "", email or ""

def _report_is_ready(data: dict) -> bool:
    """
    Inverite may return early while processing. Consider 'ready' when we have
    *either* accounts, identity, request meta, or transactions.
    """
    if not isinstance(data, dict):
        return False
    if data.get("status") in ("processing", "pending"):
        return False
    if any(k in data for k in ("accounts", "bank_accounts", "identity", "request", "transactions", "summary", "applicant", "name")):
        return True
    return False

# ───────────────────────────────
# 1️⃣ Fetch full report by GUID
# ───────────────────────────────
def fetch_report(guid: str, retries: int = 8, delay: int = 5) -> dict:
    """
    Fetch the full Inverite report (including identity, pay schedule, flags, statistics).
    Retries automatically while the report is being prepared.
    """
    url = f"{BASE_URL}/fetch/{guid}"
    headers = {"Auth": INVERITE_API_KEY}

    print(f"🔍 Fetching Inverite report: {url}")

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            print(f"🔁 Attempt {attempt}/{retries} - HTTP {response.status_code}")

            if response.status_code in (200, 202):
                # 202 means still processing; 200 may still be partial
                try:
                    data = response.json()
                except Exception as e:
                    print(f"⚠️ JSON parse failed: {e}; retrying…")
                    time.sleep(delay)
                    continue

                if response.status_code == 200 and _report_is_ready(data):
                    print(f"✅ Inverite report ready (~{len(json.dumps(data))} chars).")
                    return data
                else:
                    # still processing or incomplete
                    print("⏳ Report not ready yet; waiting…")
            else:
                print(f"⚠️ Non-OK response ({response.status_code}); retrying…")

        except requests.RequestException as e:
            print(f"⚠️ Network error during fetch attempt {attempt}: {e}")

        time.sleep(delay)

    raise RuntimeError(f"❌ Inverite report not ready after {retries} attempts for GUID {guid}.")

# ───────────────────────────────
# 2️⃣ Convert Inverite JSON → text
# ───────────────────────────────
def convert_to_text(report_json: dict) -> str:
    sections: List[str] = []

    # Robust applicant info
    display_name, first_name, last_name, email = _extract_applicant_info(report_json)

    # Basic fields
    report_date = report_json.get("created_at") or report_json.get("created") or report_json.get("timestamp") or "N/A"
    accounts = _get_accounts(report_json)
    summary = report_json.get("summary") or {}
    transactions = report_json.get("transactions") or []

    # Header Section
    sections.append("🏦 BANK REPORT SUMMARY")
    sections.append(f"Name: {display_name}".strip())
    # keep first/last visible only if you want both representations
    if (first_name or last_name) and f"{first_name} {last_name}".strip() != display_name:
        sections.append(f"Name (parsed): {first_name} {last_name}".strip())
    sections.append(f"Email: {email or 'N/A'}")
    sections.append(f"Report Date: {report_date}")
    sections.append("-" * 40)

    # Connected Accounts Section
    sections.append("📂 CONNECTED ACCOUNTS:")
    if accounts:
        for acc in accounts:
            inst   = acc.get("institution") or acc.get("bank_name") or "N/A"
            a_type = acc.get("type") or acc.get("account_type") or "N/A"
            transit = acc.get("transit") or acc.get("transit_number") or ""
            accno  = acc.get("account") or acc.get("account_number") or ""
            bal    = acc.get("current_balance") or acc.get("balance") or "N/A"
            sections.append(
                f"- Bank: {inst} | Type: {a_type} | Transit: {transit} | "
                f"Account: {accno} | Balance: ${bal}"
            )
    else:
        sections.append("No account information available.")
    sections.append("-" * 40)

    # Summary Metrics
    if isinstance(summary, dict) and summary:
        sections.append("📊 OVERALL ACCOUNT METRICS:")
        for key, val in summary.items():
            if isinstance(val, (int, float, str)):
                label = key.replace("_", " ").title()
                sections.append(f"- {label}: {val}")
    else:
        sections.append("No summary data available.")
    sections.append("-" * 40)

    # Account-Specific Details
    if accounts:
        for acc in accounts:
            acc_id = acc.get("account") or acc.get("account_number") or "N/A"
            acc_type = acc.get("type") or acc.get("account_type") or "N/A"
            sections.append(f"📈 Account Statistics for {acc_id} ({acc_type})")

            stats = acc.get("statistics") or {}
            if isinstance(stats, dict) and stats:
                for key, value in stats.items():
                    if isinstance(value, (int, float, str)):
                        sections.append(f"  - {key.replace('_', ' ').title()}: {value}")
                    else:
                        sections.append(f"  - {key}: [Invalid data]")
            else:
                sections.append("  No statistics available.")
            sections.append("-" * 40)

            pay_schedule = acc.get("pay_schedule") or []
            if isinstance(pay_schedule, list) and pay_schedule:
                sections.append("💵 PAY SCHEDULE:")
                for ps in pay_schedule:
                    if not isinstance(ps, dict):
                        continue
                    freq = ps.get("frequency", "N/A")
                    income_type = ps.get("income_type", "N/A")
                    monthly_income = ps.get("monthly_income", "N/A")
                    details = ps.get("details", "N/A")
                    future = ", ".join(ps.get("future_payments", [])) if isinstance(ps.get("future_payments"), list) else "N/A"
                    sections.append(
                        f"  - Frequency: {freq}, Income Type: {income_type}, "
                        f"Monthly Income: ${monthly_income}, Employer: {details}, "
                        f"Next Payments: {future}"
                    )
            else:
                sections.append("No pay schedule detected.")
            sections.append("-" * 40)

            flags = acc.get("flags_summary") or {}
            if isinstance(flags, dict) and flags:
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
            if not isinstance(tx, dict):
                sections.append("Invalid transaction format.")
                continue
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
        sections.append("No transactions available.")

    return "\n".join(sections)
