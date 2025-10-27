# === formidable_receiver.py ===
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from inverite_data import fetch_report, convert_to_text
from loan_analyzer import analyze_bank_statement
from datetime import datetime
import os
import json
import requests
import logging
import time


# ──────────────────────────────────────
# Setup
# ──────────────────────────────────────
load_dotenv()
app = Flask(__name__)

NOTIFICATION_LOG = "notification_log.txt"
SKIPPED_FILE = "skipped_province.json"

INVERITE_API_KEY = os.getenv("INVERITE_API_KEY", "")
FORWARD_NOTIFICATION_URL = os.getenv("FORWARD_NOTIFICATION_URL", "")
PENDING_QUEUE_FILE = "pending_queue.json"



def _json_lines_write(filepath, entry):
    """Append a JSON entry line to a file (for logging)."""
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ──────────────────────────────────────
# 1️⃣ Inverite Webhook Receiver (Render)
# ──────────────────────────────────────
@app.route("/webhook/inverite", methods=["POST"])
def receive_inverite_notification():
    """
    Robust Inverite webhook receiver:
    - Handles JSON or form payloads
    - Maps common key variants
    - Logs raw + headers for debugging
    - ACKs fast, forwards asynchronously
    """
    try:
        headers = {k: v for k, v in request.headers.items()}
        raw_body = request.get_data(as_text=True)

        # Try JSON first (non-throwing), then form
        data = request.get_json(silent=True) or {}
        if not data and request.form:
            data = request.form.to_dict(flat=True)

        # Map common variants
        guid = data.get("request") or data.get("request_id") or data.get("guid") or data.get("id")
        name = data.get("name") or data.get("customer_name") or ""
        status = (data.get("status") or data.get("verification_status") or "").strip()

        entry = {
            "guid": guid,
            "name": name,
            "status": status,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Log everything for forensics (raw, headers, parsed, mapped)
        _json_lines_write(NOTIFICATION_LOG, {
            "raw": raw_body,
            "headers": headers,
            "parsed": data,
            **entry,
        })
        print(f"✅ Inverite notification logged: {entry}")

        # ACK immediately so provider won’t suppress future attempts
        resp = jsonify({"status": "logged"})

        # Fire-and-forget forward (don’t block the ACK)
        if FORWARD_NOTIFICATION_URL:
            try:
                requests.post(FORWARD_NOTIFICATION_URL, json=entry, timeout=2)
                print(f"📤 Forwarded to local analyzer: {FORWARD_NOTIFICATION_URL}")
            except Exception as e:
                logging.warning(f"⚠️ Forward failed: {e}")

        return resp, 200

    except Exception as e:
        logging.exception("❌ Error processing Inverite webhook")
        # Still 200: don’t cause the sender to back off on future webhooks
        return jsonify({"ok": False, "error": str(e)}), 200


# ──────────────────────────────────────
# 2️⃣ Local Notification Receiver (ngrok)
# ──────────────────────────────────────
@app.route("/local_notification", methods=["POST"])
def local_notification():
    """Receives forwarded notifications from Render via ngrok."""
    try:
        data = request.get_json(force=True)
        _json_lines_write(NOTIFICATION_LOG, data)
        print(f"📥 Local notification received: {data}")
        # (Optional) could trigger auto-analysis here if desired
        return jsonify({"status": "received"}), 200
    except Exception as e:
        print(f"❌ Local notification error: {e}")
        return jsonify({"error": str(e)}), 400


# ──────────────────────────────────────
# 🔍 Helper: Find GUID by Applicant Name
# ──────────────────────────────────────
def find_guid_by_name(first_name, last_name):
    """Search notification_log.txt for a matching applicant name."""
    if not os.path.exists(NOTIFICATION_LOG):
        print("⚠️ No notification log found yet.")
        return None

    with open(NOTIFICATION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                name = entry.get("name", "").lower()
                status = entry.get("status", "").lower()
                if first_name.lower() in name and last_name.lower() in name and status == "verified":
                    guid = entry.get("guid")
                    print(f"✅ Match found in log for {first_name} {last_name}: GUID={guid}")
                    return guid
            except json.JSONDecodeError:
                continue

    print(f"⚠️ No matching Inverite entry found for {first_name} {last_name}.")
    return None
def add_to_pending_queue(item):
    """
    Append (or upsert) an applicant into a simple JSON pending queue.

    item example:
    {
        "first_name": "Jane",
        "last_name": "Doe",
        "loan_type": "payday",
        "loan_amount": "500",
        "timestamp": "YYYY-MM-DD HH:MM:SS"
    }
    """
    # ensure minimal shape
    item = {
        "first_name": item.get("first_name", "").strip(),
        "last_name": item.get("last_name", "").strip(),
        "loan_type": item.get("loan_type", "payday"),
        "loan_amount": item.get("loan_amount", ""),
        "timestamp": item.get("timestamp"),
    }

    # read existing queue (tolerate missing/corrupt file)
    if os.path.exists(PENDING_QUEUE_FILE):
        try:
            with open(PENDING_QUEUE_FILE, "r", encoding="utf-8") as f:
                queue = json.load(f)
            if not isinstance(queue, list):
                queue = []
        except json.JSONDecodeError:
            queue = []
    else:
        queue = []

    # simple upsert by (first_name,last_name,loan_type) to avoid duplicates
    key = (item["first_name"].lower(), item["last_name"].lower(), item["loan_type"].lower())
    found = False
    for i, existing in enumerate(queue):
        ekey = (
            str(existing.get("first_name", "")).lower(),
            str(existing.get("last_name", "")).lower(),
            str(existing.get("loan_type", "")).lower(),
        )
        if ekey == key:
            queue[i] = {**existing, **item}  # update/refresh
            found = True
            break
    if not found:
        queue.append(item)

    with open(PENDING_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)

    print(f"🕒 Pending queue updated ({len(queue)} items). Added/updated: {item['first_name']} {item['last_name']} [{item['loan_type']}]")


# ──────────────────────────────────────
# 3️⃣ Payday Form Submission (Main Logic)
# ──────────────────────────────────────
@app.route("/webhook/payday", methods=["POST"])
def payday_webhook():
    """
    Receives payday loan form submissions.
    Waits for Inverite report, processes if ready,
    otherwise adds to pending queue.
    """
    try:
        data = request.get_json(force=True)
        print("📩 Received payday form data:")
        print(json.dumps(data, indent=2))

        first_name = data.get("first_name", "").strip()
        last_name = data.get("last_name", "").strip()
        loan_type = data.get("loan_type", "payday")
        loan_amount = data.get("loan_amount", "")
        address = data.get("address", "").strip().lower()

        print(f"⏳ Checking province eligibility for {first_name} {last_name}...")

        # ✅ Province Detection Logic (fixed)
        allowed_provinces = ["ontario", "alberta"]
        all_known_provinces = {
            "ontario": ["ontario", "on"],
            "alberta": ["alberta", "ab"],
            "british columbia": ["british columbia", "bc"],
            "saskatchewan": ["saskatchewan", "sk"],
            "manitoba": ["manitoba", "mb"],
            "quebec": ["quebec", "qc"],
            "new brunswick": ["new brunswick", "nb"],
            "nova scotia": ["nova scotia", "ns"],
            "newfoundland": ["newfoundland", "nl"],
            "prince edward island": ["prince edward island", "pei"],
        }

        detected_province = None
        for province, variants in all_known_provinces.items():
            for variant in variants:
                # match with commas or spaces to avoid false positives like "london"
                if (
                    f" {variant} " in f" {address} "
                    or f",{variant}," in address
                    or f",{variant} " in address
                    or f" {variant}," in address
                ):
                    detected_province = province
                    break
            if detected_province:
                break

        # 🚫 Reject unsupported provinces
        if detected_province not in allowed_provinces:
            print(f"🚫 Application rejected — Province not supported: {detected_province or 'unknown'}")

            skipped_entry = {
                "first_name": first_name,
                "last_name": last_name,
                "address": address,
                "detected_province": detected_province or "unknown",
                "loan_type": loan_type,
                "loan_amount": loan_amount,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            # Save rejection
            if os.path.exists(SKIPPED_FILE):
                try:
                    with open(SKIPPED_FILE, "r", encoding="utf-8") as f:
                        skipped_data = json.load(f)
                except json.JSONDecodeError:
                    skipped_data = []
            else:
                skipped_data = []

            skipped_data.append(skipped_entry)
            with open(SKIPPED_FILE, "w", encoding="utf-8") as f:
                json.dump(skipped_data, f, indent=2, ensure_ascii=False)

            print(f"📝 Skipped applicant logged in {SKIPPED_FILE}")
            return jsonify({
                "status": "rejected",
                "message": "We currently only offer loans in Ontario and Alberta."
            }), 403

        print(f"✅ Province validated: {detected_province.title()}")

        # 🔍 Wait for Inverite Report
        print(f"⏳ Waiting for Inverite report for {first_name} {last_name}...")
        guid = None
        for attempt in range(10):  # 10 tries × 30s = 5 min
            guid = find_guid_by_name(first_name, last_name)
            if guid:
                print(f"✅ Found Inverite GUID {guid} after {attempt * 30}s")
                break
            time.sleep(30)

        if not guid:
            print(f"⚠️ No Inverite report yet for {first_name} {last_name}. Adding to pending queue.")
            add_to_pending_queue({
                "first_name": first_name,
                "last_name": last_name,
                "loan_type": loan_type,
                "loan_amount": loan_amount,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            return jsonify({
                "status": "pending",
                "message": "Report not yet available. Added to pending queue."
            }), 202

        # 🧾 Report Found – Fetch & Analyze
        report = fetch_report(guid)
        text_summary = convert_to_text(report)
        decision = analyze_bank_statement(text_summary, loan_amount)

        # 🔄 Save decision
        DECISION_LOG_FILE = "payday_loan_decisions.json"
        if os.path.exists(DECISION_LOG_FILE):
            try:
                with open(DECISION_LOG_FILE, "r", encoding="utf-8") as f:
                    all_decisions = json.load(f)
            except json.JSONDecodeError:
                all_decisions = []
        else:
            all_decisions = []

        entry = {
            "first_name": first_name,
            "last_name": last_name,
            "guid": guid,
            "loan_type": loan_type,
            "loan_amount": loan_amount,
            "decision": decision.get("decision"),
            "approved_amount": decision.get("approved_amount"),
            "rationale": decision.get("rationale"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        all_decisions.append(entry)

        with open(DECISION_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(all_decisions, f, indent=2, ensure_ascii=False)

        print(f"✅ Decision saved to {DECISION_LOG_FILE} ({len(all_decisions)} total records)")
        print(f"🤖 Loan decision completed for {first_name} {last_name}")

        return jsonify({
            "status": "success",
            "guid": guid,
            "decision": decision
        }), 200

    except Exception as e:
        print(f"❌ Error in payday_webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/")
def home():
    return "App is running!", 200

# ──────────────────────────────────────
# Run server
# ──────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
