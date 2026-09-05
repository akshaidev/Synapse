from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, constr, model_validator
from uuid import UUID

# Regex Patterns
IFSC_REGEX = r"^[A-Z]{4}0[A-Z0-9]{6}$"
ACCOUNT_REGEX = r"^[0-9]{9,18}$"
CARD_HASH_REGEX = r"^[a-f0-9]{64}$"
NCRP_REGEX = r"^NCRP-[0-9]{4}-[0-9]{7,10}$"

# Enums
class FraudType(str, Enum):
    UPI_FRAUD = "UPI_FRAUD"
    VISHING = "VISHING"
    PHISHING = "PHISHING"
    SIM_SWAP = "SIM_SWAP"
    INVESTMENT_SCAM = "INVESTMENT_SCAM"
    LOAN_APP_FRAUD = "LOAN_APP_FRAUD"
    SEXTORTION = "SEXTORTION"
    CRYPTO_FRAUD = "CRYPTO_FRAUD"
    OTHER = "OTHER"

class PaymentChannel(str, Enum):
    NEFT = "NEFT"
    RTGS = "RTGS"
    IMPS = "IMPS"
    UPI = "UPI"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"

class AccountType(str, Enum):
    SAVINGS = "SAVINGS"
    CURRENT = "CURRENT"
    BASIC_SAVINGS_BD = "BASIC_SAVINGS_BD"
    OVERDRAFT = "OVERDRAFT"
    UNKNOWN = "UNKNOWN"

class InterventionTier(str, Enum):
    PRIMARY_DIGITAL = "PRIMARY_DIGITAL"
    SECONDARY_PHYSICAL = "SECONDARY_PHYSICAL"

class HoldType(str, Enum):
    ATM_WITHDRAWAL_BLOCK = "ATM_WITHDRAWAL_BLOCK"
    ALL_CHANNELS_BLOCK = "ALL_CHANNELS_BLOCK"

class ATMBlockType(str, Enum):
    CARD_SPECIFIC_BLOCK = "CARD_SPECIFIC_BLOCK"
    FULL_TERMINAL_BLOCK = "FULL_TERMINAL_BLOCK"

class LocationMethod(str, Enum):
    CELL_TOWER_TRILATERATION = "CELL_TOWER_TRILATERATION"
    IP_GEOLOCATION = "IP_GEOLOCATION"
    COMBINED = "COMBINED"
    IFSC_BRANCH_FALLBACK = "IFSC_BRANCH_FALLBACK"

# Ingestion Sub-models
class SourceAccount(BaseModel):
    account_number: str = Field(..., pattern=ACCOUNT_REGEX)
    ifsc: str = Field(..., pattern=IFSC_REGEX)
    bank_name: str

class NCRPTicket(BaseModel):
    ticket_id: str = Field(..., pattern=NCRP_REGEX)
    complaint_timestamp: datetime
    victim_state: str = Field(..., min_length=2, max_length=2)
    victim_district: str
    fraud_type: FraudType
    amount_inr: float = Field(..., ge=0)
    source_account: SourceAccount

class Transaction(BaseModel):
    hop_index: int = Field(..., ge=1)
    txn_id: str
    txn_timestamp: datetime
    sender_account: str = Field(..., pattern=ACCOUNT_REGEX)
    sender_ifsc: str = Field(..., pattern=IFSC_REGEX)
    sender_bank: str
    receiver_account: str = Field(..., pattern=ACCOUNT_REGEX)
    receiver_ifsc: str = Field(..., pattern=IFSC_REGEX)
    receiver_bank: str
    amount_inr: float = Field(..., ge=0)
    channel: PaymentChannel

class FundFlow(BaseModel):
    total_hops: int = Field(..., ge=1, le=20)
    transactions: List[Transaction] = Field(..., min_length=1)

class CellTowerObservation(BaseModel):
    tower_id: str
    lat: float = Field(..., ge=6.0, le=37.0)
    lon: float = Field(..., ge=68.0, le=98.0)
    last_seen: datetime
    signal_strength_dbm: int = Field(..., ge=-120, le=-30)

class IPObservation(BaseModel):
    ip_address: str
    geo_lat: float = Field(..., ge=6.0, le=37.0)
    geo_lon: float = Field(..., ge=68.0, le=98.0)
    asn: str
    last_seen: datetime

class TerminalMule(BaseModel):
    mule_account_number: str = Field(..., pattern=ACCOUNT_REGEX)
    mule_ifsc: str = Field(..., pattern=IFSC_REGEX)
    mule_bank: str
    current_balance_inr: float = Field(..., ge=0)
    account_type: AccountType
    linked_card_number_hash: Optional[str] = Field(None, pattern=CARD_HASH_REGEX)
    daily_withdrawal_limit_inr: float = Field(100000.0, ge=0)
    withdrawals_today_inr: float = Field(0.0, ge=0)
    cell_tower_cluster: List[CellTowerObservation] = Field(default=[], max_length=10)
    ip_cluster: List[IPObservation] = Field(default=[], max_length=5)

# Main Ingestion Payload
class IncidentPayload(BaseModel):
    payload_version: str = Field("1.1.0")
    payload_id: UUID
    ingestion_timestamp: datetime
    ncrp_ticket: NCRPTicket
    fund_flow: FundFlow
    terminal_mule: TerminalMule

    @model_validator(mode='after')
    def validate_golden_hour_gates(self):
        # Validate self-consistency relative to the declared ingestion_timestamp.
        # Strict wall-clock validation is enforced at the API layer based on Simulation Mode.
        validate_golden_hour(self, reference_time=self.ingestion_timestamp)
        return self


def validate_golden_hour(payload: "IncidentPayload", reference_time: Optional[datetime] = None) -> None:
    """
    Validates Gate 1 (Fraud Recency <= 120 min) and Gate 2 (Payload Freshness <= 240 min).
    If reference_time is None, defaults to datetime.now(timezone.utc).
    Raises ValueError on violation:
      - GOLDEN_HOUR_EXPIRED if Gate 1 fails
      - STALE_PAYLOAD if Gate 2 fails
    """
    ref_now = reference_time or datetime.now(timezone.utc)

    txns = payload.fund_flow.transactions
    max_txn_timestamp = max(t.txn_timestamp for t in txns)

    # Normalize timezone awareness for Gate 1
    ref_1 = ref_now
    if ref_1.tzinfo and not max_txn_timestamp.tzinfo:
        ref_1 = ref_1.replace(tzinfo=None)
    elif not ref_1.tzinfo and max_txn_timestamp.tzinfo:
        ref_1 = ref_1.astimezone(max_txn_timestamp.tzinfo)

    gate1_delta = ref_1 - max_txn_timestamp
    if gate1_delta.total_seconds() > 120 * 60:
        raise ValueError(
            f"GOLDEN_HOUR_EXPIRED: Fraud recency exceeds 120 mins (Delta: {gate1_delta.total_seconds() / 60:.1f} mins)"
        )

    # Normalize timezone awareness for Gate 2
    complaint_ts = payload.ncrp_ticket.complaint_timestamp
    ref_2 = ref_now
    if ref_2.tzinfo and not complaint_ts.tzinfo:
        ref_2 = ref_2.replace(tzinfo=None)
    elif not ref_2.tzinfo and complaint_ts.tzinfo:
        ref_2 = ref_2.astimezone(complaint_ts.tzinfo)

    gate2_delta = ref_2 - complaint_ts
    if gate2_delta.total_seconds() > 240 * 60:
        raise ValueError(
            f"STALE_PAYLOAD: Complaint is older than 240 mins (Delta: {gate2_delta.total_seconds() / 60:.1f} mins)"
        )

# Webhook Sub-models
class RequestingAuthority(BaseModel):
    authority_name: str = Field("Indian Cyber Crime Coordination Centre (I4C), MHA")
    authority_code: str = Field("I4C-MHA")
    authorized_officer_id: str

class CardHold(BaseModel):
    card_number_hash: str = Field(..., pattern=CARD_HASH_REGEX)
    hold_type: HoldType
    hold_duration_minutes: int = Field(..., ge=30, le=240)
    mule_account_number: str = Field(..., pattern=ACCOUNT_REGEX)
    mule_ifsc: str = Field(..., pattern=IFSC_REGEX)

class ATMBlock(BaseModel):
    atm_id: str
    bank_name: str
    block_type: ATMBlockType
    risk_rank: int = Field(..., ge=1, le=3)
    risk_score: float = Field(..., ge=0.0, le=1.0)

class Justification(BaseModel):
    drain_time_remaining_minutes: float = Field(..., ge=0)
    drainable_today_inr: float = Field(..., ge=0)
    fund_flow_depth: int = Field(..., ge=1)
    total_amount_inr: float = Field(..., ge=0)
    mule_location_method: LocationMethod

# Main Webhook Payload
class FreezeCardATMRequest(BaseModel):
    webhook_version: str = Field("1.1.0")
    request_id: UUID
    synapse_incident_id: UUID
    ncrp_ticket_id: str = Field(..., pattern=NCRP_REGEX)
    request_timestamp: datetime
    requesting_authority: RequestingAuthority
    golden_hour_expiry: datetime
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    intervention_tier: InterventionTier
    card_hold: CardHold
    atm_blocks: List[ATMBlock] = Field(default=[], max_length=3)
    justification: Justification
    callback_url: str
