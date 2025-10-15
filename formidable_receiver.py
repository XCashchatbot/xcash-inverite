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

# ──────────────────────────────────────
# Setup
# ──────────────────────────────────────
load_dotenv()
app = Flask(__name__)

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
NOTIFICATION_LOG = os.path.join(DATA_DIR, "notification_log.txt")

INVERITE_API_KEY = os.getenv("INVERITE_API_KEY", "")
FORWARD_NOTIFICATION_URL = os.getenv("FORWARD_NOTIFICATION_URL", "")


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
    Receives Inverite webhook POST when verification completes.
    Logs notification and forwards to local analyzer if FORWARD_NOTIFICATION_URL is set.
    """
    try:
        raw_body = request.get_data(as_text=True)
        data = json.loads(raw_body)

        entry = {
            "guid": data.get("request"),
            "name": data.get("name", ""),
            "status": data.get("status", ""),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # ✅ Log locally on Render
        _json_lines_write(NOTIFICATION_LOG, entry)
        print(f"✅ Inverite notification logged: {entry}")

        # 📤 Forward to your local analyzer (via ngrok)
        if FORWARD_NOTIFICATION_URL:
            try:
                requests.post(FORWARD_NOTIFICATION_URL, json=entry, timeout=5)
                print(f"📤 Forwarded to local analyzer: {FORWARD_NOTIFICATION_URL}")
            except Exception as e:
                logging.warning(f"⚠️ Forward failed: {e}")

        return jsonify({"status": "logged"}), 200

    except Exception as e:
        logging.error(f"❌ Error processing Inverite webhook: {e}")
        return jsonify({"error": str(e)}), 500


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
        # (Optional) could trigger auto-analysis here if you want
        return jsonify({"status": "received"}), 200
    except Exception as e:
        print(f"❌ Local notification error: {e}")
        return jsonify({"error": str(e)}), 400

# 🔍 Helper function to find GUID by applicant name
def find_guid_by_name(first_name, last_name):
    """
    Search notification_log.txt for a matching applicant name
    and return the corresponding Inverite GUID if found.
    """
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

# ──────────────────────────────────────
# 3️⃣ Payday Form Submission (Analysis)
# ──────────────────────────────────────
@app.route("/webhook/payday", methods=["POST"])
def payday_webhook():
    """
    Receives payday loan form submissions.
    Waits a few minutes for Inverite report, processes if ready,
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

        print(f"⏳ Waiting for Inverite report for {first_name} {last_name}...")

        # Try finding a GUID (check every 30 sec for 5 minutes)
        guid = None
        for attempt in range(10):  # 10 tries * 30 sec = 5 minutes
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
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            return jsonify({
                "status": "pending",
                "message": "Report not yet available. Added to pending queue."
            }), 202

        # 🧾 Report found – fetch and analyze
        report = fetch_report(guid)
        text_summary = convert_to_text(report)
        decision = analyze_bank_statement(text_summary, loan_amount)

        os.makedirs(DATA_DIR, exist_ok=True)
        decision_file = f"{DATA_DIR}/{first_name}_{last_name}_{guid}_decision.json"
        with open(decision_file, "w", encoding="utf-8") as f:
            json.dump(decision, f, indent=2, ensure_ascii=False)

        print(f"🤖 Loan decision completed for {first_name} {last_name}")
        return jsonify({
            "status": "success",
            "guid": guid,
            "decision": decision
        }), 200

    except Exception as e:
        print(f"❌ Error in payday_webhook: {e}")
        return jsonify({"error": str(e)}), 500

# ──────────────────────────────────────
# Run server
# ──────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)

