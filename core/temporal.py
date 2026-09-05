"""
core/temporal.py — Stage 2: Capped Drain Time Engine

PRD Reference:
- §4.3 Stage 2 — Drain Time Regression
- [v1.1 FIX 1B] Accessible Daily Limit Calculation:
    B_accessible = min(B, W_limit - W_today)
    If B_accessible <= 0 -> drain_time = 0.0, DAILY_LIMIT_EXHAUSTED = True.
- [v1.3 FIX 4B] Drain Time Baseline with Elapsed Time (τ) Subtraction:
    D_hat = max(0.0, ceil(B_accessible / W_txn) * Δt_mean - τ)
    where W_txn = ₹20,000, Δt_mean = 4.5 minutes, and τ = T_now - T_last_txn.
- [v1.3 FIX 4C] Critical Urgency Guard:
    if DAILY_LIMIT_EXHAUSTED:
        urgency = 0.0  # NOT 1.0!
    else:
        urgency = max(0.0, 1.0 - D_hat / 120.0)
"""

import math
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from api.schemas import FundFlow, TerminalMule

logger = logging.getLogger("synapse.core.temporal")

# Hyperparameters & Operational Constants per PRD §4.3 and Assumptions.MD
DEFAULT_TXN_WITHDRAWAL_CAP_INR: float = 20000.0  # W_txn: standard per-ATM txn limit
DEFAULT_INTER_WITHDRAWAL_INTERVAL_MINUTES: float = 4.5  # Δt_mean: calibrated mule travel + ATM session duration
GOLDEN_HOUR_WINDOW_MINUTES: float = 120.0  # Golden Hour ceiling for urgency normalization


class DrainTimeResult(BaseModel):
    """Structured output for Stage 2 Drain Time Regression."""
    drain_time_remaining_minutes: float = Field(..., ge=0.0, description="Predicted minutes remaining to cash out accessible amount")
    drainable_today_inr: float = Field(..., ge=0.0, description="Accessible balance mule can withdraw today")
    total_balance_inr: float = Field(..., ge=0.0, description="Total account balance")
    daily_limit_exhausted: bool = Field(..., description="Flag indicating if today's ATM withdrawal cap is already reached")
    total_session_minutes: float = Field(..., ge=0.0, description="Estimated total session duration before τ subtraction")
    elapsed_minutes_tau: float = Field(..., ge=0.0, description="Minutes elapsed since the last incoming fraud transaction")
    num_withdrawals_required: int = Field(..., ge=0, description="Ceil(B_accessible / W_txn)")
    urgency_score: float = Field(..., ge=0.0, le=1.0, description="Normalized urgency in [0, 1] with [v1.3 FIX 4C] guard")
    feature_vector: Dict[str, Any] = Field(default_factory=dict, description="Analytical features for explainability and ML tracking")


def compute_drain_time(
    terminal_mule: TerminalMule,
    fund_flow: FundFlow,
    current_time: Optional[datetime] = None,
    txn_cap_inr: float = DEFAULT_TXN_WITHDRAWAL_CAP_INR,
    inter_withdrawal_interval_minutes: float = DEFAULT_INTER_WITHDRAWAL_INTERVAL_MINUTES,
) -> DrainTimeResult:
    """
    Computes remaining ATM cash-out drain time per PRD §4.3 incorporating
    [v1.1 FIX 1B], [v1.3 FIX 4B], and [v1.3 FIX 4C].

    Args:
        terminal_mule: Terminal mule account details and daily limit telemetry.
        fund_flow: Fund flow transaction trail.
        current_time: Reference time T_now (defaults to UTC now).
        txn_cap_inr: Per-transaction ATM withdrawal cap (default ₹20,000).
        inter_withdrawal_interval_minutes: Average interval between withdrawals (default 4.5 min).

    Returns:
        DrainTimeResult containing remaining drain time, drainable amount, urgency score, and telemetry.
    """
    if not fund_flow.transactions:
        raise ValueError("Cannot compute drain time on empty fund flow transactions.")

    # 1. Step 2a: Accessible Daily Amount Calculation [v1.1 FIX 1B]
    B = max(0.0, terminal_mule.current_balance_inr)
    W_limit = max(0.0, terminal_mule.daily_withdrawal_limit_inr)
    W_today = max(0.0, terminal_mule.withdrawals_today_inr)

    remaining_daily_cap = max(0.0, W_limit - W_today)
    B_accessible = min(B, remaining_daily_cap)

    # 2. Elapsed Time τ (tau) Calculation [v1.3 FIX 4B]
    t_now = current_time if current_time is not None else datetime.now(timezone.utc)
    
    # Latest transaction timestamp in the fund flow (T_last_txn)
    latest_txn_ts = max(tx.txn_timestamp for tx in fund_flow.transactions)

    # Reconcile timezones if necessary
    ref_now = t_now
    ts = latest_txn_ts
    if ref_now.tzinfo and not ts.tzinfo:
        ref_now = ref_now.replace(tzinfo=None)
    elif not ref_now.tzinfo and ts.tzinfo:
        ref_now = ref_now.astimezone(ts.tzinfo)

    delta_seconds = (ref_now - ts).total_seconds()
    tau = max(0.0, delta_seconds / 60.0)

    # 3. Handle DAILY_LIMIT_EXHAUSTED or Zero Balance
    if B_accessible <= 0.0 or remaining_daily_cap <= 0.0:
        daily_limit_exhausted = True if remaining_daily_cap <= 0.0 else False
        logger.info(
            f"Mule {terminal_mule.mule_account_number}: B_accessible is ₹{B_accessible:.2f} "
            f"(Limit exhausted={daily_limit_exhausted}). Drain time set to 0.0 min."
        )

        # [v1.3 FIX 4C] Critical Urgency Guard:
        # When daily limit is exhausted, mule cannot withdraw. Urgency MUST evaluate to 0.0.
        urgency_score = 0.0

        features = {
            "accessible_balance_inr": 0.0,
            "total_balance_inr": B,
            "daily_limit_inr": W_limit,
            "withdrawals_today_inr": W_today,
            "daily_limit_utilization": (W_today / W_limit) if W_limit > 0 else 1.0,
            "per_txn_cap_inr": txn_cap_inr,
            "num_withdrawals_required": 0,
            "inter_withdrawal_interval_minutes": inter_withdrawal_interval_minutes,
            "total_session_minutes": 0.0,
            "elapsed_minutes_tau": round(tau, 2),
            "fund_flow_hops": fund_flow.total_hops,
            "hour_of_day": ref_now.hour,
        }

        return DrainTimeResult(
            drain_time_remaining_minutes=0.0,
            drainable_today_inr=0.0,
            total_balance_inr=B,
            daily_limit_exhausted=daily_limit_exhausted,
            total_session_minutes=0.0,
            elapsed_minutes_tau=round(tau, 2),
            num_withdrawals_required=0,
            urgency_score=urgency_score,
            feature_vector=features,
        )

    # 4. Step 2b & 2c: Number of Withdrawals & Analytical Baseline [v1.3 FIX 4B]
    # N_w = ceil(B_accessible / W_txn)
    num_withdrawals = math.ceil(B_accessible / txn_cap_inr)
    total_session_minutes = num_withdrawals * inter_withdrawal_interval_minutes

    # D̂_baseline = max(0.0, N_w * Δt_mean - τ)
    drain_time_remaining = max(0.0, total_session_minutes - tau)

    # 5. [v1.3 FIX 4C] Urgency Calculation with Guard
    # Urgency = max(0.0, 1.0 - drain_time_remaining / 120.0)
    urgency_score = max(0.0, min(1.0, 1.0 - (drain_time_remaining / GOLDEN_HOUR_WINDOW_MINUTES)))

    # Compute fund flow velocity: INR / min flow rate
    first_txn_ts = min(tx.txn_timestamp for tx in fund_flow.transactions)
    span_seconds = (ts - first_txn_ts).total_seconds()
    span_minutes = max(0.01, span_seconds / 60.0)
    total_flow_amount = sum(tx.amount_inr for tx in fund_flow.transactions)
    velocity_inr_per_min = total_flow_amount / span_minutes

    features = {
        "accessible_balance_inr": B_accessible,
        "total_balance_inr": B,
        "daily_limit_inr": W_limit,
        "withdrawals_today_inr": W_today,
        "daily_limit_utilization": (W_today / W_limit) if W_limit > 0 else 0.0,
        "per_txn_cap_inr": txn_cap_inr,
        "num_withdrawals_required": num_withdrawals,
        "inter_withdrawal_interval_minutes": inter_withdrawal_interval_minutes,
        "total_session_minutes": round(total_session_minutes, 2),
        "elapsed_minutes_tau": round(tau, 2),
        "fund_flow_hops": fund_flow.total_hops,
        "fund_flow_velocity_inr_per_min": round(velocity_inr_per_min, 2),
        "hour_of_day": ref_now.hour,
    }

    return DrainTimeResult(
        drain_time_remaining_minutes=round(drain_time_remaining, 1),
        drainable_today_inr=B_accessible,
        total_balance_inr=B,
        daily_limit_exhausted=False,
        total_session_minutes=round(total_session_minutes, 1),
        elapsed_minutes_tau=round(tau, 2),
        num_withdrawals_required=num_withdrawals,
        urgency_score=round(urgency_score, 4),
        feature_vector=features,
    )
