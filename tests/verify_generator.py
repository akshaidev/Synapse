import json
import sys
import os
from datetime import datetime

# Update sys.path to be able to import api.schemas
sys.path.append("/Users/akshai/Developer/Synapse")
from api.schemas import IncidentPayload

def test_generated_payloads():
    print("--- VERIFYING SYNTHETIC DATA GENERATOR ---")
    cities = ["pune", "bengaluru", "delhi"]
    
    all_passed = True
    
    for city in cities:
        filepath = f"/Users/akshai/Developer/Synapse/synthetic/payload_{city}.json"
        
        if not os.path.exists(filepath):
            print(f"[FAIL] {filepath} does not exist.")
            all_passed = False
            continue
            
        with open(filepath, "r") as f:
            data = json.load(f)
            
        try:
            # 1. Pydantic validation (which implicitly tests Gate 1 & Gate 2)
            payload_obj = IncidentPayload(**data)
            print(f"[PASS] {city.capitalize()} payload strictly valid against IncidentPayload schema")
            
            # 2. Check [v1.3 FIX 4A] explicitly
            complaint_ts = payload_obj.ncrp_ticket.complaint_timestamp
            max_txn_ts = max(t.txn_timestamp for t in payload_obj.fund_flow.transactions)
            
            if complaint_ts <= max_txn_ts:
                print(f"[FAIL] {city.capitalize()} payload has complaint_timestamp <= max(txn_timestamp)")
                all_passed = False
            else:
                delta = complaint_ts - max_txn_ts
                print(f"[PASS] {city.capitalize()} complaint_timestamp is strictly after max(txn_timestamp) by {delta.total_seconds()/60:.1f} minutes")
                
            # Check amounts
            amounts = [t.amount_inr for t in payload_obj.fund_flow.transactions]
            if sorted(amounts, reverse=True) != amounts:
                 print(f"[WARN] {city.capitalize()} amounts are not strictly decreasing (Mule didn't take a cut?)")
                 
            # Check Bank limits
            limit = payload_obj.terminal_mule.daily_withdrawal_limit_inr
            bank = payload_obj.terminal_mule.mule_bank
            if bank in ["HDFC Bank", "ICICI Bank", "Axis Bank"] and limit != 200000.0:
                print(f"[FAIL] {city.capitalize()} {bank} limit incorrect")
                all_passed = False
            elif bank in ["Canara Bank", "State Bank of India", "Punjab National Bank", "Bank of Baroda"] and limit != 100000.0:
                print(f"[FAIL] {city.capitalize()} {bank} limit incorrect")
                all_passed = False
                
        except Exception as e:
            print(f"[FAIL] {city.capitalize()} payload failed schema validation: {e}")
            all_passed = False
            
    if all_passed:
        print("\n--- ALL GENERATOR TESTS PASSED ---")
        return True
    else:
        print("\n--- GENERATOR TESTS FAILED ---")
        return False

if __name__ == "__main__":
    sys.exit(0 if test_generated_payloads() else 1)
