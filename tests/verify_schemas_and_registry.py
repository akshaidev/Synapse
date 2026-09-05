import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import sys

# Update sys.path to be able to import api.schemas
sys.path.append("/Users/akshai/Developer/Synapse")
from api.schemas import IncidentPayload, FreezeCardATMRequest

def test_registry():
    print("--- VERIFYING ATM REGISTRY ---")
    with open("/Users/akshai/Developer/Synapse/data/atm_registry.json", "r") as f:
        atms = json.load(f)
    
    if len(atms) != 200:
        print(f"[FAIL] Expected 200 ATMs, found {len(atms)}")
        return False
        
    cnrb_atm = next((atm for atm in atms if atm["atm_id"] == "CNRB-ATM-PNE-0042"), None)
    if not cnrb_atm:
        print("[FAIL] CNRB-ATM-PNE-0042 not found")
        return False
        
    if cnrb_atm["is_onsite"] is not False:
        print("[FAIL] CNRB-ATM-PNE-0042 is_onsite is not False")
        return False
        
    print("[PASS] ATM Registry has 200 records, CNRB-ATM-PNE-0042 is offsite")
    return True

def test_schemas():
    print("\n--- VERIFYING SCHEMAS ---")
    
    now = datetime.now(timezone.utc)
    # Valid Payload
    valid_payload = {
        "payload_version": "1.1.0",
        "payload_id": str(uuid4()),
        "ingestion_timestamp": now.isoformat(),
        "ncrp_ticket": {
            "ticket_id": "NCRP-2026-0045781",
            "complaint_timestamp": (now - timedelta(minutes=10)).isoformat(),
            "victim_state": "MH",
            "victim_district": "Pune",
            "fraud_type": "UPI_FRAUD",
            "amount_inr": 487500.0,
            "source_account": {
                "account_number": "918010045672301",
                "ifsc": "UTIB0002583",
                "bank_name": "Axis Bank"
            }
        },
        "fund_flow": {
            "total_hops": 2,
            "transactions": [
                {
                    "hop_index": 1,
                    "txn_id": "UTR1",
                    "txn_timestamp": (now - timedelta(minutes=20)).isoformat(),
                    "sender_account": "918010045672301",
                    "sender_ifsc": "UTIB0002583",
                    "sender_bank": "Axis Bank",
                    "receiver_account": "50100287654321",
                    "receiver_ifsc": "HDFC0001729",
                    "receiver_bank": "HDFC Bank",
                    "amount_inr": 487500.0,
                    "channel": "UPI"
                },
                {
                    "hop_index": 2,
                    "txn_id": "UTR2",
                    "txn_timestamp": (now - timedelta(minutes=15)).isoformat(),
                    "sender_account": "50100287654321",
                    "sender_ifsc": "HDFC0001729",
                    "sender_bank": "HDFC Bank",
                    "receiver_account": "09871234567890",
                    "receiver_ifsc": "CNRB0002341",
                    "receiver_bank": "Canara Bank",
                    "amount_inr": 485000.0,
                    "channel": "IMPS"
                }
            ]
        },
        "terminal_mule": {
            "mule_account_number": "09871234567890",
            "mule_ifsc": "CNRB0002341",
            "mule_bank": "Canara Bank",
            "current_balance_inr": 241350.0,
            "account_type": "SAVINGS",
            "linked_card_number_hash": "b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
            "daily_withdrawal_limit_inr": 100000.0,
            "withdrawals_today_inr": 0.0,
            "cell_tower_cluster": [],
            "ip_cluster": []
        }
    }
    
    try:
        incident = IncidentPayload(**valid_payload)
        print("[PASS] Valid IncidentPayload parsed successfully")
    except Exception as e:
        print(f"[FAIL] Valid payload failed: {e}")
        return False
        
    # Test Gate 1 Failure
    gate1_payload = json.loads(json.dumps(valid_payload))
    gate1_payload["fund_flow"]["transactions"][0]["txn_timestamp"] = (now - timedelta(minutes=135)).isoformat()
    gate1_payload["fund_flow"]["transactions"][1]["txn_timestamp"] = (now - timedelta(minutes=130)).isoformat()
    try:
        IncidentPayload(**gate1_payload)
        print("[FAIL] Gate 1 failure did not trigger")
        return False
    except ValueError as e:
        if "GOLDEN_HOUR_EXPIRED" in str(e):
            print("[PASS] Gate 1 correctly triggered GOLDEN_HOUR_EXPIRED")
        else:
            print(f"[FAIL] Gate 1 error mismatch: {e}")
            return False

    # Test Gate 2 Failure
    gate2_payload = json.loads(json.dumps(valid_payload))
    gate2_payload["ncrp_ticket"]["complaint_timestamp"] = (now - timedelta(minutes=250)).isoformat()
    try:
        IncidentPayload(**gate2_payload)
        print("[FAIL] Gate 2 failure did not trigger")
        return False
    except ValueError as e:
        if "STALE_PAYLOAD" in str(e):
            print("[PASS] Gate 2 correctly triggered STALE_PAYLOAD")
        else:
            print(f"[FAIL] Gate 2 error mismatch: {e}")
            return False

    return True

if __name__ == "__main__":
    r1 = test_registry()
    r2 = test_schemas()
    if r1 and r2:
        print("\n--- ALL TESTS PASSED ---")
    else:
        print("\n--- SOME TESTS FAILED ---")
