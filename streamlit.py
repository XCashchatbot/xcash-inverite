import json
from pathlib import Path
import pandas as pd
import streamlit as st

# ========= Files =========
DECISION_LOG_FILE = "payday_loan_decisions.json"
SKIPPED_FILE = "skipped_province.json"

# ========= Page =========
st.set_page_config(page_title="Payday Loan Review", layout="wide")
st.title("📄 Payday Loan Review")

# ========= Helpers =========
def safe_load_json(path: str):
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def fmt_currency(x):
    if pd.isna(x):
        return "—"
    try:
        x = float(x)
        return f"${x:,.0f}" if x.is_integer() else f"${x:,.2f}"
    except Exception:
        return str(x)

def decision_badge_html(decision: str) -> str:
    d = (decision or "").strip().lower()
    if d == "approved":
        return '<span class="badge badge-approve">Approved</span>'
    if d == "declined":
        return '<span class="badge badge-decline">Declined</span>'
    if d == "approved for lower amount":
        return '<span class="badge badge-info">Approved (Lower)</span>'
    return f'<span class="badge badge-info">{decision or "—"}</span>'

def render_cards(
    df: pd.DataFrame,
    show_decision_badge: bool = True,
    extra_rows=None,
    include_requested_amount: bool = True,
    include_rationale: bool = True,
):
    """
    Render expandable cards. Build ONE HTML block per card so details/summary remain intact.
    extra_rows: list of (label, fn(row)) tuples to inject extra lines.
    """
    if df.empty:
        st.info("No results match your filters.")
        return

    for _, row in df.iterrows():
        # Name
        if "Name" in df.columns and pd.notna(row.get("Name", None)) and str(row.get("Name")).strip():
            name = str(row.get("Name")).strip()
        else:
            first = str(row.get("first_name", "")).strip()
            last  = str(row.get("last_name", "")).strip()
            name  = (first + " " + last).strip()

        decision  = row.get("decision", "")
        right_html = decision_badge_html(decision) if show_decision_badge else ""

        # Build body
        body_html = ""

        if include_requested_amount and "Requested Amount" in df.columns:
            requested = row.get("Requested Amount", None)
            body_html += f'<div class="kv-row"><span class="kv-key">Requested Amount:</span> <span>{fmt_currency(requested)}</span></div>'

        if extra_rows:
            for label, fn in extra_rows:
                try:
                    val = fn(row)
                except Exception:
                    val = "—"
                body_html += f'<div class="kv-row"><span class="kv-key">{label}:</span> <span>{val}</span></div>'

        if include_rationale:
            rationale = (row.get("rationale") or row.get("reason") or "_No rationale provided._")
            body_html += (
                '<div class="kv-row"><span class="kv-key">Rationale:</span></div>'
                f'<div class="rationale">{rationale}</div>'
            )

        # One single HTML block per card
        card_html = f"""
<div class="decision-card">
  <details>
    <summary>
      <div class="card-header">
        <div class="card-left">
          <span class="caret"></span>
          <span>{name}</span>
        </div>
        <div class="card-right">{right_html}</div>
      </div>
    </summary>
    <div class="card-body">
      {body_html}
    </div>
  </details>
</div>
"""
        st.markdown(card_html, unsafe_allow_html=True)

# ========= CSS =========
st.markdown("""
<style>
/* Card container */
.decision-card {
  border: 1px solid #E5E7EB;
  border-radius: 12px;
  padding: 6px 10px;
  margin: 10px 0;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}

/* Native details/summary expander */
.decision-card details { cursor: pointer; }
.decision-card summary { list-style: none; }
.decision-card summary::-webkit-details-marker { display:none; }

/* Header row: flex for perfect alignment */
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 6px;
}
.card-left {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  color: #0F172A; /* darker */
}
.card-right {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* Caret */
.caret {
  width: 9px; height: 9px;
  border-right: 2px solid #374151;
  border-bottom: 2px solid #374151;
  transform: rotate(-45deg);
  margin-right: 6px;
  transition: transform 0.15s ease;
}
details[open] .caret { transform: rotate(45deg); }

/* Badges */
.badge {
  font-size: 12px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid transparent;
}
.badge-approve {
  color: #065F46;
  background: #D1FAE5;
  border: 1px solid #065F46;
}
.badge-decline {
  color: #7F1D1D;
  background: #FEE2E2;
  border: 1px solid #7F1D1D;
}
.badge-info {
  color: #1D4ED8;
  background: #DBEAFE;
  border-color: #93C5FD;
}

/* Body */
.card-body {
  border-top: 1px dashed #E5E7EB;
  margin-top: 6px;
  padding: 12px 8px 14px;
}
.kv-row { margin: 2px 0 10px; }
.kv-key { color:#374151; font-weight:700; margin-right:6px; }
.rationale { white-space: pre-wrap; line-height: 1.5; color:#111827; }
</style>
""", unsafe_allow_html=True)

# ========= Load data =========
decisions_raw = safe_load_json(DECISION_LOG_FILE)
skipped_raw   = safe_load_json(SKIPPED_FILE)

# ========= Tabs =========
tab1, tab2 = st.tabs(["✅ Decisions", "⏭️ Skipped Applicants"])

# =========================
# Tab 1: Decisions
# =========================
with tab1:
    if not decisions_raw:
        st.info("No decisions found.")
    else:
        df = pd.DataFrame(decisions_raw)

        # timestamps, sorting
        df["timestamp_dt"] = pd.to_datetime(df.get("timestamp"), errors="coerce")
        df = df.sort_values("timestamp_dt", ascending=False)

        # Name (safe)
        first = df["first_name"] if "first_name" in df.columns else pd.Series("", index=df.index)
        last  = df["last_name"]  if "last_name"  in df.columns else pd.Series("", index=df.index)
        df["Name"] = (
            pd.Series(first, dtype="string").fillna("").str.strip() + " " +
            pd.Series(last,  dtype="string").fillna("").str.strip()
        ).str.strip()

        # Requested Amount
        df["Requested Amount"] = pd.to_numeric(df.get("loan_amount"), errors="coerce")

        # Month (for sidebar filters)
        df["Month"] = df["timestamp_dt"].dt.strftime("%Y-%m")

        # Sidebar filters (Decisions only)
        with st.sidebar:
            st.header("Filters (Decisions)")
            decisions_available = sorted([d for d in df.get("decision", pd.Series(dtype="string")).dropna().unique()])
            decisions_selected = st.multiselect("Decision", decisions_available, default=decisions_available)

            months_available = sorted(df["Month"].dropna().unique(), reverse=True)
            months_selected = st.multiselect("Month (YYYY-MM)", months_available, default=months_available)

            name_query = st.text_input("Search name (optional)").strip()

        # Apply filters
        mask = pd.Series(True, index=df.index)
        if decisions_selected:
            mask &= df["decision"].isin(decisions_selected)
        if months_selected:
            mask &= df["Month"].isin(months_selected)
        if name_query:
            mask &= df["Name"].str.contains(name_query, case=False, na=False)
        fdf = df.loc[mask].copy()

        # Render
        render_cards(fdf, show_decision_badge=True, include_requested_amount=True, include_rationale=True)

# =========================
# Tab 2: Skipped Applicants
# =========================
with tab2:
    st.subheader("Skipped Applicants")

    if not skipped_raw:
        st.info("No skipped applicants file found or it is empty.")
    else:
        sdf = pd.DataFrame(skipped_raw)

        # Normalize
        sdf["timestamp_dt"] = pd.to_datetime(sdf.get("timestamp"), errors="coerce")
        sdf = sdf.sort_values("timestamp_dt", ascending=False)

        # Name (safe)
        first2 = sdf["first_name"] if "first_name" in sdf.columns else pd.Series("", index=sdf.index)
        last2  = sdf["last_name"]  if "last_name"  in sdf.columns else pd.Series("", index=sdf.index)
        sdf["Name"] = (
            pd.Series(first2, dtype="string").fillna("").str.strip() + " " +
            pd.Series(last2,  dtype="string").fillna("").str.strip()
        ).str.strip()

        # Province comes from detected_province (display nicely)
        def nice_province(x):
            if pd.isna(x) or not str(x).strip():
                return "—"
            val = str(x).strip()
            if val.lower() == "unknown":
                return "Unknown"
            return val.title()

        if "detected_province" in sdf.columns:
            sdf["Province"] = sdf["detected_province"].apply(nice_province)
        else:
            sdf["Province"] = "—"

        # Address if present
        if "address" not in sdf.columns:
            sdf["address"] = ""

        # Build simple cards with NO filters and NO rationale
        extra_rows = [
            ("Province", lambda r: r.get("Province", "—") or "—"),
            ("Address",  lambda r: r.get("address", "—") or "—"),
            
        ]
        render_cards(
            sdf,
            show_decision_badge=False,
            extra_rows=extra_rows,
            include_requested_amount=False,   # you asked not to show requested amount here
            include_rationale=False          # no rationale on skipped tab
        )
