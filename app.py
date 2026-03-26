"""
app.py - HVAC Equipment Comparison Streamlit App

Compares annual energy costs and consumption across HVAC system configurations
using TMY3-style hourly weather data and utility rate data from NREL APIs.
"""

import pandas as pd
import numpy as np
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from weather import geocode_zip, fetch_tmy3_data, make_demo_weather_data, compute_degree_hours
from utility_rates import fetch_utility_rates
from hvac_calc import (
    calculate_annual_energy,
    DEFAULT_EQUIPMENT_PRESETS,
    EQUIPMENT_TYPES,
    EQUIPMENT_TYPE_LABELS,
    EQUIPMENT_TYPE_KEYS,
    equipment_type_from_label,
    equipment_label_from_type,
    EQUIP_AC_FURNACE,
    EQUIP_ASHP,
    EQUIP_MINISPLIT,
    EQUIP_BOILER,
    EQUIP_ELECTRIC_RESISTANCE,
)

st.set_page_config(
    page_title="HVAC Equipment Comparator",
    page_icon="🌡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state initialization ────────────────────────────────────────────

def init_session_state():
    if "weather_df" not in st.session_state:
        st.session_state.weather_df = None
    if "degree_hours" not in st.session_state:
        st.session_state.degree_hours = None
    if "location_info" not in st.session_state:
        st.session_state.location_info = None  # dict: lat, lon, city
    if "utility_rates" not in st.session_state:
        st.session_state.utility_rates = None
    if "demo_mode" not in st.session_state:
        st.session_state.demo_mode = False
    if "equipment_df" not in st.session_state:
        # Initialize with default presets
        st.session_state.equipment_df = pd.DataFrame([
            {
                "Name": p["name"],
                "Type": equipment_label_from_type(p["type"]),
                "SEER": p["seer"],
                "HSPF": p["hspf"],
                "AFUE (0-1)": p["afue"],
            }
            for p in DEFAULT_EQUIPMENT_PRESETS
        ])
    if "elec_rate_override" not in st.session_state:
        st.session_state.elec_rate_override = 0.13
    if "gas_rate_override" not in st.session_state:
        st.session_state.gas_rate_override = 1.20


init_session_state()

# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("HVAC Comparator")
    st.markdown("---")

    api_key = st.secrets["NREL_API_KEY"]
    email = st.secrets["NREL_EMAIL"]

    st.subheader("Location & Weather")

    zip_code = st.text_input(
        "US ZIP Code",
        placeholder="e.g. 60601",
        max_chars=10,
    )

    col_fetch, col_demo = st.columns(2)
    with col_fetch:
        fetch_clicked = st.button(
            "Fetch Data",
            use_container_width=True,
            help="Geocode ZIP, fetch TMY weather, and look up utility rates.",
        )
    with col_demo:
        demo_clicked = st.button(
            "Demo Mode",
            use_container_width=True,
            help="Use synthetic weather data — no API key needed.",
        )

    # ── Demo Mode ──
    if demo_clicked:
        with st.spinner("Loading demo data..."):
            demo_df = make_demo_weather_data(hdd=4000, cdd=1500)
            st.session_state.weather_df = demo_df
            st.session_state.degree_hours = compute_degree_hours(demo_df)
            st.session_state.location_info = {
                "lat": 41.85,
                "lon": -87.65,
                "city": "Demo City, IL (Synthetic Data)",
            }
            st.session_state.utility_rates = {
                "residential_rate_elec": 0.13,
                "residential_rate_gas": 1.20,
                "utility_name": "Demo Utility",
                "elec_source": "default",
                "gas_source": "default",
                "error": None,
            }
            st.session_state.elec_rate_override = 0.13
            st.session_state.gas_rate_override = 1.20
            st.session_state.demo_mode = True
        st.success("Demo data loaded!")

    # ── Fetch Real Data ──
    if fetch_clicked:
        if not zip_code.strip():
            st.error("Please enter a ZIP code.")
        else:
            try:
                with st.spinner(f"Geocoding ZIP {zip_code}..."):
                    lat, lon, city_name = geocode_zip(zip_code.strip())

                st.session_state.location_info = {
                    "lat": lat,
                    "lon": lon,
                    "city": city_name,
                }

                with st.spinner("Fetching TMY weather data from NREL NSRDB..."):
                    weather_df = fetch_tmy3_data(lat, lon, api_key.strip(), email.strip())
                    st.session_state.weather_df = weather_df
                    st.session_state.degree_hours = compute_degree_hours(weather_df)
                    st.session_state.demo_mode = False

                with st.spinner("Fetching utility rates from NREL OpenEI..."):
                    rates = fetch_utility_rates(lat, lon, api_key.strip())
                    st.session_state.utility_rates = rates
                    st.session_state.elec_rate_override = rates["residential_rate_elec"]
                    st.session_state.gas_rate_override = rates["residential_rate_gas"]

                st.success(f"Data loaded for {city_name}!")

            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Unexpected error: {e}")

    # ── Location & Weather Summary ──
    if st.session_state.location_info:
        info = st.session_state.location_info
        dh = st.session_state.degree_hours

        st.markdown("---")
        st.subheader("Location")
        st.write(f"**{info['city']}**")
        st.write(f"Lat: {info['lat']:.3f}, Lon: {info['lon']:.3f}")

        if dh:
            col_h, col_c = st.columns(2)
            col_h.metric("HDD (65°F)", f"{dh['HDD']:.0f}")
            col_c.metric("CDD (65°F)", f"{dh['CDD']:.0f}")

    # ── Utility Rates ──
    if st.session_state.utility_rates:
        rates = st.session_state.utility_rates
        st.markdown("---")
        st.subheader("Utility Rates")

        if rates.get("utility_name") and rates["utility_name"] != "Unknown Utility":
            st.caption(f"Utility: {rates['utility_name']}")

        if rates.get("error"):
            st.warning(f"Rate lookup note: {rates['error']}")

        st.session_state.elec_rate_override = st.number_input(
            "Electricity Rate ($/kWh)",
            min_value=0.01,
            max_value=1.00,
            value=float(st.session_state.elec_rate_override),
            step=0.01,
            format="%.3f",
        )
        st.caption(f"Source: {rates.get('elec_source', 'manual')}")

        st.session_state.gas_rate_override = st.number_input(
            "Natural Gas Rate ($/therm)",
            min_value=0.10,
            max_value=10.00,
            value=float(st.session_state.gas_rate_override),
            step=0.05,
            format="%.2f",
        )
        st.caption(f"Source: {rates.get('gas_source', 'manual')}")

    if st.session_state.demo_mode:
        st.markdown("---")
        st.info("**Demo Mode** — Synthetic weather data is being used. Results are for illustration only.")

# ── Main Content ─────────────────────────────────────────────────────────────

st.title("HVAC Equipment Comparison Tool")
st.markdown(
    "Compare annual energy usage and costs for different HVAC equipment configurations "
    "using TMY (Typical Meteorological Year) hourly weather data."
)

if not st.session_state.location_info:
    st.info(
        "**Getting started:** Enter your NREL API key, email, and ZIP code in the sidebar, "
        "then click **Fetch Data** — or click **Demo Mode** to try the app with synthetic weather data."
    )
    st.markdown("""
    **What this app does:**
    - Geocodes your ZIP code to find latitude/longitude
    - Downloads TMY3-style hourly weather data (8,760 hours) from the NREL NSRDB
    - Looks up local electricity rates from the NREL OpenEI database
    - Calculates annual heating and cooling energy for each equipment configuration
    - Compares total annual costs side-by-side

    **Free API key:** Get one at [developer.nrel.gov/signup](https://developer.nrel.gov/signup/)
    """)
    st.stop()

tab_setup, tab_results, tab_weather = st.tabs(["Setup", "Results", "Weather Details"])

# ── TAB 1: SETUP ─────────────────────────────────────────────────────────────

with tab_setup:
    st.header("Design Parameters")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Building Load")
        design_cooling_load = st.number_input(
            "Design Cooling Load (BTU/hr)",
            min_value=0,
            max_value=200_000,
            value=36_000,
            step=1_000,
            help="Peak cooling load at design outdoor temperature. "
                 "Rule of thumb: ~400 BTU/hr per sq ft for average homes.",
        )
        design_heating_load = st.number_input(
            "Design Heating Load (BTU/hr)",
            min_value=0,
            max_value=300_000,
            value=60_000,
            step=1_000,
            help="Peak heating load at design outdoor temperature. "
                 "Rule of thumb: ~20-30 BTU/hr per sq ft for well-insulated homes.",
        )
        floor_area = st.number_input(
            "Floor Area (sq ft, reference only)",
            min_value=100,
            max_value=20_000,
            value=2_000,
            step=100,
        )

        if floor_area > 0 and design_cooling_load > 0:
            st.caption(
                f"Cooling load intensity: {design_cooling_load / floor_area:.0f} BTU/hr·ft²  |  "
                f"Heating load intensity: {design_heating_load / floor_area:.0f} BTU/hr·ft²"
            )

    with col2:
        st.subheader("Design Conditions")
        t_design_cooling = st.number_input(
            "Design Cooling Outdoor Temp (°F)",
            min_value=75.0,
            max_value=120.0,
            value=95.0,
            step=1.0,
            help="Outdoor temp at which cooling design load applies. "
                 "Typically 99th percentile summer dry-bulb for your location.",
        )
        t_design_heating = st.number_input(
            "Design Heating Outdoor Temp (°F)",
            min_value=-40.0,
            max_value=45.0,
            value=15.0,
            step=1.0,
            help="Outdoor temp at which heating design load applies. "
                 "Typically 99th percentile winter dry-bulb for your location.",
        )
        t_balance = st.number_input(
            "Balance Point Temperature (°F)",
            min_value=50.0,
            max_value=75.0,
            value=65.0,
            step=1.0,
            help="Outdoor temperature at which no heating or cooling is needed. "
                 "Commonly 65°F; lower for well-insulated or heavily-occupied buildings.",
        )

    st.markdown("---")
    st.header("Equipment Configurations")
    st.markdown(
        "Define up to 6 equipment configurations to compare. "
        "Edit the table below — click a cell to modify values."
    )

    # Build type options for the dropdown
    type_options = EQUIPMENT_TYPE_LABELS

    equipment_df_edited = st.data_editor(
        st.session_state.equipment_df,
        num_rows="dynamic",
        column_config={
            "Name": st.column_config.TextColumn(
                "Equipment Name",
                width="medium",
                help="Descriptive name for this configuration.",
            ),
            "Type": st.column_config.SelectboxColumn(
                "Equipment Type",
                options=type_options,
                width="large",
                help="Select the equipment type.",
            ),
            "SEER": st.column_config.NumberColumn(
                "SEER (cooling)",
                min_value=0.0,
                max_value=50.0,
                format="%.1f",
                help="Seasonal Energy Efficiency Ratio for cooling. Leave 0 for heating-only equipment.",
            ),
            "HSPF": st.column_config.NumberColumn(
                "HSPF (heat pump htg)",
                min_value=0.0,
                max_value=20.0,
                format="%.1f",
                help="Heating Seasonal Performance Factor for heat pump heating. Leave 0 for gas equipment.",
            ),
            "AFUE (0-1)": st.column_config.NumberColumn(
                "AFUE (gas efficiency)",
                min_value=0.0,
                max_value=1.0,
                format="%.2f",
                help="Annual Fuel Utilization Efficiency for gas equipment (0.80 = 80%). Leave 0 for electric equipment.",
            ),
        },
        hide_index=True,
        use_container_width=True,
        key="equipment_editor",
    )

    # Enforce maximum 6 rows
    if len(equipment_df_edited) > 6:
        st.warning("Maximum 6 equipment configurations allowed. Extra rows will be ignored.")
        equipment_df_edited = equipment_df_edited.iloc[:6]

    st.session_state.equipment_df = equipment_df_edited

    if len(equipment_df_edited) == 0:
        st.info("Add at least one equipment row to see results.")
    else:
        st.success(f"{len(equipment_df_edited)} configuration(s) defined. Go to the **Results** tab to see the comparison.")

    # Efficiency reference table
    with st.expander("Efficiency Reference Values"):
        st.markdown("""
        | Equipment Type | Typical Range | High Efficiency |
        |---|---|---|
        | Central AC (SEER) | 14–18 | 20–25 |
        | Air Source Heat Pump (SEER) | 14–18 | 20–25 |
        | Air Source Heat Pump (HSPF) | 7.7–10 | 10–13 |
        | Mini-Split (SEER) | 18–24 | 24–33 |
        | Mini-Split (HSPF) | 9–12 | 12–14 |
        | Gas Furnace (AFUE) | 0.80 | 0.96–0.98 |
        | Gas Boiler (AFUE) | 0.82 | 0.90–0.95 |

        **SEER2 / HSPF2 note:** If you have SEER2/HSPF2 ratings (post-2023 DOE standard),
        multiply by ~1.046 to convert SEER2→SEER, or multiply HSPF2 by ~1.11 to convert HSPF2→HSPF
        before entering them above.
        """)

# ── TAB 2: RESULTS ────────────────────────────────────────────────────────────

with tab_results:
    weather_df = st.session_state.weather_df
    equip_df = st.session_state.equipment_df

    _results_ready = True
    if weather_df is None:
        st.info("Fetch weather data first using the sidebar controls.")
        _results_ready = False

    if _results_ready and (equip_df is None or len(equip_df) == 0):
        st.info("Define at least one equipment configuration in the **Setup** tab.")
        _results_ready = False

    if _results_ready:
        st.header("Annual Energy & Cost Comparison")

        hourly_temps_f = weather_df["Temperature_F"].values
        elec_rate = st.session_state.elec_rate_override
        gas_rate = st.session_state.gas_rate_override

        # Run calculations
        results = []
        errors = []

        for _, row in equip_df.iterrows():
            name = str(row.get("Name", "Unnamed")).strip()
            if not name:
                name = "Unnamed"
            equip_type_label = str(row.get("Type", EQUIPMENT_TYPE_LABELS[0]))
            equip_type = equipment_type_from_label(equip_type_label)

            config = {
                "name": name,
                "type": equip_type,
                "seer": float(row.get("SEER", 14.0) or 14.0),
                "hspf": float(row.get("HSPF", 0.0) or 0.0),
                "afue": float(row.get("AFUE (0-1)", 0.0) or 0.0),
                "elec_rate": elec_rate,
                "gas_rate": gas_rate,
            }

            # Validate config
            warning = None
            if equip_type in (EQUIP_ASHP, EQUIP_MINISPLIT) and config["hspf"] == 0:
                warning = f"{name}: HSPF is 0 — heating energy will be 0. Please enter an HSPF value."
            if equip_type == EQUIP_AC_FURNACE and config["afue"] == 0:
                warning = f"{name}: AFUE is 0 — gas heating will be 0. Please enter an AFUE value."
            if equip_type in (EQUIP_AC_FURNACE, EQUIP_ASHP, EQUIP_MINISPLIT) and config["seer"] == 0:
                warning = f"{name}: SEER is 0 — cooling energy will be 0. Please enter a SEER value."

            if warning:
                errors.append(warning)

            try:
                res = calculate_annual_energy(
                    hourly_temps_f=hourly_temps_f,
                    design_cooling_load_btuh=float(design_cooling_load),
                    design_heating_load_btuh=float(design_heating_load),
                    equipment_config=config,
                    t_design_cooling=float(t_design_cooling),
                    t_design_heating=float(t_design_heating),
                    t_balance=float(t_balance),
                )
                results.append({
                    "name": name,
                    "type_label": equip_type_label,
                    "cooling_kwh": res["cooling_kwh"],
                    "heating_kwh": res["heating_kwh"],
                    "heating_therms": res["heating_therms"],
                    "elec_cost": res["elec_cost"],
                    "gas_cost": res["gas_cost"],
                    "total_cost": res["total_cost"],
                })
            except Exception as e:
                errors.append(f"{name}: Calculation error — {e}")

        for err in errors:
            st.warning(err)

        if not results:
            st.error("No valid equipment configurations to compare.")
        else:
            results_df = pd.DataFrame(results)

            # ── Summary Table ──
            st.subheader("Summary Table")

            display_df = results_df[[
                "name", "type_label", "cooling_kwh", "heating_kwh", "heating_therms",
                "elec_cost", "gas_cost", "total_cost"
            ]].copy()
            display_df.columns = [
                "Equipment", "Type",
                "Cooling kWh", "Heating kWh", "Heating Therms",
                "Elec Cost ($)", "Gas Cost ($)", "Total Cost ($)"
            ]

            # Format numeric columns
            format_dict = {col: "{:,.0f}" for col in ["Cooling kWh", "Heating kWh", "Heating Therms"]}
            format_dict.update({col: "${:,.2f}" for col in ["Elec Cost ($)", "Gas Cost ($)", "Total Cost ($)"]})

            st.dataframe(
                display_df.style.format(format_dict).highlight_min(
                    subset=["Total Cost ($)"], color="#d4edda"
                ).highlight_max(
                    subset=["Total Cost ($)"], color="#f8d7da"
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                f"Rates used: Electricity ${elec_rate:.3f}/kWh | Natural Gas ${gas_rate:.2f}/therm. "
                "Adjust in the sidebar."
            )

            st.markdown("---")

            # ── Charts ──
            st.subheader("Annual Cost Comparison")

            names = results_df["name"].tolist()
            colors_cool = "#4292c6"
            colors_heat_elec = "#fd8d3c"
            colors_heat_gas = "#e6550d"

            # Chart 1: Stacked bar — cost breakdown
            fig_cost = go.Figure()

            cooling_costs = [r["cooling_kwh"] * elec_rate for r in results]
            heating_elec_costs = [r["heating_kwh"] * elec_rate for r in results]
            heating_gas_costs = [r["gas_cost"] for r in results]

            fig_cost.add_trace(go.Bar(
                name="Cooling (Electric)",
                x=names, y=cooling_costs,
                marker_color=colors_cool,
                text=[f"${v:,.0f}" for v in cooling_costs],
                textposition="inside",
            ))
            fig_cost.add_trace(go.Bar(
                name="Heating (Electric)",
                x=names, y=heating_elec_costs,
                marker_color=colors_heat_elec,
                text=[f"${v:,.0f}" if v > 50 else "" for v in heating_elec_costs],
                textposition="inside",
            ))
            fig_cost.add_trace(go.Bar(
                name="Heating (Gas)",
                x=names, y=heating_gas_costs,
                marker_color=colors_heat_gas,
                text=[f"${v:,.0f}" if v > 50 else "" for v in heating_gas_costs],
                textposition="inside",
            ))

            fig_cost.update_layout(
                barmode="stack",
                title="Annual Energy Cost Breakdown by Equipment",
                xaxis_title="Equipment Configuration",
                yaxis_title="Annual Cost ($)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                plot_bgcolor="white",
                height=450,
            )
            fig_cost.update_yaxes(tickprefix="$", tickformat=",")
            st.plotly_chart(fig_cost, use_container_width=True)

            # Chart 2: Total cost bar
            fig_total = go.Figure(go.Bar(
                x=names,
                y=results_df["total_cost"].tolist(),
                marker_color=[
                    "#2ca02c" if v == results_df["total_cost"].min() else "#1f77b4"
                    for v in results_df["total_cost"].tolist()
                ],
                text=[f"${v:,.0f}" for v in results_df["total_cost"].tolist()],
                textposition="outside",
            ))
            fig_total.update_layout(
                title="Total Annual Energy Cost",
                xaxis_title="Equipment Configuration",
                yaxis_title="Annual Cost ($)",
                plot_bgcolor="white",
                height=400,
            )
            fig_total.update_yaxes(tickprefix="$", tickformat=",")
            st.plotly_chart(fig_total, use_container_width=True)

            # Chart 3: Energy usage (grouped kWh + therms on secondary axis)
            st.subheader("Annual Energy Consumption")

            fig_energy = make_subplots(specs=[[{"secondary_y": True}]])

            total_kwh = [r["cooling_kwh"] + r["heating_kwh"] for r in results]
            therms = [r["heating_therms"] for r in results]

            fig_energy.add_trace(
                go.Bar(
                    name="Total Electricity (kWh)",
                    x=names, y=total_kwh,
                    marker_color="#4292c6",
                    text=[f"{v:,.0f}" for v in total_kwh],
                    textposition="outside",
                    offsetgroup=0,
                ),
                secondary_y=False,
            )

            if any(t > 0 for t in therms):
                fig_energy.add_trace(
                    go.Bar(
                        name="Gas (Therms)",
                        x=names, y=therms,
                        marker_color="#e6550d",
                        text=[f"{v:,.0f}" if v > 0 else "" for v in therms],
                        textposition="outside",
                        offsetgroup=1,
                    ),
                    secondary_y=True,
                )
                fig_energy.update_yaxes(
                    title_text="Gas Use (Therms/yr)",
                    secondary_y=True,
                    tickformat=",",
                )

            fig_energy.update_layout(
                title="Annual Energy Consumption by Equipment",
                xaxis_title="Equipment Configuration",
                barmode="group",
                plot_bgcolor="white",
                height=420,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            fig_energy.update_yaxes(
                title_text="Electricity Use (kWh/yr)",
                secondary_y=False,
                tickformat=",",
            )
            st.plotly_chart(fig_energy, use_container_width=True)

            # Chart 4: Cooling vs Heating electricity breakdown
            fig_kwh_split = go.Figure()
            fig_kwh_split.add_trace(go.Bar(
                name="Cooling kWh",
                x=names, y=[r["cooling_kwh"] for r in results],
                marker_color=colors_cool,
            ))
            fig_kwh_split.add_trace(go.Bar(
                name="Heating kWh",
                x=names, y=[r["heating_kwh"] for r in results],
                marker_color=colors_heat_elec,
            ))
            fig_kwh_split.update_layout(
                barmode="stack",
                title="Annual Electricity Use: Cooling vs. Heating",
                xaxis_title="Equipment Configuration",
                yaxis_title="kWh/year",
                plot_bgcolor="white",
                height=380,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            fig_kwh_split.update_yaxes(tickformat=",")
            st.plotly_chart(fig_kwh_split, use_container_width=True)

            # ── Savings Table ──
            if len(results) > 1:
                st.subheader("Savings vs. Baseline")
                baseline = results[0]
                savings_rows = []
                for r in results[1:]:
                    annual_savings = baseline["total_cost"] - r["total_cost"]
                    savings_rows.append({
                        "Equipment": r["name"],
                        "vs. Baseline": baseline["name"],
                        "Annual Savings ($)": annual_savings,
                        "10-yr Savings ($)": annual_savings * 10,
                        "20-yr Savings ($)": annual_savings * 20,
                    })
                savings_df = pd.DataFrame(savings_rows)
                fmt = {
                    "Annual Savings ($)": "${:,.0f}",
                    "10-yr Savings ($)": "${:,.0f}",
                    "20-yr Savings ($)": "${:,.0f}",
                }

                def color_savings(val):
                    if isinstance(val, (int, float)):
                        return "color: green" if val > 0 else ("color: red" if val < 0 else "")
                    return ""

                st.dataframe(
                    savings_df.style.format(fmt).map(
                        color_savings,
                        subset=["Annual Savings ($)", "10-yr Savings ($)", "20-yr Savings ($)"]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                st.caption(
                    "Positive values indicate savings relative to the first equipment configuration. "
                    "Does not account for equipment purchase cost or installation."
                )

# ── TAB 3: WEATHER DETAILS ────────────────────────────────────────────────────

with tab_weather:
    weather_df = st.session_state.weather_df
    if weather_df is None:
        st.info("Fetch weather data first using the sidebar controls.")
    else:
        dh = st.session_state.degree_hours
        info = st.session_state.location_info

        st.header(f"TMY Weather Data — {info['city']}")

        if st.session_state.demo_mode:
            st.info("Displaying synthetic demo weather data.")

        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        monthly_stats = []
        for m in range(1, 13):
            mask = weather_df["Month"] == m
            temps = weather_df.loc[mask, "Temperature_F"]
            monthly_stats.append({
                "Month": month_names[m - 1],
                "Min F": temps.min(),
                "Avg F": temps.mean(),
                "Max F": temps.max(),
                "HDD": dh["monthly_hdd"][m - 1],
                "CDD": dh["monthly_cdd"][m - 1],
            })

        monthly_df = pd.DataFrame(monthly_stats)

        st.subheader("Monthly Temperature Statistics")
        st.dataframe(
            monthly_df.style.format({
                "Min F": "{:.1f}",
                "Avg F": "{:.1f}",
                "Max F": "{:.1f}",
                "HDD": "{:.0f}",
                "CDD": "{:.0f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

        col_total1, col_total2 = st.columns(2)
        col_total1.metric("Total Annual HDD (base 65F)", f"{dh['HDD']:,.0f}")
        col_total2.metric("Total Annual CDD (base 65F)", f"{dh['CDD']:,.0f}")

        st.markdown("---")

        # Chart 1: Monthly Average Temperature
        fig_temp = go.Figure()
        fig_temp.add_trace(go.Scatter(
            x=month_names,
            y=monthly_df["Avg F"].tolist(),
            mode="lines+markers",
            name="Avg Temp",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=8),
        ))
        fig_temp.add_trace(go.Scatter(
            x=month_names,
            y=monthly_df["Max F"].tolist(),
            mode="lines",
            name="Monthly Max",
            line=dict(color="#d62728", width=1, dash="dash"),
            opacity=0.7,
        ))
        fig_temp.add_trace(go.Scatter(
            x=month_names,
            y=monthly_df["Min F"].tolist(),
            mode="lines",
            name="Monthly Min",
            line=dict(color="#2ca02c", width=1, dash="dash"),
            opacity=0.7,
        ))
        fig_temp.add_hline(
            y=65,
            line_dash="dot",
            line_color="gray",
            annotation_text="Balance Point (65F)",
            annotation_position="right",
        )
        fig_temp.update_layout(
            title="Monthly Temperature Profile (TMY)",
            xaxis_title="Month",
            yaxis_title="Temperature (F)",
            plot_bgcolor="white",
            height=400,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_temp, use_container_width=True)

        # Chart 2: Monthly HDD/CDD
        fig_hdd = go.Figure()
        fig_hdd.add_trace(go.Bar(
            x=month_names,
            y=monthly_df["HDD"].tolist(),
            name="HDD",
            marker_color="#4292c6",
        ))
        fig_hdd.add_trace(go.Bar(
            x=month_names,
            y=monthly_df["CDD"].tolist(),
            name="CDD",
            marker_color="#d62728",
        ))
        fig_hdd.update_layout(
            barmode="group",
            title="Monthly Heating & Cooling Degree Days (Base 65F)",
            xaxis_title="Month",
            yaxis_title="Degree Days",
            plot_bgcolor="white",
            height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_hdd, use_container_width=True)

        # Chart 3: Hourly temperature distribution
        fig_hist = px.histogram(
            weather_df,
            x="Temperature_F",
            nbins=50,
            title="Hourly Temperature Distribution (All 8,760 Hours)",
            labels={"Temperature_F": "Temperature (F)", "count": "Hours"},
            color_discrete_sequence=["#1f77b4"],
        )
        fig_hist.add_vline(
            x=65, line_dash="dash", line_color="red",
            annotation_text="65F Balance",
            annotation_position="top right",
        )
        fig_hist.update_layout(
            plot_bgcolor="white",
            height=360,
            yaxis_title="Number of Hours",
        )
        st.plotly_chart(fig_hist, use_container_width=True)

        # Download weather data
        st.markdown("---")
        with st.expander("Download Weather Data"):
            csv_data = weather_df[["Year", "Month", "Day", "Hour", "Temperature", "Temperature_F"]].to_csv(index=False)
            st.download_button(
                label="Download TMY Data as CSV",
                data=csv_data,
                file_name=f"tmy_weather_{info['city'].replace(', ', '_').replace(' ', '_')}.csv",
                mime="text/csv",
            )
