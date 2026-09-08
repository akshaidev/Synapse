import json
import os
import sys
import math
import random
import hashlib
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from pathlib import Path

# Add project root to sys.path for schema validation
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.schemas import IncidentPayload, FraudType, PaymentChannel, AccountType

COMPLAINANT_NAMES = [
    "Rajesh Kumar", "Priya Sharma", "Anil Mehta", "Sunita Verma", "Vikram Singh",
    "Meena Nair", "Suresh Patel", "Kavitha Reddy", "Deepak Joshi", "Lakshmi Iyer",
    "Amit Gupta", "Rekha Yadav", "Rahul Bose", "Anita Pillai", "Manoj Tiwari",
    "Seema Desai", "Ravi Shankar", "Pooja Agarwal", "Sanjay Mishra", "Nandini Rao",
    "Vijay Kulkarni", "Geeta Bhatt", "Harish Choudhury", "Usha Narayanan", "Kishore Das",
    "Arjun Dasgupta", "Bhavna Chawla", "Tanvi Sengupta", "Rohan Mukherjee", "Shweta Kadam"
]

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

def generate_account_number():
    return str(random.randint(10**10, 10**14 - 1))

def generate_txn_id(channel):
    return f"{channel}{datetime.now().strftime('%Y%m%d')}{random.randint(1000, 9999)}"

def prompt_user(field_name, default_display, parser=None):
    """
    Prompt user for a field.
    Pressing Enter returns None (indicating random/default should be chosen).
    """
    prompt_str = f"  -> {field_name} [Press Enter for random/default: '{default_display}']: "
    try:
        val = input(prompt_str).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)

    if not val:
        return None
    if parser:
        try:
            return parser(val)
        except Exception as e:
            print(f"     ⚠️ Invalid value '{val}' ({e}). Falling back to random default.")
            return None
    return val

def build_custom_payload():
    print("=" * 75)
    print("  PROJECT SYNAPSE — CUSTOM INCIDENT PAYLOAD GENERATOR")
    print("  Configure each field or press [Enter] to auto-randomize.")
    print("=" * 75)

    now = datetime.now(timezone.utc)

    # 1. City / Target Region
    rand_city = random.choice(CITIES)
    city_names = [c["name"] for c in CITIES]
    city_input = prompt_user("City / District", rand_city["name"])
    if city_input:
        matched_city = next((c for c in CITIES if c["name"].lower() == city_input.lower()), None)
        if matched_city:
            chosen_city = matched_city
        else:
            chosen_city = {
                "name": city_input,
                "state": "DL",
                "prefix": city_input[:3].upper(),
                "lat_range": (rand_city["lat_range"]),
                "lon_range": (rand_city["lon_range"])
            }
    else:
        chosen_city = rand_city

    # 2. Complainant Name
    rand_name = random.choice(COMPLAINANT_NAMES)
    complainant_name = prompt_user("Complainant Name", rand_name) or rand_name

    # 3. Fraud Type
    rand_fraud = random.choice(["UPI_FRAUD", "VISHING", "PHISHING", "SIM_SWAP", "INVESTMENT_SCAM", "LOAN_APP_FRAUD"])
    fraud_type_in = prompt_user("Fraud Type (UPI_FRAUD, VISHING, PHISHING, SIM_SWAP, etc.)", rand_fraud)
    if fraud_type_in and fraud_type_in.upper() in [f.value for f in FraudType]:
        fraud_type = fraud_type_in.upper()
    else:
        fraud_type = rand_fraud

    # 4. Total Amount
    rand_amount = round(random.uniform(50000, 600000), 2)
    amount_inr = prompt_user("Total Fraud Amount (INR)", f"₹{rand_amount:,.2f}", float) or rand_amount

    # 5. Source / Victim Bank
    rand_vbank = random.choice(BANKS)
    vbank_name_in = prompt_user("Victim / Source Bank Name", rand_vbank["name"])
    if vbank_name_in:
        vbank = next((b for b in BANKS if b["name"].lower() == vbank_name_in.lower()), {
            "name": vbank_name_in,
            "ifsc": "SBIN0011424",
            "limit": 100000.0
        })
    else:
        vbank = rand_vbank

    # 6. Source Account Number
    rand_vacc = generate_account_number()
    source_acc = prompt_user("Victim / Source Account Number (9-18 digits)", rand_vacc) or rand_vacc

    # 7. Total Hops
    rand_hops = random.randint(2, 6)
    num_hops = prompt_user("Number of Transaction Hops (2 to 7)", str(rand_hops), int) or rand_hops
    num_hops = max(2, min(num_hops, 7))

    # 8. Terminal Mule Bank
    rand_mbank = random.choice(BANKS)
    mbank_name_in = prompt_user("Terminal Mule Bank Name", rand_mbank["name"])
    if mbank_name_in:
        mbank = next((b for b in BANKS if b["name"].lower() == mbank_name_in.lower()), {
            "name": mbank_name_in,
            "ifsc": "CNRB0002341",
            "limit": 100000.0
        })
    else:
        mbank = rand_mbank

    # 9. Terminal Mule Account Number
    rand_macc = generate_account_number()
    mule_acc = prompt_user("Terminal Mule Account Number (9-18 digits)", rand_macc) or rand_macc

    # 10. Terminal Mule Account Type (Default to viable ATM-eligible SAVINGS)
    rand_acct_type = random.choice(["SAVINGS", "SAVINGS", "BASIC_SAVINGS_BD"])
    acct_type_in = prompt_user("Terminal Mule Account Type (SAVINGS, BASIC_SAVINGS_BD, CURRENT)", rand_acct_type)
    if acct_type_in and acct_type_in.upper() in [a.value for a in AccountType]:
        acct_type = acct_type_in.upper()
    else:
        acct_type = rand_acct_type

    # 11. Daily Limit
    rand_limit = mbank["limit"]
    daily_limit = prompt_user("Terminal Mule Daily Limit (INR)", f"₹{rand_limit:,.2f}", float) or rand_limit

    # 12. Withdrawn Today
    rand_withdrawn = 0.0
    withdrawn_today = prompt_user("Terminal Mule Amount Withdrawn Today (INR)", f"₹{rand_withdrawn:,.2f}", float) or rand_withdrawn

    # 13. Terminal Mule Balance
    rand_balance = round(amount_inr * 1.15, 2)
    mule_balance = prompt_user("Terminal Mule Balance (INR)", f"₹{rand_balance:,.2f}", float) or rand_balance

    # 14. Output File Path
    default_filename = "synthetic/payload_custom.json"
    file_path_in = prompt_user("Output File Path", default_filename) or default_filename
    if not file_path_in.endswith(".json"):
        file_path_in += ".json"

    # Calculate accessible amount and session duration for a rich interception window
    rem_cap = max(0.0, daily_limit - withdrawn_today)
    accessible = min(mule_balance, rem_cap)
    if accessible < 20000.0:
        accessible = 40000.0
    num_withdrawals = math.ceil(accessible / 20000.0)
    total_session_minutes = num_withdrawals * 4.5
    target_drain_remaining = round(random.uniform(0.35, 0.85) * total_session_minutes, 1)
    tau = round(total_session_minutes - target_drain_remaining, 2)
    tau = max(1.5, tau)

    # Build sequential transactions
    last_hop_time = now - timedelta(minutes=tau)
    complaint_delay = min(tau * 0.7, round(random.uniform(1.0, 3.0), 2))
    complaint_ts = last_hop_time + timedelta(minutes=complaint_delay)

    transactions = []
    curr_amount = amount_inr
    curr_sender = source_acc
    curr_s_ifsc = vbank["ifsc"]
    curr_s_bank = vbank["name"]

    for i in range(num_hops):
        mins_before_last = (num_hops - 1 - i) * random.randint(5, 10)
        txn_time = last_hop_time - timedelta(minutes=mins_before_last)

        if i == num_hops - 1:
            # Terminal hop
            rec_acc = mule_acc
            rec_ifsc = mbank["ifsc"]
            rec_bank = mbank["name"]
        else:
            hop_bank = random.choice(BANKS)
            rec_acc = generate_account_number()
            rec_ifsc = hop_bank["ifsc"]
            rec_bank = hop_bank["name"]

        channel = random.choice(CHANNELS)
        if i > 0:
            curr_amount = round(curr_amount * random.uniform(0.95, 0.99), 2)

        transactions.append({
            "hop_index": i + 1,
            "txn_id": generate_txn_id(channel),
            "txn_timestamp": txn_time.isoformat(),
            "sender_account": curr_sender,
            "sender_ifsc": curr_s_ifsc,
            "sender_bank": curr_s_bank,
            "receiver_account": rec_acc,
            "receiver_ifsc": rec_ifsc,
            "receiver_bank": rec_bank,
            "amount_inr": curr_amount,
            "channel": channel
        })

        curr_sender = rec_acc
        curr_s_ifsc = rec_ifsc
        curr_s_bank = rec_bank

    card_hash = hashlib.sha256(str(random.randint(10**15, 10**16 - 1)).encode()).hexdigest()

    ip_cluster = [
        {
            "ip_address": f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}",
            "geo_lat": round(random.uniform(*chosen_city["lat_range"]), 4),
            "geo_lon": round(random.uniform(*chosen_city["lon_range"]), 4),
            "asn": f"AS{random.randint(1000, 9999)}",
            "last_seen": (now - timedelta(minutes=random.randint(5, 45))).isoformat()
        }
    ]

    payload_dict = {
        "payload_version": "1.1.0",
        "payload_id": str(uuid4()),
        "ingestion_timestamp": now.isoformat(),
        "ncrp_ticket": {
            "ticket_id": f"NCRP-{now.year}-{random.randint(1000000, 9999999)}",
            "complaint_timestamp": complaint_ts.isoformat(),
            "victim_state": chosen_city["state"],
            "victim_district": chosen_city["name"],
            "complainant_name": complainant_name,
            "fraud_type": fraud_type,
            "amount_inr": amount_inr,
            "source_account": {
                "account_number": source_acc,
                "ifsc": vbank["ifsc"],
                "bank_name": vbank["name"]
            }
        },
        "fund_flow": {
            "total_hops": num_hops,
            "transactions": transactions
        },
        "terminal_mule": {
            "mule_account_number": mule_acc,
            "mule_ifsc": mbank["ifsc"],
            "mule_bank": mbank["name"],
            "current_balance_inr": mule_balance,
            "account_type": acct_type,
            "linked_card_number_hash": card_hash,
            "daily_withdrawal_limit_inr": daily_limit,
            "withdrawals_today_inr": withdrawn_today,
            "cell_tower_cluster": [],
            "ip_cluster": ip_cluster
        }
    }

    # Validate against Pydantic schema
    try:
        IncidentPayload.model_validate(payload_dict)
    except Exception as exc:
        print(f"\n❌ Schema validation failed: {exc}")
        sys.exit(1)

    # Save to disk
    out_file = os.path.abspath(file_path_in)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(payload_dict, f, indent=2)

    print("\n" + "=" * 75)
    print("  ✅ CUSTOM PAYLOAD CREATED & VALIDATED SUCCESSFULLY!")
    print("=" * 75)
    print(f"  Saved to:            {out_file}")
    print(f"  NCRP Ticket:         {payload_dict['ncrp_ticket']['ticket_id']}")
    print(f"  Complainant:         {complainant_name}")
    print(f"  City / District:     {chosen_city['name']} ({chosen_city['state']})")
    print(f"  Fraud Type:          {fraud_type}")
    print(f"  Initial Amount:      ₹{amount_inr:,.2f}")
    print(f"  Total Hops:          {num_hops} hops")
    print(f"  Victim Account:      {source_acc} ({vbank['name']})")
    print(f"  Terminal Mule:       {mule_acc} ({mbank['name']})")
    print(f"  Mule Balance:        ₹{mule_balance:,.2f} (Limit: ₹{daily_limit:,.2f}, Withdrawn: ₹{withdrawn_today:,.2f})")
    print("=" * 75 + "\n")

if __name__ == "__main__":
    build_custom_payload()
