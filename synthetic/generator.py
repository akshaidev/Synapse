import json
import os
import random
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import hashlib

def generate_account_number():
    return str(random.randint(10**10, 10**14 - 1))

def generate_txn_id(channel):
    return f"{channel}{datetime.now().strftime('%Y%m%d')}{random.randint(1000, 9999)}"

CITIES = [
    {"name": "Pune", "state": "MH", "prefix": "PNE", "lat_range": (18.4, 18.7), "lon_range": (73.7, 74.0)},
    {"name": "Bengaluru", "state": "KA", "prefix": "BLR", "lat_range": (12.8, 13.1), "lon_range": (77.5, 77.8)},
    {"name": "Delhi", "state": "DL", "prefix": "DEL", "lat_range": (28.5, 28.8), "lon_range": (77.1, 77.4)}
]

BANKS = [
    {"name": "Canara Bank", "ifsc": "CNRB0002341", "limit": 100000.0},
    {"name": "State Bank of India", "ifsc": "SBIN0011424", "limit": 100000.0},
    {"name": "HDFC Bank", "ifsc": "HDFC0001729", "limit": 200000.0},
    {"name": "Punjab National Bank", "ifsc": "PUNB0187600", "limit": 100000.0},
    {"name": "ICICI Bank", "ifsc": "ICIC0000100", "limit": 200000.0},
    {"name": "Bank of Baroda", "ifsc": "BARB0VJPUNE", "limit": 100000.0},
    {"name": "Axis Bank", "ifsc": "UTIB0002583", "limit": 200000.0}
]

CHANNELS = ["UPI", "IMPS", "NEFT", "RTGS"]
FRAUD_TYPES = ["UPI_FRAUD", "VISHING", "PHISHING", "SIM_SWAP"]

def generate_payload(city, now):
    num_hops = random.randint(2, 7)
    initial_amount = random.uniform(50000, 500000)
    
    # We want max(txn_timestamp) to be <= 120 minutes from NOW.
    # We also want complaint_timestamp > max(txn_timestamp).
    # And complaint_timestamp <= 240 minutes from NOW.
    
    # Let's anchor the last hop at NOW - 30 minutes
    last_hop_time = now - timedelta(minutes=30)
    
    # v1.3 FIX 4A: complaint_timestamp MUST be AFTER the last hop
    complaint_ts = last_hop_time + timedelta(minutes=random.randint(1, 5))
    
    transactions = []
    current_amount = initial_amount
    
    victim_bank = random.choice(BANKS)
    current_sender_acc = generate_account_number()
    current_sender_ifsc = victim_bank["ifsc"]
    current_sender_bank_name = victim_bank["name"]
    
    source_account_info = {
        "account_number": current_sender_acc,
        "ifsc": current_sender_ifsc,
        "bank_name": current_sender_bank_name
    }
    
    for i in range(num_hops):
        # Time sequential, ending at last_hop_time
        # Say each hop takes 5-15 mins
        mins_before_last = (num_hops - 1 - i) * 10
        txn_time = last_hop_time - timedelta(minutes=mins_before_last)
        
        receiver_bank = random.choice(BANKS)
        receiver_acc = generate_account_number()
        receiver_ifsc = receiver_bank["ifsc"]
        
        channel = random.choice(CHANNELS)
        
        # Mule keeps a cut, so amount decreases slightly
        if i > 0:
            current_amount = current_amount * random.uniform(0.95, 0.99)
            
        txn = {
            "hop_index": i + 1,
            "txn_id": generate_txn_id(channel),
            "txn_timestamp": txn_time.isoformat(),
            "sender_account": current_sender_acc,
            "sender_ifsc": current_sender_ifsc,
            "sender_bank": current_sender_bank_name,
            "receiver_account": receiver_acc,
            "receiver_ifsc": receiver_ifsc,
            "receiver_bank": receiver_bank["name"],
            "amount_inr": round(current_amount, 2),
            "channel": channel
        }
        transactions.append(txn)
        
        current_sender_acc = receiver_acc
        current_sender_ifsc = receiver_ifsc
        current_sender_bank_name = receiver_bank["name"]
    
    # Terminal Mule is the receiver of the last hop
    terminal_mule_bank = next(b for b in BANKS if b["ifsc"] == current_sender_ifsc)
    
    card_hash = hashlib.sha256(str(random.randint(10**15, 10**16-1)).encode()).hexdigest()
    
    ip_cluster = []
    for _ in range(random.randint(1, 3)):
        ip_cluster.append({
            "ip_address": f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
            "geo_lat": random.uniform(*city["lat_range"]),
            "geo_lon": random.uniform(*city["lon_range"]),
            "asn": f"AS{random.randint(1000, 9999)}",
            "last_seen": (now - timedelta(minutes=random.randint(5, 60))).isoformat()
        })
        
    terminal_mule = {
        "mule_account_number": current_sender_acc,
        "mule_ifsc": current_sender_ifsc,
        "mule_bank": terminal_mule_bank["name"],
        "current_balance_inr": round(current_amount * random.uniform(1.0, 1.2), 2),
        "account_type": "SAVINGS",
        "linked_card_number_hash": card_hash,
        "daily_withdrawal_limit_inr": terminal_mule_bank["limit"],
        "withdrawals_today_inr": 0.0,
        "cell_tower_cluster": [],
        "ip_cluster": ip_cluster
    }

    payload = {
        "payload_version": "1.1.0",
        "payload_id": str(uuid4()),
        "ingestion_timestamp": now.isoformat(),
        "ncrp_ticket": {
            "ticket_id": f"NCRP-{now.year}-{random.randint(1000000, 9999999)}",
            "complaint_timestamp": complaint_ts.isoformat(),
            "victim_state": city["state"],
            "victim_district": city["name"],
            "fraud_type": random.choice(FRAUD_TYPES),
            "amount_inr": round(initial_amount, 2),
            "source_account": source_account_info
        },
        "fund_flow": {
            "total_hops": num_hops,
            "transactions": transactions
        },
        "terminal_mule": terminal_mule
    }
    
    return payload

def main():
    out_dir = "/Users/akshai/Developer/Synapse/synthetic"
    os.makedirs(out_dir, exist_ok=True)
    
    # Use timezone-aware UTC for strict ISO 8601 formatting with offsets
    now = datetime.now(timezone.utc)
    
    for city in CITIES:
        payload = generate_payload(city, now)
        filepath = os.path.join(out_dir, f"payload_{city['name'].lower()}.json")
        with open(filepath, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"Generated {filepath}")

if __name__ == "__main__":
    main()
