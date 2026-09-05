"""
core/graph.py — Stage 1: Terminal Mule Isolation & ATM Viability Filter

PRD Reference:
- §4.2 Stage 1 — Terminal Mule Isolation (NetworkX DAG Traversal)
- §1.3 Golden Hour Scope
- [v1.2 FIX 1D] Bounded Exponential Decay for MPS Recency:
    MPS(v) = w1 * (A_v / A_max) + w2 * exp(-μ * (T_now - T_v)) + w3 * 𝟙[match]
    where w1=0.30, w2=0.30, w3=0.40, μ=0.1 min^-1 (half-life ≈ 7 min).
- [v1.1 FIX 3B] Mule Viability Filter:
    (1) linked_card_number_hash IS NOT NULL
    (2) account_type IN ('SAVINGS', 'BASIC_SAVINGS_BD', 'UNKNOWN')
    (3) mule_bank NOT IN NON_ATM_BANKS exclusion list
"""

import math
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any, Set
from pydantic import BaseModel, Field
import networkx as nx

from api.schemas import FundFlow, TerminalMule, AccountType, PaymentChannel

logger = logging.getLogger("synapse.core.graph")

# Hyperparameters & Constants per PRD §4.2 and Assumptions.MD
MPS_WEIGHT_AMOUNT: float = 0.30
MPS_WEIGHT_RECENCY: float = 0.30
MPS_WEIGHT_MATCH: float = 0.40
MPS_RECENCY_DECAY_MU: float = 0.1  # min^-1 (half-life ≈ 7 minutes)

# Permitted account types for ATM cash-out
VALID_ATM_ACCOUNT_TYPES: Set[AccountType] = {
    AccountType.SAVINGS,
    AccountType.BASIC_SAVINGS_BD,
    AccountType.UNKNOWN,
}

# Non-ATM Banks Exclusion List per PRD §4.2 & Assumptions.MD
NON_ATM_BANKS: Set[str] = {
    "Paytm Payments Bank",
    "Fino Payments Bank",
    "Airtel Payments Bank",
    "Jio Payments Bank",
    "India Post Payments Bank",
}

NON_ATM_IFSC_PREFIXES: Set[str] = {
    "PYTM",  # Paytm Payments Bank
    "FINO",  # Fino Payments Bank
    "AIRP",  # Airtel Payments Bank
    "JIOP",  # Jio Payments Bank
    "IPOS",  # India Post Payments Bank
}


class LeafCandidate(BaseModel):
    """Evaluation metrics and MPS components for an isolated leaf node in the fund flow DAG."""
    account_number: str
    bank_name: str
    ifsc: str
    total_incoming_amount_inr: float
    latest_txn_timestamp: datetime
    amount_ratio: float
    recency_score: float
    match_score: float
    mps_score: float
    is_declared_mule: bool


class Stage1Result(BaseModel):
    """Structured output for Stage 1 execution."""
    selected_mule_account: str
    selected_mule_bank: str
    selected_mule_ifsc: str
    mps_score: float
    is_viable: bool
    status: str  # "VIABLE_ATM_MULE" | "NO_VIABLE_ATM_MULE"
    disqualification_reason: Optional[str] = None
    mismatch_warning: bool = False
    warning_message: Optional[str] = None
    leaf_candidates: List[LeafCandidate] = Field(default_factory=list)
    total_leaf_nodes: int = 0
    graph_stats: Dict[str, Any] = Field(default_factory=dict)


def is_non_atm_bank(bank_name: str, ifsc: str) -> bool:
    """Check if bank or IFSC belongs to the NON_ATM_BANKS exclusion list."""
    if not bank_name and not ifsc:
        return False
    
    # Check IFSC prefix (first 4 characters)
    clean_ifsc = ifsc.strip().upper() if ifsc else ""
    if len(clean_ifsc) >= 4 and clean_ifsc[:4] in NON_ATM_IFSC_PREFIXES:
        return True

    # Check bank name exact and substring match
    clean_bank = bank_name.strip().lower() if bank_name else ""
    for non_atm_bank in NON_ATM_BANKS:
        target = non_atm_bank.lower()
        if target in clean_bank or clean_bank in target:
            return True
        # Check tokenized sub-brands
        if "paytm" in clean_bank or "fino" in clean_bank or "airtel" in clean_bank or "india post" in clean_bank or "ippb" in clean_bank:
            return True

    return False


def check_atm_viability(
    account_number: str,
    bank_name: str,
    ifsc: str,
    account_type: Optional[AccountType],
    linked_card_number_hash: Optional[str]
) -> Tuple[bool, Optional[str]]:
    """
    Enforce PRD §4.2 Mule Viability Filter:
    1. Card Linkage: linked_card_number_hash IS NOT NULL and non-empty.
    2. Account Type: Must be in ('SAVINGS', 'BASIC_SAVINGS_BD', 'UNKNOWN').
    3. Bank ATM Capability: Bank / IFSC must not be in NON_ATM_BANKS.

    Returns:
        (is_viable: bool, disqualification_reason: Optional[str])
    """
    # 1. Card Linkage Check
    if not linked_card_number_hash or not linked_card_number_hash.strip():
        reason = (
            f"Card Linkage Check Failed: Mule account {account_number} has no active debit card "
            "(linked_card_number_hash is null/empty). Cash-out cannot occur via ATM terminal."
        )
        logger.info(reason)
        return False, reason

    # 2. Account Type Check
    if account_type is None or account_type not in VALID_ATM_ACCOUNT_TYPES:
        reason = (
            f"Account Type Check Failed: Account {account_number} type '{account_type}' is ineligible for routine "
            "ATM debit card cash-outs (only SAVINGS, BASIC_SAVINGS_BD, or UNKNOWN permitted)."
        )
        logger.info(reason)
        return False, reason
    
    if account_type == AccountType.UNKNOWN:
        logger.warning(
            f"Account {account_number} type is UNKNOWN; admitted with benefit-of-doubt per PRD §4.2."
        )

    # 3. Bank ATM Capability Check
    if is_non_atm_bank(bank_name, ifsc):
        reason = (
            f"Bank ATM Capability Check Failed: Bank '{bank_name}' / IFSC '{ifsc}' is in the NON_ATM_BANKS "
            "exclusion list (payments bank or entity without ATM cash withdrawal infrastructure)."
        )
        logger.info(reason)
        return False, reason

    return True, None


def isolate_terminal_mule(
    fund_flow: FundFlow,
    terminal_mule: TerminalMule,
    current_time: Optional[datetime] = None
) -> Stage1Result:
    """
    Constructs fund flow DiGraph, isolates leaf nodes, calculates MPS using [v1.2 FIX 1D],
    cross-references against CFCFRMS declared mule, and applies ATM viability filter.
    """
    if not fund_flow.transactions:
        raise ValueError("Cannot perform Stage 1 isolation on an empty fund flow transaction list.")

    # 1. Graph Construction
    G = nx.DiGraph()
    for tx in fund_flow.transactions:
        G.add_edge(
            tx.sender_account,
            tx.receiver_account,
            txn_id=tx.txn_id,
            txn_timestamp=tx.txn_timestamp,
            amount_inr=tx.amount_inr,
            channel=tx.channel.value if hasattr(tx.channel, "value") else str(tx.channel),
            hop_index=tx.hop_index,
            sender_bank=tx.sender_bank,
            sender_ifsc=tx.sender_ifsc,
            receiver_bank=tx.receiver_bank,
            receiver_ifsc=tx.receiver_ifsc,
        )

    # Graph statistics
    graph_stats = {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "is_dag": nx.is_directed_acyclic_graph(G),
    }

    # 2. Leaf Node Identification (out-degree == 0)
    leaf_nodes = [node for node, out_deg in G.out_degree() if out_deg == 0]
    if not leaf_nodes:
        raise ValueError("Malformed fund flow graph: No leaf nodes (out-degree 0) detected.")

    # Reference Time T_now for recency calculation
    t_now = current_time if current_time is not None else datetime.now(timezone.utc)

    # 3. Collect Raw Metrics for Each Leaf Node
    raw_candidates = []
    for leaf in leaf_nodes:
        in_edges = list(G.in_edges(leaf, data=True))
        total_amount = sum(edge_data["amount_inr"] for _, _, edge_data in in_edges)
        latest_edge = max(in_edges, key=lambda e: e[2]["txn_timestamp"])
        latest_ts = latest_edge[2]["txn_timestamp"]

        # Reconcile bank and IFSC
        if leaf == terminal_mule.mule_account_number:
            bank = terminal_mule.mule_bank
            ifsc = terminal_mule.mule_ifsc
        else:
            bank = latest_edge[2].get("receiver_bank", "UNKNOWN_BANK")
            ifsc = latest_edge[2].get("receiver_ifsc", "UNKNOWN_IFSC")

        raw_candidates.append({
            "account_number": leaf,
            "bank_name": bank,
            "ifsc": ifsc,
            "total_amount": total_amount,
            "latest_ts": latest_ts,
            "is_declared": (leaf == terminal_mule.mule_account_number)
        })

    # A_max across all leaf candidates
    A_max = max(c["total_amount"] for c in raw_candidates)

    # 4. Compute Mule Probability Score (MPS) per candidate
    leaf_candidates: List[LeafCandidate] = []
    for c in raw_candidates:
        # (a) Amount Ratio term: A_v / A_max
        amount_ratio = (c["total_amount"] / A_max) if A_max > 0.0 else 0.0

        # (b) Recency term: exp(-μ * Δt) where Δt in minutes [v1.2 FIX 1D]
        ref_now = t_now
        ts = c["latest_ts"]
        if ref_now.tzinfo and not ts.tzinfo:
            ref_now = ref_now.replace(tzinfo=None)
        elif not ref_now.tzinfo and ts.tzinfo:
            ref_now = ref_now.astimezone(ts.tzinfo)

        delta_seconds = (ref_now - ts).total_seconds()
        delta_minutes = max(0.0, delta_seconds / 60.0)
        recency_score = math.exp(-MPS_RECENCY_DECAY_MU * delta_minutes)

        # (c) Match term: 𝟙[match]
        match_score = 1.0 if c["is_declared"] else 0.0

        # Composite MPS
        mps_raw = (
            MPS_WEIGHT_AMOUNT * amount_ratio
            + MPS_WEIGHT_RECENCY * recency_score
            + MPS_WEIGHT_MATCH * match_score
        )
        mps_bounded = min(1.0, max(0.0, mps_raw))

        leaf_candidates.append(
            LeafCandidate(
                account_number=c["account_number"],
                bank_name=c["bank_name"],
                ifsc=c["ifsc"],
                total_incoming_amount_inr=round(c["total_amount"], 2),
                latest_txn_timestamp=c["latest_ts"],
                amount_ratio=round(amount_ratio, 4),
                recency_score=round(recency_score, 4),
                match_score=round(match_score, 4),
                mps_score=round(mps_bounded, 4),
                is_declared_mule=c["is_declared"],
            )
        )

    # Sort leaf candidates descending by MPS score
    leaf_candidates.sort(key=lambda x: x.mps_score, reverse=True)
    top_candidate = leaf_candidates[0]

    # 5. Cross-Reference Validation & Mismatch Warning
    mismatch_warning = False
    warning_message = None
    if top_candidate.account_number != terminal_mule.mule_account_number:
        mismatch_warning = True
        warning_message = (
            f"MULE_MISMATCH_WARNING: DAG-derived terminal mule ({top_candidate.account_number}, MPS={top_candidate.mps_score:.4f}) "
            f"diverges from payload-declared terminal mule ({terminal_mule.mule_account_number})."
        )
        logger.warning(warning_message)

    # 6. Apply Mule Viability Filter
    # Attribute mapping: If the selected candidate matches the payload-declared mule, use declared credentials.
    # If it diverges, evaluate available graph metadata and check for lack of card credentials.
    if top_candidate.account_number == terminal_mule.mule_account_number:
        cand_account_type = terminal_mule.account_type
        cand_card_hash = terminal_mule.linked_card_number_hash
        cand_bank = terminal_mule.mule_bank
        cand_ifsc = terminal_mule.mule_ifsc
    else:
        cand_account_type = AccountType.UNKNOWN
        cand_card_hash = None  # No card linkage provided for undeclared node
        cand_bank = top_candidate.bank_name
        cand_ifsc = top_candidate.ifsc

    is_viable, disqualification_reason = check_atm_viability(
        account_number=top_candidate.account_number,
        bank_name=cand_bank,
        ifsc=cand_ifsc,
        account_type=cand_account_type,
        linked_card_number_hash=cand_card_hash,
    )

    status = "VIABLE_ATM_MULE" if is_viable else "NO_VIABLE_ATM_MULE"

    return Stage1Result(
        selected_mule_account=top_candidate.account_number,
        selected_mule_bank=cand_bank,
        selected_mule_ifsc=cand_ifsc,
        mps_score=top_candidate.mps_score,
        is_viable=is_viable,
        status=status,
        disqualification_reason=disqualification_reason,
        mismatch_warning=mismatch_warning,
        warning_message=warning_message,
        leaf_candidates=leaf_candidates,
        total_leaf_nodes=len(leaf_candidates),
        graph_stats=graph_stats,
    )
