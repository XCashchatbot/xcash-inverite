from __future__ import annotations
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from collections import Counter

# -------------------------
# Helpers
# -------------------------
def _to_float(s: Any) -> float:
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(",", "")
    return float(s) if s else 0.0

def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)  # naive
        except Exception:
            continue
    return None

def _direction(credit: float, debit: float, details: str) -> str:
    if credit > 0 and debit == 0:
        return "credit"
    if debit > 0 and credit == 0:
        return "debit"
    dl = (details or "").lower()
    if any(k in dl for k in ["deposit", "autodeposit", "credit", "received", "refund"]):
        return "credit"
    return "debit"

def _canonical_counterparty(details: str) -> str:
    """More stable merchant/lender key from free-text details."""
    d = (details or "").lower()
    d = re.sub(r"\s+", " ", d)
    # strip common boilerplate/tokens
    for t in [
        "branch transaction", "pre-authorized", "preauthorized", "preauth", "p.a.d",
        "pad", "transaction", "eft", "debit", "credit", "pos", "visa", "mc"
    ]:
        d = d.replace(t, "")
    d = re.sub(r"[^a-z0-9\s\.\-&]", " ", d)         # keep simple chars
    d = re.sub(r"\b(\d{2,})\b", " ", d)             # drop long numbers
    d = re.sub(r"\s+", " ", d).strip()
    return d

def _is_gambling(category: str, details: str) -> bool:
    if category and "gambling" in category.lower():
        return True
    brands = [
        "draftkings","bet365","betmgm","fanduel","rivalry","thescore","northstar",
        "olg","proline","playnow","ggpoker","pokerstars","betway","unibet","leovegas",
        "casino","sports interaction","tonybet","bet99","powerplay","pinnacle","888"
    ]
    dl = (details or "").lower()
    if any(b in dl for b in brands):
        return True
    return bool(re.search(r"\b(casino|sportsbook|bet(ting)?)\b", dl))

def _is_etransfer(details: str) -> bool:
    dl = (details or "").lower()
    return bool(re.search(r"\b(interac|e-?transfer|etransfer|e\.t\.)\b", dl) or "autodeposit" in dl)

def _is_payroll(flags: List[str], details: str) -> bool:
    fl = [f.lower() for f in (flags or [])]
    if "is_payroll" in fl:
        return True
    return bool(re.search(r"\b(payroll|pay ?cheque|paycheck|salary)\b", (details or "").lower()))

def _is_trustee(flags: List[str], category: str, details: str) -> bool:
    f = " ".join(flags or []).lower()
    c = (category or "").lower()
    d = (details or "").lower()
    return ("is_bankruptcy_trustee" in f) or ("bankruptcy" in c) or ("trustee" in d)

def _is_failed(flags: List[str], details: str) -> bool:
    f = " ".join(flags or []).lower()
    d = (details or "").lower()
    if "is_return" in f:
        return True
    return bool(re.search(r"\b(return|reversal|nsf|insufficient|rejected|declined)\b", d))

def _is_payday(flags: List[str], category: str) -> bool:
    f = " ".join(flags or []).lower()
    c = (category or "").lower()
    if "is_payday" in f or "is_loan" in f:
        return True
    return c.startswith("fees_and_charges/loans/payday")

def _payroll_from_payschedules(acc: Dict[str, Any], start_dt: datetime, end_dt: datetime) -> bool:
    for ps in acc.get("payschedules") or []:
        for p in ps.get("payments") or []:
            dt = _parse_dt(p.get("date",""))
            if dt and start_dt <= dt <= end_dt:
                flags = p.get("flags") or []
                if _is_payroll(flags, p.get("details","")):
                    return True
    return False

# -------------------------
# Core extractor
# -------------------------
def extract_features_from_json(resp: Dict[str, Any], window_days: int = 30) -> Dict[str, Any]:
    """
    Reads Inverite JSON and produces deterministic features for the last `window_days`
    ending at `complete_datetime`. Robust to Inverite quirks (reversals, payschedules, etc.).
    """
    end_dt = _parse_dt(resp.get("complete_datetime", "")) or datetime.utcnow()
    start_dt = end_dt - timedelta(days=window_days)

    # Flatten transactions in window
    txns: List[Dict[str, Any]] = []
    accounts = resp.get("accounts") or []
    for acc in accounts:
        for t in (acc.get("transactions") or []):
            dt = _parse_dt(t.get("date", ""))  # daily resolution
            if not dt or not (start_dt <= dt <= end_dt):
                continue

            credit = _to_float(t.get("credit", ""))
            debit = _to_float(t.get("debit", ""))
            details = t.get("details", "") or ""
            direction = _direction(credit, debit, details)
            amount = credit if direction == "credit" else debit

            txns.append({
                "date": dt,
                "details": details,
                "category": t.get("category", "") or "",
                "flags": [f.lower() for f in (t.get("flags") or [])],
                "direction": direction,
                "amount": float(amount or 0.0),
                "balance": _to_float(t.get("balance", "")),
                "counterparty": _canonical_counterparty(details),
            })

    # --- Counters / features ---
    # Payroll (from transactions OR payschedules)
    payroll_detected = any(_is_payroll(x["flags"], x["details"]) for x in txns)
    if not payroll_detected:
        payroll_detected = any(_payroll_from_payschedules(acc, start_dt, end_dt) for acc in accounts)

    # E-transfers IN/OUT
    etransfer_in = sum(1 for x in txns if _is_etransfer(x["details"]) and x["direction"] == "credit")
    etransfer_out = sum(1 for x in txns if _is_etransfer(x["details"]) and x["direction"] == "debit")

    # Gambling
    g_mask = [ _is_gambling(x["category"], x["details"]) for x in txns ]
    gambling_txn_count = sum(g_mask)
    gambling_total = round(sum(abs(x["amount"]) for x, gm in zip(txns, g_mask) if gm), 2)
    gambling_max = round(max([abs(x["amount"]) for x, gm in zip(txns, g_mask) if gm] or [0.0]), 2)

    # Bankruptcy / trustee
    trustee_mask = [ _is_trustee(x["flags"], x["category"], x["details"]) for x in txns ]
    under_bp = any(trustee_mask)
    trustee_payments = sum(1 for x, tm in zip(txns, trustee_mask) if tm and x["direction"] == "debit" and x["amount"] > 0)
    # Count failures on ANY direction if marked as return/reversal/etc.
    trustee_failed = sum(1 for x, tm in zip(txns, trustee_mask) if tm and _is_failed(x["flags"], x["details"]))

    # Payday loans (explicit only)
    def _is_payday_txn(x: Dict[str, Any]) -> bool:
        return _is_payday(x["flags"], x["category"])

    lenders = Counter([x["counterparty"] for x in txns if _is_payday_txn(x) and x["counterparty"]])
    distinct_lenders = len(lenders)
    loan_deductions = sum(1 for x in txns if _is_payday_txn(x) and x["direction"] == "debit")
    new_loans = sum(1 for x in txns if _is_payday_txn(x) and x["direction"] == "credit")
    # Active loans: any lender with ≥1 debit in window
    active_loans = len({x["counterparty"] for x in txns if _is_payday_txn(x) and x["direction"] == "debit" and x["counterparty"]})

    # NSF / Overdraft
    nsf_count = sum(1 for x in txns if re.search(r"\bnsf\b|\binsufficient funds\b|\bnon-?sufficient\b", x["details"].lower()))
    overdrafts = sum(1 for x in txns if "overdraft" in x["details"].lower() or (isinstance(x["balance"], (int, float)) and x["balance"] < 0))

    # Cashflow
    total_inflow = round(sum(x["amount"] for x in txns if x["direction"] == "credit"), 2)
    total_outflow = round(sum(x["amount"] for x in txns if x["direction"] == "debit"), 2)

    return {
        "window_days": window_days,
        "payroll_detected": payroll_detected,
        "etransfer_in_count_30d": etransfer_in,
        "etransfer_out_count_30d": etransfer_out,
        "nsf_count_30d": nsf_count,
        "overdraft_hits_30d": overdrafts,

        "gambling_detected": gambling_txn_count > 0,
        "gambling_txn_count_30d": gambling_txn_count,
        "gambling_total_amount_30d": gambling_total,
        "gambling_max_single_amount": gambling_max,

        "under_bankruptcy_or_consumer_proposal": under_bp,
        "trustee_payments_count": trustee_payments,
        "trustee_failed_count": trustee_failed,

        "new_loans_received_count": new_loans,
        "loan_deductions_count": loan_deductions,
        "distinct_lenders_count": distinct_lenders,
        "existing_loans_count": active_loans,

        "total_inflow_30d": total_inflow,
        "total_outflow_30d": total_outflow,

        # Small exemplars to help with logs/debugging
        "examples": {
            "lenders_top3": [m for m, _ in lenders.most_common(3)],
        },
    }

# Convenience wrapper preserved for compatibility
def extract_features(source: Any, window_days: int = 30) -> Dict[str, Any]:
    if isinstance(source, dict) and "accounts" in source:
        return extract_features_from_json(source, window_days=window_days)
    # Neutral defaults if called with non-JSON text (kept from your original)
    return {
        "window_days": window_days,
        "payroll_detected": False,
        "etransfer_in_count_30d": 0,
        "etransfer_out_count_30d": 0,
        "nsf_count_30d": 0,
        "overdraft_hits_30d": 0,
        "gambling_detected": False,
        "gambling_txn_count_30d": 0,
        "gambling_total_amount_30d": 0.0,
        "gambling_max_single_amount": 0.0,
        "under_bankruptcy_or_consumer_proposal": False,
        "trustee_payments_count": 0,
        "trustee_failed_count": 0,
        "new_loans_received_count": 0,
        "loan_deductions_count": 0,
        "distinct_lenders_count": 0,
        "existing_loans_count": 0,
        "total_inflow_30d": 0.0,
        "total_outflow_30d": 0.0,
        "examples": {},
        "_diagnostics": {"fallback_used": True},
    }
