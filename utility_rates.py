"""
utility_rates.py - Fetch utility rate data from NREL OpenEI API.
"""

import requests
import streamlit as st

# Default gas rate when API doesn't provide one
DEFAULT_GAS_RATE = 1.20  # $/therm (EIA national average ~2024)
DEFAULT_ELEC_RATE = 0.13  # $/kWh fallback


@st.cache_data(show_spinner=False)
def fetch_utility_rates(lat: float, lon: float, api_key: str) -> dict:
    """
    Fetch utility rates from NREL OpenEI Utility Rates API.

    Args:
        lat: Latitude
        lon: Longitude
        api_key: NREL API key

    Returns:
        dict with keys:
            'residential_rate_elec': float ($/kWh)
            'residential_rate_gas': float ($/therm)
            'utility_name': str
            'source': str (description of data source)
    """
    url = "https://developer.nrel.gov/api/utility_rates/v3.json"
    params = {
        "api_key": api_key,
        "lat": lat,
        "lon": lon,
        "format": "json",
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        return _fallback_rates(reason=f"API request failed: {e}")

    # Check for API-level errors
    if "error" in data:
        return _fallback_rates(reason=data["error"].get("message", "Unknown API error"))

    outputs = data.get("outputs", {})
    if not outputs:
        return _fallback_rates(reason="No outputs in API response")

    # Extract electricity rate
    elec_rate = outputs.get("residential")
    if elec_rate is None or elec_rate == 0:
        elec_rate = DEFAULT_ELEC_RATE
        elec_source = "default (EIA average)"
    else:
        elec_rate = float(elec_rate)
        elec_source = "NREL OpenEI"

    # Gas rate: OpenEI utility rates API doesn't reliably provide gas rates.
    # Use EIA national average as default.
    gas_rate = DEFAULT_GAS_RATE
    gas_source = "default (EIA national average)"

    # Extract utility name
    utility_name = outputs.get("utility_name", "Unknown Utility")

    return {
        "residential_rate_elec": elec_rate,
        "residential_rate_gas": gas_rate,
        "utility_name": utility_name,
        "elec_source": elec_source,
        "gas_source": gas_source,
        "source": "NREL OpenEI API",
        "error": None,
    }


def _fallback_rates(reason: str = "") -> dict:
    """Return default rates when API is unavailable."""
    return {
        "residential_rate_elec": DEFAULT_ELEC_RATE,
        "residential_rate_gas": DEFAULT_GAS_RATE,
        "utility_name": "Unknown (using defaults)",
        "elec_source": "default (EIA average)",
        "gas_source": "default (EIA national average)",
        "source": "Defaults (API unavailable)",
        "error": reason,
    }
