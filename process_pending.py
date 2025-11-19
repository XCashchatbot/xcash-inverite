import os
import json
import time
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

from dotenv import load_dotenv
from inverite_data import fetch_report, convert_to_text
from loan_analyzer import analyze_bank_statement

load_dotenv()

# --- Always write beside this file, not the current working directory
BASE_DIR = Path(__file__).resolve().parent
PENDING_QUEUE_FILE = BASE_DIR / "pending_queue.json"
NOTIFICATION_LOG    = BASE_DIR / "notification_log.txt"
DECISION_LOG_FILE   = BASE_DIR / "payday_loan_decisions.json"

# --- Simple cross-platform file lock (Windows-friendly)
@contextmanager
def file_lock(lock_path: Path, poll_interval=0.1, timeout=30):
    start = time.time()
    while True:
        try:
            # create a lockfile exclusively
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            try:
                yield
            finally:
                os.close(fd)
                try:
                    lock_path.unlink(missing_ok=True)
                except Exception:
                    pass
            break
        except FileExistsError:
            if time.time() - start > timeout:
                raise TimeoutError(f"Could not acquire lock: {lock_path}")
            time.sleep(poll_interval)

def read_json_list(filepath: Path):
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        # raise if invalid so we don't silently nuke content
        return json.load(f)

def write_json_list_atomic(filepath: Path, data):
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(filepath)

def append_unique_decision(entry: dict, filepath: Path):
    lockfile = filepath.with_suffix(filepath.suffix + ".lock")
    with file_lock(lockfile):
        data = read_json_list(filepath) if filepath.exists() else []
        # Deduplicate by (guid, timestamp). Adjust if you prefer only guid.
        seen = {(d.get("guid"), d.get("timestamp")) for d in data}
        key = (entry.get("guid"), entry.get("timestamp"))
        if key not in seen:
            data.append(entry)
            write_json_list_atomic(filepath, data)
            return True
        return False

def find_guid_in_notifications(first_name, last_name):
    if not NOTIFICATION_LOG.exists():
        return None
    fn = first_name.lower()
    ln = last_name.lower()
    with open(NOTIFICATION_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = (entry.get("name") or "").lower()
            if fn in name and ln in name and (entry.get("status") or "").lower() == "verified":
                return entry.get("guid")
    return None

def process_pending():
    try:
        queue = read_json_list(PENDING_QUEUE_FILE) if PENDING_QUEUE_FILE.exists() else []
    except Exception as e:
        print(f"❌ Pending queue is invalid JSON: {e}")
        queue = []

    if not queue:
        print("✅ No pending applicants.")
        return

    new_queue = []
    for applicant in queue:
        first = applicant.get("first_name", "").strip()
        last  = applicant.get("last_name", "").strip()
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

            # 🔐 SAFETY: normalize the result from analyze_bank_statement
            decision = analyze_bank_statement(text_summary, loan_amount)

            if not isinstance(decision, dict):
                print(
                    f"⚠️ Unexpected decision type for {first} {last}: "
                    f"{type(decision)} -> {decision}"
                )
                decision = {
                    "decision": "Error",
                    "approved_amount": None,
                    "rationale": f"Non-dict decision: {decision}",
                }

            decision_entry = {
                "first_name": first,
                "last_name": last,
                "guid": guid,
                "loan_amount": loan_amount,
                "decision": decision.get("decision"),
                "approved_amount": decision.get("approved_amount"),
                "rationale": decision.get("rationale"),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            if append_unique_decision(decision_entry, DECISION_LOG_FILE):
                print(f"✅ Decision saved for {first} {last}: {decision.get('decision')}")
            else:
                print(f"ℹ️ Skipped duplicate decision for {first} {last}")

        except Exception as e:
            print(f"❌ Error analyzing {first} {last}: {e}")
            new_queue.append(applicant)

    # Atomically write the updated queue
    try:
        write_json_list_atomic(PENDING_QUEUE_FILE, new_queue)
    except Exception as e:
        print(f"❌ Could not update pending queue: {e}")
    else:
        print("✅ Pending list updated.")

if __name__ == "__main__":
    print("🚀 Starting process_pending.py...")
    process_pending()
    print("🏁 Script finished.")
    time.sleep(60)
