# report_features.py
import datetime as dt
from typing import Dict, Any, List

TRUSTEE_FLAG = "is_bankruptcy_trustee"

def _parse_date(d: str) -> dt.date:
    try:
        return dt.datetime.strptime(d[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def extract_critical_signals(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic, non-LLM extractor.
    Reads ALL transactions + account statistics and returns compact signals
    for the underwriting LLM.
    """
    today = dt.date.today()
    days30 = today - dt.timedelta(days=30)
    days60 = today - dt.timedelta(days=60)
    days90 = today - dt.timedelta(days=90)

    features: Dict[str, Any] = {
        "summary_window_days": 30,
        "accounts_count": 0,

        "income": {
            "payroll_30_count": 0,
            "payroll_30_total": 0.0,
            "gov_60_count": 0,
            "gov_60_total": 0.0,
            "primary_income_is_gov": False,
        },

        "payday_loans": {
            "loans_30_count": 0,
            "loans_30_credits": 0.0,
            "loans_30_debits": 0.0,
            "payday_30_count": 0,
            "distinct_lenders_30": 0,
            "active_loans_estimate": 0,
        },

        "gambling": {
            "txn_count_30": 0,
            "total_30": 0.0,
            "max_single_30": 0.0,
            "days_with_gambling": 0,
        },

        "etransfers": {
            "sent_30_count": 0,
            "sent_30_total": 0.0,
            "received_30_count": 0,
            "received_30_total": 0.0,
        },

        "proposal_trustee": {
            "active": False,
            "payments_90_count": 0,
            "payments_90_total": 0.0,
            "nsf_90_count": 0,
            "last_payment_date": None,
        },

        "nsf_overdraft": {
            "nsf_90_count": 0,
            "overdraft_90_count": 0,
            "overdraft_30_count": 0,
        },

        "cashflow": {
            "credits_30_total": 0.0,
            "debits_30_total": 0.0,
            "net_30": 0.0,
        },
    }

    accounts: List[dict] = report.get("accounts") or report.get("bank_accounts") or []
    features["accounts_count"] = len(accounts)

    # ---- 1) Account-level statistics (fast, reliable) ----
    for acc in accounts:
        stats = acc.get("statistics") or {}
        if not isinstance(stats, dict):
            continue

        # Cashflow (30d)
        features["cashflow"]["credits_30_total"] += float(stats.get("credits_30_total", 0) or 0)
        features["cashflow"]["debits_30_total"] += float(stats.get("debits_30_total", 0) or 0)

        # Loans / payday (30d)
        features["payday_loans"]["loans_30_count"] += int(stats.get("loans_30_count", 0) or 0)
        features["payday_loans"]["loans_30_credits"] += float(stats.get("loans_30_credits", 0) or 0)
        features["payday_loans"]["loans_30_debits"] += float(stats.get("loans_30_debits", 0) or 0)

        features["payday_loans"]["payday_30_count"] += int(stats.get("payday_30_count", 0) or 0)

        # Overdraft & NSF (90d)
        features["nsf_overdraft"]["overdraft_90_count"] += int(stats.get("overdraft_90_count", 0) or 0)
        features["nsf_overdraft"]["overdraft_30_count"] += int(stats.get("overdraft_30_count", 0) or 0)

        # Some Inverite reports put NSF counts in quarter blocks; you can extend here if needed.

    # Compute net
    cf = features["cashflow"]
    cf["net_30"] = cf["credits_30_total"] - cf["debits_30_total"]

    # ---- 2) Transaction-level scan ----
    txs: List[dict] = report.get("transactions") or []
    lenders = set()
    gambling_days = set()

    GOV_INCOME_KEYWORDS = [
        "CANADA CHILD", "CHILD BENEFIT", "CHILD TAX", "CCTB", "CCB",
        "ODSP", "DISABILITY", "CPP", "OAS", "GST/HST", "GST CREDIT"
    ]

    for tx in txs:
        if not isinstance(tx, dict):
            continue

        date_str = tx.get("date")
        d = _parse_date(date_str) if date_str else None
        if not d:
            continue

        details = (tx.get("details") or tx.get("description") or "").upper()
        category = (tx.get("category") or "").lower()
        flags = tx.get("flags") or []

        credit = float(tx.get("credit") or 0 or 0.0)
        debit = float(tx.get("debit") or 0 or 0.0)

        # 30-day window check
        in_30 = d >= days30
        in_60 = d >= days60
        in_90 = d >= days90

        # --- Payroll ---
        if in_30 and "is_payroll" in flags:
            features["income"]["payroll_30_count"] += 1
            features["income"]["payroll_30_total"] += credit

        # --- Government income (ODSP, Child Tax, CPP, etc.) ---
        if in_60 and credit > 0 and any(k in details for k in GOV_INCOME_KEYWORDS):
            features["income"]["gov_60_count"] += 1
            features["income"]["gov_60_total"] += credit

        # --- E-transfers ---
        if in_30 and "INTERAC ETRNSFR" in details:
            if credit > 0:
                features["etransfers"]["received_30_count"] += 1
                features["etransfers"]["received_30_total"] += credit
            if debit > 0:
                features["etransfers"]["sent_30_count"] += 1
                features["etransfers"]["sent_30_total"] += debit

        # --- Payday / high-cost loans by category/flags ---
        if in_30 and ("loans/payday" in category or "loans/high_cost" in category or "is_loan" in flags or "is_payday" in flags):
            # new loan vs repayment by direction
            if credit > 0:
                # new loan
                lenders.add(details)
                features["payday_loans"]["loans_30_credits"] += credit
            if debit > 0:
                # deduction
                lenders.add(details)
                features["payday_loans"]["loans_30_debits"] += debit

        # --- Gambling by category ---
        if in_30 and "entertainment/gambling" in category:
            features["gambling"]["txn_count_30"] += 1
            features["gambling"]["total_30"] += debit
            features["gambling"]["max_single_30"] = max(
                features["gambling"]["max_single_30"], debit
            )
            gambling_days.add(d)

        # --- Proposal / Trustee (bankruptcy_trustee flag) ---
        if in_90 and TRUSTEE_FLAG in flags:
            features["proposal_trustee"]["active"] = True
            if debit > 0:
                features["proposal_trustee"]["payments_90_count"] += 1
                features["proposal_trustee"]["payments_90_total"] += debit
                last = features["proposal_trustee"]["last_payment_date"]
                if not last or d > _parse_date(last):
                    features["proposal_trustee"]["last_payment_date"] = d.isoformat()

            # very rough NSF detection on trustee tx
            if "NSF" in details or "RETURN" in details:
                features["proposal_trustee"]["nsf_90_count"] += 1

    # finalize distinct lenders & gambling days
    features["payday_loans"]["distinct_lenders_30"] = len(lenders)
    features["gambling"]["days_with_gambling"] = len(gambling_days)

    # Primary income is gov? (threshold 70% of payroll+gov)
    inc = features["income"]
    total_income_60 = inc["gov_60_total"] + inc["payroll_30_total"]
    if total_income_60 > 0 and inc["gov_60_total"] / total_income_60 >= 0.7:
        inc["primary_income_is_gov"] = True

    # Simple estimate of active loans: distinct lenders if any deductions in last 30 days
    if features["payday_loans"]["loans_30_debits"] > 0:
        features["payday_loans"]["active_loans_estimate"] = max(1, len(lenders))

    return features
