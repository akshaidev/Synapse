"""
core/cluster.py — Stage 3: Haversine Spatial Ranker & Multi-Factor ATM Risk Scoring

PRD Reference:
- §4.4 Stage 3 — ATM Identification & Ranking
- Step 3a: Priority Cascade Mule Positioning (COMBINED -> CELL_TOWER -> IP_GEOLOCATION -> IFSC_BRANCH_FALLBACK)
- Step 3b: Haversine Radius Query with Fallback Expansion (r_search = 5.0 km urban, 15.0 km rural; 1.5x expansion up to 2x)
- Step 3c: Multi-Factor ATM Risk Scoring with [v1.3 FIX 4E] r_active Proximity Normalization:
    RiskScore = β1 * D_norm + β2 * B_match + β3 * C_status + β4 * O_site + β5 * T_traffic
    where β1=0.30, β2=0.25, β3=0.20, β4=0.15, β5=0.10.
    D_norm = max(0.0, 1 - d(P, a_j) / r_active)
- Defensive Guards:
    (1) Traffic Component Zero-Division Guard: If max_traffic <= 0, T_traffic = 0.0.
    (2) Fallback IFSC Mapping: Static dictionary with sensible city/district defaults.
"""

import math
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from api.schemas import (
    TerminalMule,
    LocationMethod,
)

logger = logging.getLogger("synapse.core.cluster")

# Physical & Algorithmic Constants
EARTH_RADIUS_KM: float = 6371.0
CELL_TOWER_DECAY_LAMBDA: float = 0.05  # min^-1 temporal decay for cell observations
CELL_IP_BLEND_ALPHA: float = 0.70  # 70% cell tower + 30% IP geolocation

# Search Radii & Expansion limits
URBAN_SEARCH_RADIUS_KM: float = 5.0
RURAL_SEARCH_RADIUS_KM: float = 15.0
MAX_RADIUS_EXPANSIONS: int = 2
EXPANSION_FACTOR: float = 1.5
MIN_CANDIDATE_ATMS: int = 3

# Multi-Factor ATM Risk Score Weights per PRD §4.4 Step 3c
BETA_PROXIMITY: float = 0.30  # β1
BETA_BANK_MATCH: float = 0.25  # β2
BETA_CASH_STATUS: float = 0.20  # β3
BETA_OFFSITE: float = 0.15  # β4
BETA_TRAFFIC: float = 0.10  # β5

# Cash Replenishment Status Mapping per PRD §4.4 / Assumptions.MD
CASH_STATUS_WEIGHTS: Dict[str, float] = {
    "FULL": 1.0,
    "PARTIAL": 0.7,
    "LOW": 0.3,
    "EMPTY": 0.0,
    "UNKNOWN": 0.5,
}

# Fallback IFSC Coordinates Registry (Mock & Known Locations)
KNOWN_IFSC_COORDINATES: Dict[str, Tuple[float, float]] = {
    "CNRB0002341": (18.5204, 73.8567),  # Canara Bank, FC Road, Pune
    "HDFC0001729": (12.9352, 77.6245),  # HDFC Bank, Koramangala, Bengaluru
    "SBIN0011424": (28.6315, 77.2167),  # State Bank of India, Connaught Place, Delhi
    "PUNB0187600": (28.6328, 77.2195),  # Punjab National Bank, Delhi
    "ICIC0001234": (18.5250, 73.8500),  # ICICI Bank, Pune
    "BARB0VJSHIV": (18.5300, 73.8450),  # Bank of Baroda, Shivaji Nagar, Pune
}

# Regional / City Fallback Coordinates
CITY_FALLBACK_COORDINATES: Dict[str, Tuple[float, float]] = {
    "pune": (18.5204, 73.8567),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090),
    "mumbai": (19.0760, 72.8777),
    "hyderabad": (17.3850, 78.4867),
    "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639),
}

DEFAULT_INDIA_CENTER: Tuple[float, float] = (20.5937, 78.9629)


class RankedATM(BaseModel):
    """Ranked candidate ATM record with composite RiskScore and feature breakdown."""
    atm_id: str
    bank_name: str
    address: str
    pin_code: str
    lat: float
    lon: float
    is_onsite: bool
    daily_avg_txn_count: int
    cash_replenishment_status: str
    distance_km: float
    risk_rank: int
    risk_score: float
    proximity_score: float
    bank_match_score: float
    cash_score: float
    offsite_score: float
    traffic_score: float


class Stage3Result(BaseModel):
    """Structured output for Stage 3 execution."""
    estimated_position: Tuple[float, float]
    location_method: LocationMethod
    active_search_radius_km: float
    total_candidates_found: int
    expansion_count: int
    top_atms: List[RankedATM] = Field(default_factory=list)
    confidence_cap_applied: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Computes Great-Circle distance between two coordinates in kilometers using the Haversine formula.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    # Numerical stability clamp
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


def estimate_mule_position(
    terminal_mule: TerminalMule,
    current_time: Optional[datetime] = None,
    victim_district: Optional[str] = None,
) -> Tuple[Tuple[float, float], LocationMethod, bool]:
    """
    Step 3a: Priority Cascade Mule Position Estimation per PRD §4.4.
    Returns:
        ((lat, lon), location_method, confidence_cap_flag)
    """
    t_now = current_time if current_time is not None else datetime.now(timezone.utc)
    cell_cluster = terminal_mule.cell_tower_cluster or []
    ip_cluster = terminal_mule.ip_cluster or []

    # Priority 1: COMBINED (both cell and IP available)
    if cell_cluster and ip_cluster:
        p_cell = _compute_cell_tower_centroid(cell_cluster, t_now)
        p_ip = _compute_ip_centroid(ip_cluster)
        lat = CELL_IP_BLEND_ALPHA * p_cell[0] + (1.0 - CELL_IP_BLEND_ALPHA) * p_ip[0]
        lon = CELL_IP_BLEND_ALPHA * p_cell[1] + (1.0 - CELL_IP_BLEND_ALPHA) * p_ip[1]
        logger.info(f"Position estimated via COMBINED: ({lat:.4f}, {lon:.4f})")
        return (lat, lon), LocationMethod.COMBINED, False

    # Priority 2: CELL_TOWER_TRILATERATION
    if cell_cluster and not ip_cluster:
        p_cell = _compute_cell_tower_centroid(cell_cluster, t_now)
        logger.info(f"Position estimated via CELL_TOWER_TRILATERATION: ({p_cell[0]:.4f}, {p_cell[1]:.4f})")
        return p_cell, LocationMethod.CELL_TOWER_TRILATERATION, False

    # Priority 3: IP_GEOLOCATION (Primary MVP Path)
    if not cell_cluster and ip_cluster:
        p_ip = _compute_ip_centroid(ip_cluster)
        logger.info(f"Position estimated via IP_GEOLOCATION: ({p_ip[0]:.4f}, {p_ip[1]:.4f})")
        return p_ip, LocationMethod.IP_GEOLOCATION, False

    # Priority 4: IFSC_BRANCH_FALLBACK (Defensive Guard 2 included)
    ifsc = terminal_mule.mule_ifsc.strip().upper() if terminal_mule.mule_ifsc else ""
    if ifsc in KNOWN_IFSC_COORDINATES:
        pos = KNOWN_IFSC_COORDINATES[ifsc]
        logger.warning(f"Position fallback via known IFSC {ifsc}: {pos}")
        return pos, LocationMethod.IFSC_BRANCH_FALLBACK, True

    # District/City match fallback
    if victim_district:
        dist_key = victim_district.strip().lower()
        if dist_key in CITY_FALLBACK_COORDINATES:
            pos = CITY_FALLBACK_COORDINATES[dist_key]
            logger.warning(f"Position fallback via district '{victim_district}': {pos}")
            return pos, LocationMethod.IFSC_BRANCH_FALLBACK, True

    # Fallback to India center
    logger.error(f"Position estimation reached default fallback for IFSC {ifsc}")
    return DEFAULT_INDIA_CENTER, LocationMethod.IFSC_BRANCH_FALLBACK, True


def _compute_ip_centroid(ip_cluster: List[Any]) -> Tuple[float, float]:
    """Computes arithmetic mean of IP geolocation observations."""
    n = len(ip_cluster)
    if n == 0:
        return DEFAULT_INDIA_CENTER
    mean_lat = sum(obs.geo_lat for obs in ip_cluster) / n
    mean_lon = sum(obs.geo_lon for obs in ip_cluster) / n
    return (mean_lat, mean_lon)


def _compute_cell_tower_centroid(cell_cluster: List[Any], t_now: datetime) -> Tuple[float, float]:
    """Computes signal-strength and temporally-decayed weighted centroid of cell towers."""
    if not cell_cluster:
        return DEFAULT_INDIA_CENTER

    weights = []
    for obs in cell_cluster:
        # Power conversion: 10^(s / 10)
        power = 10.0 ** (obs.signal_strength_dbm / 10.0)

        # Elapsed time delta
        ref_now = t_now
        ts = obs.last_seen
        if ref_now.tzinfo and not ts.tzinfo:
            ref_now = ref_now.replace(tzinfo=None)
        elif not ref_now.tzinfo and ts.tzinfo:
            ref_now = ref_now.astimezone(ts.tzinfo)

        delta_minutes = max(0.0, (ref_now - ts).total_seconds() / 60.0)
        decay = math.exp(-CELL_TOWER_DECAY_LAMBDA * delta_minutes)
        weights.append(power * decay)

    total_weight = sum(weights)
    if total_weight <= 0.0:
        return _compute_ip_centroid(cell_cluster)  # Uniform fallback

    weighted_lat = sum(w * obs.lat for w, obs in zip(weights, cell_cluster)) / total_weight
    weighted_lon = sum(w * obs.lon for w, obs in zip(weights, cell_cluster)) / total_weight
    return (weighted_lat, weighted_lon)


def rank_atms(
    terminal_mule: TerminalMule,
    atm_registry: List[Dict[str, Any]],
    current_time: Optional[datetime] = None,
    victim_district: Optional[str] = None,
    is_urban: bool = True,
) -> Stage3Result:
    """
    Executes Stage 3: Mule position estimation, Haversine candidate retrieval with expansion,
    and multi-factor ATM Risk Scoring per PRD §4.4.
    """
    if not atm_registry:
        raise ValueError("ATM registry is empty. Cannot rank candidate ATMs.")

    # 1. Step 3a: Estimate Mule Position
    mule_pos, location_method, confidence_cap = estimate_mule_position(
        terminal_mule=terminal_mule,
        current_time=current_time,
        victim_district=victim_district,
    )
    mule_lat, mule_lon = mule_pos

    # 2. Step 3b: Haversine Radius Query & Fallback Expansion
    base_radius = URBAN_SEARCH_RADIUS_KM if is_urban else RURAL_SEARCH_RADIUS_KM
    r_active = base_radius
    expansion_count = 0
    candidate_records: List[Tuple[Dict[str, Any], float]] = []

    for expansion_step in range(MAX_RADIUS_EXPANSIONS + 1):
        r_active = base_radius * (EXPANSION_FACTOR ** expansion_step)
        candidate_records = []

        for atm in atm_registry:
            d_km = haversine_distance(mule_lat, mule_lon, atm["lat"], atm["lon"])
            if d_km <= r_active:
                candidate_records.append((atm, d_km))

        if len(candidate_records) >= MIN_CANDIDATE_ATMS or expansion_step == MAX_RADIUS_EXPANSIONS:
            expansion_count = expansion_step
            break

    total_candidates = len(candidate_records)

    # 3. Step 3c: Multi-Factor ATM Risk Scoring
    # Determine max daily_avg_txn_count across candidates for log-normalization
    max_daily_txns = max((atm["daily_avg_txn_count"] for atm, _ in candidate_records), default=0)

    ranked_candidates: List[RankedATM] = []
    target_bank = terminal_mule.mule_bank.strip().lower()

    for atm, d_km in candidate_records:
        # (a) Proximity D_norm with [v1.3 FIX 4E] active radius normalization & clamp
        # [v2.0 FIX] Spatial Expansion Penalty: Multiply by (base_radius / r_active)
        # to explicitly penalize ATMs found during desperate radius expansions.
        d_norm = max(0.0, 1.0 - (d_km / r_active))
        expansion_penalty = base_radius / r_active
        prox_contrib = BETA_PROXIMITY * d_norm * expansion_penalty

        # (b) Bank match B_match
        atm_bank = atm["bank_name"].strip().lower()
        is_bank_match = (atm_bank == target_bank or target_bank in atm_bank or atm_bank in target_bank)
        bank_match_val = 1.0 if is_bank_match else 0.0
        bank_contrib = BETA_BANK_MATCH * bank_match_val

        # (c) Cash status C_status
        status_key = str(atm.get("cash_replenishment_status", "UNKNOWN")).upper()
        cash_val = CASH_STATUS_WEIGHTS.get(status_key, CASH_STATUS_WEIGHTS["UNKNOWN"])
        cash_contrib = BETA_CASH_STATUS * cash_val

        # (d) Offsite preference O_site (is_onsite == False -> 1.0)
        is_offsite = not atm.get("is_onsite", False)
        offsite_val = 1.0 if is_offsite else 0.0
        offsite_contrib = BETA_OFFSITE * offsite_val

        # (e) Traffic anonymity T_traffic with Defensive Guard 1
        daily_txns = max(0, atm.get("daily_avg_txn_count", 0))
        if max_daily_txns <= 0:
            traffic_val = 0.0
        else:
            traffic_val = math.log(1.0 + daily_txns) / math.log(1.0 + max_daily_txns)
        traffic_contrib = BETA_TRAFFIC * traffic_val

        # Total RiskScore
        risk_score_raw = (
            prox_contrib
            + bank_contrib
            + cash_contrib
            + offsite_contrib
            + traffic_contrib
        )
        # Defense-in-depth clamp strictly in [0.0, 1.0]
        risk_score = min(1.0, max(0.0, risk_score_raw))

        ranked_candidates.append(
            RankedATM(
                atm_id=atm["atm_id"],
                bank_name=atm["bank_name"],
                address=atm["address"],
                pin_code=atm["pin_code"],
                lat=atm["lat"],
                lon=atm["lon"],
                is_onsite=atm["is_onsite"],
                daily_avg_txn_count=daily_txns,
                cash_replenishment_status=status_key,
                distance_km=round(d_km, 3),
                risk_rank=0,  # assigned after sorting
                risk_score=round(risk_score, 4),
                proximity_score=round(prox_contrib, 4),
                bank_match_score=round(bank_contrib, 4),
                cash_score=round(cash_contrib, 4),
                offsite_score=round(offsite_contrib, 4),
                traffic_score=round(traffic_contrib, 4),
            )
        )

    # Sort descending by risk_score, break ties by shortest distance
    ranked_candidates.sort(key=lambda a: (-a.risk_score, a.distance_km))

    # Assign 1-indexed ranks to Top 3
    top_3 = ranked_candidates[:3]
    for idx, cand in enumerate(top_3):
        cand.risk_rank = idx + 1

    return Stage3Result(
        estimated_position=(round(mule_lat, 5), round(mule_lon, 5)),
        location_method=location_method,
        active_search_radius_km=round(r_active, 2),
        total_candidates_found=total_candidates,
        expansion_count=expansion_count,
        top_atms=top_3,
        confidence_cap_applied=confidence_cap,
        details={
            "base_search_radius_km": base_radius,
            "max_traffic_candidate": max_daily_txns,
            "mule_bank_targeted": terminal_mule.mule_bank,
        },
    )
