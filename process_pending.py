import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv

from inverite_data import fetch_report, convert_to_text
from loan_analyzer import analyze_bank_statement

load_dotenv()

PENDING_QUEUE_FILE = "pending_queue.json"
NOTIFICATION_LOG = "notification_log.txt"
DECISION_LOG_FILE = "payday_loan_decisions.json"

def load_json_list(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def write_json_list(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def find_guid_in_notifications(first_name, last_name):
    """Search Inverite webhook log for this applicant"""
    if not os.path.exists(NOTIFICATION_LOG):
        return None
    with open(NOTIFICATION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                name = entry.get("name", "").lower()
                if first_name.lower() in name and last_name.lower() in name:
                    if entry.get("status", "").lower() == "verified":
                        return entry.get("guid")
            except:
                continue
    return None

def process_pending():
    queue = load_json_list(PENDING_QUEUE_FILE)
    if not queue:
        print("✅ No pending applicants.")
        return

    new_queue = []
    for applicant in queue:
        first = applicant.get("first_name")
        last = applicant.get("last_name")
        loan_amount = applicant.get("loan_amount", "")

        print(f"⏳ Checking pending applicant: {first} {last}...")

        guid = find_guid_in_notifications(first, last)
        if not guid:
            print(f"⚠️ No Inverite report yet for {first} {last}, keeping in queue.")
            new_queue.append(applicant)
            continue

        print(f"✅ Inverite report found for {first} {last}: GUID={guid}")

        try:
            report = fetch_report(guid)
            text_summary = convert_to_text(report)
            decision = analyze_bank_statement(text_summary, loan_amount)

            decision_entry = {
                "first_name": first,
                "last_name": last,
                "guid": guid,
                "loan_amount": loan_amount,
                "decision": decision.get("decision"),
                "approved_amount": decision.get("approved_amount"),
                "rationale": decision.get("rationale"),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            all_decisions = load_json_list(DECISION_LOG_FILE)
            all_decisions.append(decision_entry)
            write_json_list(DECISION_LOG_FILE, all_decisions)

            print(f"✅ Decision saved for {first} {last}: {decision.get('decision')}")

        except Exception as e:
            print(f"❌ Error analyzing {first} {last}: {e}")
            new_queue.append(applicant)

    write_json_list(PENDING_QUEUE_FILE, new_queue)
    print("✅ Pending list updated.")

from datetime import datetime

@app.route("/", methods=["GET"])
def home():
    """Simple route for uptime pings."""
    return {"status": "ok", "time": datetime.now().isoformat()}

if __name__ == "__main__":
    print(f"🔁 Running pending processor at {datetime.now()}")
    process_pending()
    print("✅ Done.")
