from __future__ import annotations

import streamlit as st

from dashboard.repositories import get_session
from dashboard.scenario_loader import ScenarioBundle, discover_scenarios, get_scenario_by_key

@st.cache_data(show_spinner=False)
def load_scenarios_cached() -> tuple[list[ScenarioBundle], list[str]]:
    """
    Cached scenario discovery for all pages.

    If scenario files change while the app is running, use the admin refresh button
    to clear the cache.
    """
    return discover_scenarios("scenarios")

def refresh_scenario_cache() -> None:
    load_scenarios_cached.clear()


def get_bundle_for_session_id(session_id: str) -> ScenarioBundle | None:
    session = get_session(session_id)
    if not session:
        return None

    scenarios, _errors = load_scenarios_cached()
    scenario_key = f"{session['scenario_id']}:{session['scenario_version']}"
    return get_scenario_by_key(scenarios, scenario_key)


def get_bundle_for_scenario_key(scenario_key: str) -> ScenarioBundle | None:
    scenarios, _errors = load_scenarios_cached()
    return get_scenario_by_key(scenarios, scenario_key)