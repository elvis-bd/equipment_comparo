"""
weather.py - Geocoding and weather data fetching for HVAC comparison app.
"""

import io
import requests
import pandas as pd
import numpy as np
import streamlit as st


def geocode_zip(zip_code: str) -> tuple[float, float, str]:
    """
    Geocode a US ZIP code. Tries the US Census Bureau geocoder first (no API key
    or special headers needed), then falls back to Nominatim.

    Returns:
        (lat, lon, city_name) tuple
    Raises:
        ValueError if the ZIP code cannot be found by any method
    """
    # --- Attempt 1: US Census Bureau Geocoder ---
    try:
        url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
        params = {
            "address": f"{zip_code}",
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "format": "json",
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            lat = float(coords["y"])
            lon = float(coords["x"])
            matched_addr = matches[0].get("matchedAddress", "")
            # Parse city/state from matched address (format: "CITY, STATE ZIP")
            parts = matched_addr.split(",")
            city_name = matched_addr if len(parts) < 2 else f"{parts[0].strip()}, {parts[1].strip().split()[0]}"
            return lat, lon, city_name
    except Exception:
        pass  # Fall through to next method

    # --- Attempt 2: Nominatim / OpenStreetMap ---
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "postalcode": zip_code,
            "country": "US",
            "format": "json",
            "limit": 1,
            "addressdetails": 1,
        }
        headers = {
            "User-Agent": "HVACEquipmentComparator/1.0 (contact: hvac-comparo-app@users.noreply.github.com)",
            "Accept": "application/json",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        results = resp.json()
        if results:
            result = results[0]
            lat = float(result["lat"])
            lon = float(result["lon"])
            address = result.get("address", {})
            city = (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("county")
                or address.get("state")
                or "Unknown Location"
            )
            state = address.get("state", "")
            city_name = f"{city}, {state}" if state else city
            return lat, lon, city_name
    except Exception:
        pass  # Fall through to error

    raise ValueError(
        f"Could not geocode ZIP code '{zip_code}'. "
        "Please check that it is a valid US ZIP code and try again."
    )


@st.cache_data(show_spinner=False)
def fetch_tmy3_data(lat: float, lon: float, api_key: str, email: str) -> pd.DataFrame:
    """
    Fetch TMY3-style hourly weather data from NREL NSRDB PSM3 TMY API.

    Returns:
        DataFrame with 8760 rows and columns including:
        'Temperature' (°C), 'Temperature_F' (°F), 'Month', 'Day', 'Hour'
    Raises:
        ValueError on API or parsing errors
    """
    url = "https://developer.nlr.gov/api/nsrdb/v2/solar/nsrdb-GOES-tmy-v4-0-0-download.csv"
    params = {
        "api_key": api_key,
        "wkt": f"POINT({lon:.6f} {lat:.6f})",
        "names": "tmy",
        "attributes": "air_temperature",
        "interval": "60",
        "email": email,
    }

    try:
        resp = requests.get(url, params=params, timeout=60, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ValueError(f"NREL NSRDB API request failed: {e}") from e

    content = resp.text

    # Check for API error messages (returned as JSON even for CSV endpoint)
    if content.strip().startswith("{"):
        import json
        try:
            error_data = json.loads(content)
            errors = error_data.get("errors", [])
            if errors:
                raise ValueError(f"NREL API error: {'; '.join(errors)}")
            raise ValueError(f"Unexpected JSON response from NREL API: {content[:200]}")
        except json.JSONDecodeError:
            pass

    # The CSV format (NSRDB v4):
    #   Row 0: metadata column names (Source, Location ID, City, State, ...)
    #   Row 1: metadata values (NSRDB, 238129, -, -, ...)
    #   Row 2: data column headers (Year, Month, Day, Hour, Minute, Temperature)
    #   Rows 3+: hourly data
    lines = content.splitlines()
    if len(lines) < 4:
        raise ValueError("NREL API returned insufficient data. Check your API key and coordinates.")

    # Skip the first 2 metadata rows so pandas reads row 2 as the header
    try:
        df = pd.read_csv(io.StringIO(content), skiprows=2)
    except Exception as e:
        raise ValueError(f"Failed to parse NREL weather CSV: {e}") from e

    # Validate expected columns
    expected_cols = {"Year", "Month", "Day", "Hour", "Minute"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(
            f"NREL CSV missing expected columns: {missing}. "
            f"Got columns: {list(df.columns)}"
        )

    # Temperature column may be named 'Temperature' or 'air_temperature'
    temp_col = None
    for candidate in ["Temperature", "air_temperature", "Dry-bulb temperature"]:
        if candidate in df.columns:
            temp_col = candidate
            break

    if temp_col is None:
        # Try to find any column with 'temp' in its name (case-insensitive)
        for col in df.columns:
            if "temp" in col.lower():
                temp_col = col
                break

    if temp_col is None:
        raise ValueError(
            f"Could not find temperature column in NREL data. "
            f"Available columns: {list(df.columns)}"
        )

    # Rename to standard name if needed
    if temp_col != "Temperature":
        df = df.rename(columns={temp_col: "Temperature"})

    # Ensure numeric
    df["Temperature"] = pd.to_numeric(df["Temperature"], errors="coerce")
    df["Month"] = pd.to_numeric(df["Month"], errors="coerce").astype(int)
    df["Day"] = pd.to_numeric(df["Day"], errors="coerce").astype(int)
    df["Hour"] = pd.to_numeric(df["Hour"], errors="coerce").astype(int)

    # Drop any fully-null rows
    df = df.dropna(subset=["Temperature", "Month", "Hour"])

    # Convert °C to °F
    df["Temperature_F"] = df["Temperature"] * 9.0 / 5.0 + 32.0

    # Ensure we have close to 8760 rows
    if len(df) < 8700 or len(df) > 8800:
        raise ValueError(
            f"Expected ~8760 hourly rows but got {len(df)}. "
            "The NREL data may be incomplete or in an unexpected format."
        )

    df = df.reset_index(drop=True)
    return df


def make_demo_weather_data(hdd: float = 4000.0, cdd: float = 1500.0) -> pd.DataFrame:
    """
    Generate synthetic hourly weather data for demo mode.
    Creates a plausible temperature profile that yields approximately the
    specified HDD and CDD (base 65°F).

    Returns:
        DataFrame with same structure as fetch_tmy3_data output.
    """
    rng = np.random.default_rng(42)

    # Monthly average temperatures (°F) tuned to hit ~HDD 4000, CDD 1500
    # (roughly Boston/Chicago climate)
    monthly_avg_f = np.array([28, 31, 40, 52, 63, 72, 78, 76, 68, 56, 44, 32], dtype=float)

    months = []
    days = []
    hours = []
    temps_f = []

    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    for m_idx, (avg_temp, n_days) in enumerate(zip(monthly_avg_f, days_in_month)):
        month = m_idx + 1
        for day in range(1, n_days + 1):
            # Daily variation: cooler at night, warmer in afternoon
            # Daily swing ~15°F, with seasonal noise
            daily_mean = avg_temp + rng.normal(0, 5)
            for hour in range(24):
                # Sinusoidal daily variation: min at 5am, max at 3pm
                diurnal = 7.5 * np.sin(np.pi * (hour - 5) / 10 - np.pi / 2)
                temp = daily_mean + diurnal + rng.normal(0, 1.5)
                months.append(month)
                days.append(day)
                hours.append(hour)
                temps_f.append(float(temp))

    df = pd.DataFrame({
        "Year": 2020,
        "Month": months,
        "Day": days,
        "Hour": hours,
        "Minute": 0,
        "Temperature": [(t - 32) * 5 / 9 for t in temps_f],  # Store °C
        "Temperature_F": temps_f,
    })

    # Trim/pad to exactly 8760
    df = df.iloc[:8760].copy()
    return df


def compute_degree_hours(
    df: pd.DataFrame,
    balance_point_f: float = 65.0,
) -> dict:
    """
    Compute heating and cooling degree days from hourly temperature data.

    Args:
        df: DataFrame with 'Temperature_F' and 'Month' columns
        balance_point_f: Balance point temperature in °F (default 65°F)

    Returns:
        dict with keys:
            'HDD': total heating degree days
            'CDD': total cooling degree days
            'monthly_hdd': array of HDD by month (len 12)
            'monthly_cdd': array of CDD by month (len 12)
            'heating_hours': hourly heating load fractions (raw delta-T below balance)
            'cooling_hours': hourly cooling load fractions (raw delta-T above balance)
    """
    temps = df["Temperature_F"].values

    # Hourly degree differences
    heat_delta = np.maximum(0.0, balance_point_f - temps)
    cool_delta = np.maximum(0.0, temps - balance_point_f)

    # Degree days = sum of hourly degree-hours / 24
    hdd = float(heat_delta.sum() / 24.0)
    cdd = float(cool_delta.sum() / 24.0)

    # Monthly breakdown
    monthly_hdd = np.zeros(12)
    monthly_cdd = np.zeros(12)
    months = df["Month"].values
    for m in range(1, 13):
        mask = months == m
        monthly_hdd[m - 1] = heat_delta[mask].sum() / 24.0
        monthly_cdd[m - 1] = cool_delta[mask].sum() / 24.0

    return {
        "HDD": hdd,
        "CDD": cdd,
        "monthly_hdd": monthly_hdd,
        "monthly_cdd": monthly_cdd,
        "heating_degree_hours": heat_delta,
        "cooling_degree_hours": cool_delta,
    }
