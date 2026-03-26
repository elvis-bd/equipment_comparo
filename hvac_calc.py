"""
hvac_calc.py - HVAC energy calculation using Variable Base Degree Day / bin method
with hourly TMY data.
"""

import numpy as np


# Equipment type constants
EQUIP_AC_FURNACE = "ac_furnace"
EQUIP_ASHP = "ashp"
EQUIP_MINISPLIT = "minisplit"
EQUIP_BOILER = "boiler"
EQUIP_ELECTRIC_RESISTANCE = "electric_resistance"

EQUIPMENT_TYPES = {
    EQUIP_AC_FURNACE: "Central AC + Gas Furnace",
    EQUIP_ASHP: "Air Source Heat Pump",
    EQUIP_MINISPLIT: "Mini-Split Heat Pump",
    EQUIP_BOILER: "Gas Boiler (Heating Only)",
    EQUIP_ELECTRIC_RESISTANCE: "Electric Resistance Heating",
}

EQUIPMENT_TYPE_LABELS = list(EQUIPMENT_TYPES.values())
EQUIPMENT_TYPE_KEYS = list(EQUIPMENT_TYPES.keys())


def equipment_type_from_label(label: str) -> str:
    """Convert display label to equipment type key."""
    for k, v in EQUIPMENT_TYPES.items():
        if v == label:
            return k
    return EQUIP_AC_FURNACE


def equipment_label_from_type(equip_type: str) -> str:
    """Convert equipment type key to display label."""
    return EQUIPMENT_TYPES.get(equip_type, equip_type)


def calculate_annual_energy(
    hourly_temps_f: np.ndarray,
    design_cooling_load_btuh: float,
    design_heating_load_btuh: float,
    equipment_config: dict,
    t_design_cooling: float = 95.0,
    t_design_heating: float = 15.0,
    t_balance: float = 65.0,
) -> dict:
    """
    Calculate annual HVAC energy consumption using the hourly bin method.

    For each hour, the load fraction is computed as a linear interpolation between
    the balance point temperature and the design outdoor temperature. This is the
    Variable Base Degree Day / bin method applied at hourly resolution.

    Args:
        hourly_temps_f: Array of 8760 hourly outdoor dry-bulb temps in °F
        design_cooling_load_btuh: Design cooling load in BTU/hr (at t_design_cooling)
        design_heating_load_btuh: Design heating load in BTU/hr (at t_design_heating)
        equipment_config: dict with equipment specs (see module docstring)
        t_design_cooling: Design cooling outdoor temp in °F (default 95°F)
        t_design_heating: Design heating outdoor temp in °F (default 15°F)
        t_balance: Balance point temperature in °F (default 65°F)

    Returns:
        dict with:
            'cooling_kwh': Annual cooling electricity use in kWh
            'heating_kwh': Annual heating electricity use in kWh (heat pumps)
            'heating_therms': Annual heating gas use in therms (gas equipment)
            'elec_cost': Annual electricity cost in $
            'gas_cost': Annual gas cost in $
            'total_cost': Annual total energy cost in $
            'cooling_btuh_hourly': Hourly cooling load in BTU/hr (8760 array)
            'heating_btuh_hourly': Hourly heating load in BTU/hr (8760 array)
    """
    temps = np.asarray(hourly_temps_f, dtype=float)

    equip_type = equipment_config.get("type", EQUIP_AC_FURNACE)
    seer = float(equipment_config.get("seer") or 14.0)
    hspf = float(equipment_config.get("hspf") or 8.5)
    afue = float(equipment_config.get("afue") or 0.80)
    elec_rate = float(equipment_config.get("elec_rate") or 0.13)
    gas_rate = float(equipment_config.get("gas_rate") or 1.20)

    # --- Cooling Load Calculation ---
    # Load fraction for cooling: linear ramp from 0 at balance point to 1 at design cooling temp
    # Only applies when outdoor temp > balance point
    if t_design_cooling > t_balance:
        cool_fraction = np.clip(
            (temps - t_balance) / (t_design_cooling - t_balance),
            0.0, 1.0
        )
    else:
        cool_fraction = np.zeros_like(temps)

    cooling_btuh_hourly = design_cooling_load_btuh * cool_fraction
    # Only cool when outdoor temp is above balance point
    cooling_btuh_hourly = np.where(temps > t_balance, cooling_btuh_hourly, 0.0)

    # --- Heating Load Calculation ---
    # Load fraction for heating: linear ramp from 0 at balance point to 1 at design heating temp
    # Only applies when outdoor temp < balance point
    if t_balance > t_design_heating:
        heat_fraction = np.clip(
            (t_balance - temps) / (t_balance - t_design_heating),
            0.0, 1.0
        )
    else:
        heat_fraction = np.zeros_like(temps)

    heating_btuh_hourly = design_heating_load_btuh * heat_fraction
    # Only heat when outdoor temp is below balance point
    heating_btuh_hourly = np.where(temps < t_balance, heating_btuh_hourly, 0.0)

    # --- Cooling Energy ---
    # All equipment types with cooling use SEER for cooling efficiency
    has_cooling = equip_type in (EQUIP_AC_FURNACE, EQUIP_ASHP, EQUIP_MINISPLIT)

    if has_cooling and seer > 0:
        # SEER is in BTU/Wh; convert BTU to kWh: divide by (SEER * 1000)
        cooling_kwh = float(cooling_btuh_hourly.sum() / (seer * 1000.0))
    else:
        cooling_kwh = 0.0

    # --- Heating Energy ---
    heating_kwh = 0.0
    heating_therms = 0.0

    if equip_type in (EQUIP_ASHP, EQUIP_MINISPLIT):
        # Heat pump heating: HSPF is in BTU/Wh
        if hspf > 0:
            heating_kwh = float(heating_btuh_hourly.sum() / (hspf * 1000.0))

    elif equip_type == EQUIP_AC_FURNACE:
        # Gas furnace: AFUE is 0-1 efficiency
        if afue > 0:
            # BTU to therms: 1 therm = 100,000 BTU
            heating_therms = float(heating_btuh_hourly.sum() / (afue * 100_000.0))

    elif equip_type == EQUIP_BOILER:
        # Gas boiler: AFUE is 0-1 efficiency
        if afue > 0:
            heating_therms = float(heating_btuh_hourly.sum() / (afue * 100_000.0))

    elif equip_type == EQUIP_ELECTRIC_RESISTANCE:
        # Electric resistance: COP = 1.0, convert BTU to kWh
        # 1 kWh = 3412.14 BTU
        heating_kwh = float(heating_btuh_hourly.sum() / 3412.14)

    # --- Cost Calculation ---
    elec_cost = (cooling_kwh + heating_kwh) * elec_rate
    gas_cost = heating_therms * gas_rate
    total_cost = elec_cost + gas_cost

    return {
        "cooling_kwh": cooling_kwh,
        "heating_kwh": heating_kwh,
        "heating_therms": heating_therms,
        "elec_cost": elec_cost,
        "gas_cost": gas_cost,
        "total_cost": total_cost,
        "cooling_btuh_hourly": cooling_btuh_hourly,
        "heating_btuh_hourly": heating_btuh_hourly,
    }


# Default equipment presets
DEFAULT_EQUIPMENT_PRESETS = [
    {
        "name": "Standard AC + Furnace 80%",
        "type": EQUIP_AC_FURNACE,
        "seer": 14.0,
        "hspf": 0.0,
        "afue": 0.80,
    },
    {
        "name": "High Eff AC + Furnace 96%",
        "type": EQUIP_AC_FURNACE,
        "seer": 18.0,
        "hspf": 0.0,
        "afue": 0.96,
    },
    {
        "name": "Standard Heat Pump",
        "type": EQUIP_ASHP,
        "seer": 15.0,
        "hspf": 8.5,
        "afue": 0.0,
    },
    {
        "name": "High Eff Heat Pump",
        "type": EQUIP_ASHP,
        "seer": 20.0,
        "hspf": 10.0,
        "afue": 0.0,
    },
    {
        "name": "Mini-Split",
        "type": EQUIP_MINISPLIT,
        "seer": 22.0,
        "hspf": 11.0,
        "afue": 0.0,
    },
]
