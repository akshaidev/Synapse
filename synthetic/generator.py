import json
import os
import sys
import math
import random
import argparse
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import hashlib

# Extensive pool of realistic Indian complainant names across diverse regions
COMPLAINANT_NAMES = [
    "Rajesh Kumar", "Priya Sharma", "Anil Mehta", "Sunita Verma", "Vikram Singh",
    "Meena Nair", "Suresh Patel", "Kavitha Reddy", "Deepak Joshi", "Lakshmi Iyer",
    "Amit Gupta", "Rekha Yadav", "Rahul Bose", "Anita Pillai", "Manoj Tiwari",
    "Seema Desai", "Ravi Shankar", "Pooja Agarwal", "Sanjay Mishra", "Nandini Rao",
    "Vijay Kulkarni", "Geeta Bhatt", "Harish Choudhury", "Usha Narayanan", "Kishore Das",
    "Arjun Dasgupta", "Bhavna Chawla", "Tanvi Sengupta", "Rohan Mukherjee", "Shweta Kadam",
    "Gaurav Saxena", "Divya Menon", "Pradeep Nambiar", "Swati Hegde", "Alok Sengupta",
    "Aditi Deshmukh", "Nitin Kamat", "Rashmi Marathe", "Kunal Banerjee", "Preeti Jain",
    "Siddharth Rawat", "Neha Kapoor", "Girish Madhavan", "Ananya Sundaram", "Kartik Venkat",
    "Shalini Trivedi", "Manish Pandey", "Sneha Kulkarni", "Tarun Bhasin", "Richa Varma",
    "Hemant Soni", "Madhavi Pillai", "Vikas Shekhawat", "Aarti Goswami", "Brijesh Maurya"
]

# ONLY cities present in data/atm_registry.json (Pune, Bengaluru, Delhi)
# Coordinates tightly focused around high-density ATM clusters in the registry
CITIES = [
    {"name": "Pune", "state": "MH", "prefix": "PNE", "lat_range": (18.48, 18.62), "lon_range": (73.78, 73.92)},
    {"name": "Bengaluru", "state": "KA", "prefix": "BLR", "lat_range": (12.90, 13.04), "lon_range": (77.54, 77.68)},
    {"name": "Delhi", "state": "DL", "prefix": "DEL", "lat_range": (28.58, 28.72), "lon_range": (77.16, 77.30)},
]

BANKS = [
    {"name": "Canara Bank", "ifsc": "CNRB0002341", "limit": 100000.0},
    {"name": "State Bank of India", "ifsc": "SBIN0011424", "limit": 100000.0},
    {"name": "HDFC Bank", "ifsc": "HDFC0001729", "limit": 200000.0},
    {"name": "Punjab National Bank", "ifsc": "PUNB0187600", "limit": 100000.0},
    {"name": "ICICI Bank", "ifsc": "ICIC0000100", "limit": 200000.0},
    {"name": "Bank of Baroda", "ifsc": "BARB0VJPUNE", "limit": 100000.0},
    {"name": "Axis Bank", "ifsc": "UTIB0002583", "limit": 200000.0},
    {"name": "Kotak Mahindra Bank", "ifsc": "KKBK0000958", "limit": 200000.0},
    {"name": "IndusInd Bank", "ifsc": "INDB0000001", "limit": 200000.0},
]

CHANNELS = ["UPI", "IMPS", "NEFT", "RTGS"]
FRAUD_TYPES = [
    "UPI_FRAUD", "VISHING", "PHISHING", "SIM_SWAP",
    "INVESTMENT_SCAM", "LOAN_APP_FRAUD"
]

def generate_account_number():
    return str(random.randint(10**10, 10**14 - 1))

def generate_txn_id(channel):
    return f"{channel}{datetime.now().strftime('%Y%m%d')}{random.randint(1000, 9999)}"

def generate_payload(city=None, now=None):
    if now is None:
        now = datetime.now(timezone.utc)
    if city is None:
        city = random.choice(CITIES)

    num_hops = random.randint(2, 6)
    initial_amount = round(random.uniform(45000, 750000), 2)

    # 1. Terminal Mule Setup & Calibration for Rich Interception Window
    terminal_mule_bank = random.choice(BANKS)
    daily_limit = terminal_mule_bank["limit"]
    # 25% of cases have prior ATM withdrawals today, 75% have full limit available
    withdrawn_today = random.choice([10000.0, 20000.0, 30000.0, 40000.0]) if random.random() < 0.25 else 0.0
    balance = round(initial_amount * random.uniform(1.05, 1.35), 2)

    remaining_daily_cap = max(0.0, daily_limit - withdrawn_today)
    accessible = min(balance, remaining_daily_cap)
    if accessible < 20000.0:
        accessible = 40000.0
        withdrawn_today = 0.0

    # Total cash-out session duration per PRD §4.3: N_w * 4.5 min
    num_withdrawals = math.ceil(accessible / 20000.0)
    total_session_minutes = num_withdrawals * 4.5

    # Target remaining interception window: varied smoothly between 25% and 88% of session time
    # Guarantees a realistic, unique, actionable countdown for every case (e.g. 5.5 min to 38.0 min)
    target_drain_remaining = round(random.uniform(0.25, 0.88) * total_session_minutes, 1)
    tau = round(total_session_minutes - target_drain_remaining, 2)
    tau = max(1.5, tau)

    # In simulation mode or live mode, last_hop_time is exactly tau minutes before now
    last_hop_time = now - timedelta(minutes=tau)

    # Complaint filed shortly after last transaction, strictly before now
    complaint_delay = min(tau * 0.7, round(random.uniform(1.0, 3.2), 2))
    complaint_ts = last_hop_time + timedelta(minutes=complaint_delay)

    # 2. Sequential Transactions
    victim_bank = random.choice(BANKS)
    current_sender_acc = generate_account_number()
    current_sender_ifsc = victim_bank["ifsc"]
    current_sender_bank_name = victim_bank["name"]

    source_account_info = {
        "account_number": current_sender_acc,
        "ifsc": current_sender_ifsc,
        "bank_name": current_sender_bank_name
    }

    terminal_mule_acc = generate_account_number()
    transactions = []
    current_amount = initial_amount

    for i in range(num_hops):
        # Spaced out 2.5 to 6.5 minutes per hop before last_hop_time
        mins_before_last = (num_hops - 1 - i) * round(random.uniform(2.5, 6.5), 2)
        txn_time = last_hop_time - timedelta(minutes=mins_before_last)

        if i == num_hops - 1:
            # Terminal hop
            receiver_acc = terminal_mule_acc
            receiver_bank_name = terminal_mule_bank["name"]
            receiver_ifsc = terminal_mule_bank["ifsc"]
        else:
            inter_bank = random.choice(BANKS)
            receiver_acc = generate_account_number()
            receiver_bank_name = inter_bank["name"]
            receiver_ifsc = inter_bank["ifsc"]

        channel = random.choice(CHANNELS)
        if i > 0:
            # Mule siphon deduction: 1.5% to 4.5% skim
            current_amount = round(current_amount * random.uniform(0.955, 0.985), 2)

        txn = {
            "hop_index": i + 1,
            "txn_id": generate_txn_id(channel),
            "txn_timestamp": txn_time.isoformat(),
            "sender_account": current_sender_acc,
            "sender_ifsc": current_sender_ifsc,
            "sender_bank": current_sender_bank_name,
            "receiver_account": receiver_acc,
            "receiver_ifsc": receiver_ifsc,
            "receiver_bank": receiver_bank_name,
            "amount_inr": current_amount,
            "channel": channel
        }
        transactions.append(txn)

        current_sender_acc = receiver_acc
        current_sender_ifsc = receiver_ifsc
        current_sender_bank_name = receiver_bank_name

    # 3. Terminal Mule Credentials (Always viable for ATM cash-out)
    card_hash = hashlib.sha256(str(random.randint(10**15, 10**16 - 1)).encode()).hexdigest()
    acct_type = random.choice(["SAVINGS", "SAVINGS", "SAVINGS", "BASIC_SAVINGS_BD"])

    ip_cluster = []
    for _ in range(random.randint(1, 3)):
        ip_cluster.append({
            "ip_address": f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
            "geo_lat": round(random.uniform(*city["lat_range"]), 4),
            "geo_lon": round(random.uniform(*city["lon_range"]), 4),
            "asn": f"AS{random.randint(1000, 9999)}",
            "last_seen": (now - timedelta(minutes=random.randint(5, 45))).isoformat()
        })

    terminal_mule = {
        "mule_account_number": terminal_mule_acc,
        "mule_ifsc": terminal_mule_bank["ifsc"],
        "mule_bank": terminal_mule_bank["name"],
        "current_balance_inr": balance,
        "account_type": acct_type,
        "linked_card_number_hash": card_hash,
        "daily_withdrawal_limit_inr": daily_limit,
        "withdrawals_today_inr": withdrawn_today,
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
            "complainant_name": random.choice(COMPLAINANT_NAMES),
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
    parser = argparse.ArgumentParser(description="Generate randomized synthetic Synapse incident payloads.")
    parser.add_argument("--count", "-c", type=int, default=None, help="Number of payloads to generate (default: prompts user or 50)")
    parser.add_argument("--outdir", "-o", type=str, default=None, help="Output directory (default: synthetic/)")
    args = parser.parse_args()

    count = args.count
    if count is None:
        if sys.stdin.isatty():
            try:
                user_input = input("Enter number of payloads to generate [default: 50]: ").strip()
                count = int(user_input) if user_input else 50
            except (ValueError, EOFError, KeyboardInterrupt):
                count = 50
        else:
            count = 50

    if count <= 0:
        count = 50

    base_dir = args.outdir or os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base_dir, exist_ok=True)

    now = datetime.now(timezone.utc)
    print(f"\n[SYNAPSE GENERATOR] Generating {count} randomized payload(s) into: {base_dir}")

    for idx in range(1, count + 1):
        city = random.choice(CITIES)
        payload = generate_payload(city, now)
        filename = f"payload_{idx}.json"
        filepath = os.path.join(base_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        ticket = payload["ncrp_ticket"]["ticket_id"]
        complainant = payload["ncrp_ticket"]["complainant_name"]
        amt = payload["ncrp_ticket"]["amount_inr"]
        hops = payload["fund_flow"]["total_hops"]
        city_name = city["name"]
        
        # Calculate expected remaining drain time for logging
        b = payload["terminal_mule"]["current_balance_inr"]
        lim = payload["terminal_mule"]["daily_withdrawal_limit_inr"]
        w = payload["terminal_mule"]["withdrawals_today_inr"]
        acc = min(b, max(0.0, lim - w))
        nw = math.ceil(acc / 20000.0)
        tot_sess = nw * 4.5
        last_t = datetime.fromisoformat(payload["fund_flow"]["transactions"][-1]["txn_timestamp"])
        tau_val = (now - last_t).total_seconds() / 60.0
        rem_drain = max(0.0, tot_sess - tau_val)
        
        print(f"  [{idx:02d}/{count:02d}] {filename} -> {ticket} | {complainant:<18} | ₹{amt:>9,.2f} | {city_name:<9} | {hops} hops | Win: {rem_drain:.1f}m")

    print(f"\n[SUCCESS] Generated {count} payloads: payload_1.json through payload_{count}.json\n")

if __name__ == "__main__":
    main()
