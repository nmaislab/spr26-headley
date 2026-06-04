from __future__ import annotations

import streamlit as st

from dashboard.auth import render_admin_login
from dashboard.scenario_loader import ScenarioBundle
from dashboard.app_context import load_scenarios_cached, refresh_scenario_cache, get_bundle_for_session_id
from dashboard.ui_components import scenario_label, render_scenario_card, render_toasts, push_toast, render_session_filters
from dashboard.session_manager import SessionStateError, completion_summary, transition_session
from dashboard.repositories import create_session, list_sessions

def render_admin_sessions_page() -> None:
    if not render_admin_login():
        return
    
    st.title("🗂️ Manage Sessions")

    render_toasts()

    if st.button("Refresh scenario cache"):
        refresh_scenario_cache()
        st.success("Scenario cache cleared.")
        st.rerun()
    
    scenarios, errors = load_scenarios_cached()

    if errors:
        with st.expander("Scenario configuration errors"):
            for err in errors:
                st.error(err)

    tabs = st.tabs(["Create Session", "Lock and Unlock Session"])
    with tabs[0]:
        render_create_session(scenarios)
    with tabs[1]:
        render_lock_and_unlock_session()
    # with tabs[2]:
    #     render_edit_session()

def render_create_session(scenarios: list[ScenarioBundle]) -> None:
    if not scenarios:
        st.error("No valid scenarios found under ./scenarios. Please add at least one scenario folder with a valid scenario.json manifest.")
        return
    
    options = {scenario_label(s): s for s in scenarios}
    selected_label = st.selectbox("Scenario", list(options.keys()))
    bundle = options[selected_label]
    with st.container(border=True):
        render_scenario_card(bundle)

        methods = bundle.scenario.get("mcdm_methods", {})

        weighting_available_options = {
            "AHP": "AHP",
            "FUZZY_AHP": "Fuzzy AHP",
        }

        weighting_options = methods.get("weighting_supported", ["AHP"])
        weighting_options = [m for m in weighting_options if m in weighting_available_options]

        if not weighting_options:
            weighting_options = ["AHP"]

        default_weighting = methods.get("default_weighting", "AHP")
        default_weighting_index = (
            weighting_options.index(default_weighting)
            if default_weighting in weighting_options
            else 0
        )
        ranking_available_options = {
            "TOPSIS": "TOPSIS",
            "FUZZY_TOPSIS": "Fuzzy TOPSIS",
        }

        ranking_options = methods.get("ranking_supported", ["TOPSIS"])
        ranking_options = [m for m in ranking_options if m in ranking_available_options]

        if not ranking_options:
            ranking_options = ["TOPSIS"]

        default_ranking = methods.get("default_ranking", "TOPSIS")
        default_ranking_index = (
            ranking_options.index(default_ranking)
            if default_ranking in ranking_options
            else 0
        )

        mode_available_options = {
            "single_stakeholder": "Single Stakeholder",
            "multi_stakeholder": "Multi-Stakeholder"
        }

        st.markdown("### Create Session")
        with st.form("create_session_form"):
            session_name = st.text_input("Session name", value=f"{bundle.title} Session")
            mode = st.radio("Session mode", list(mode_available_options.keys()), format_func=mode_available_options.get, horizontal=True)
            selected_weighting = st.selectbox(
                "Weighting method",
                weighting_options,
                format_func=weighting_available_options.get,
                index=default_weighting_index,
                help=(
                    "This controls how stakeholder preferences are converted into final criteria weights. "
                ),
            )

            selected_ranking = st.selectbox(
                "Ranking method",
                ranking_options,
                format_func=ranking_available_options.get,
                index=default_ranking_index,
            )

            aggregation_strategy_options = {
                "two_stage_by_group": "Two-Stage (By Stakeholder Group)",
                "simple": "Simple (Flat Aggregation - Deprecated)",
            }
            
            aggregation_strategy = st.radio(
                "Aggregation Strategy",
                list(aggregation_strategy_options.keys()),
                format_func=aggregation_strategy_options.get,
                index=0,
                horizontal=True,
                help=(
                    "**Two-Stage (Recommended)**: Submissions are first aggregated within each stakeholder group "
                    "(equally), then group matrices are aggregated using stakeholder group voting weights. "
                    "This ensures each group maintains proper influence regardless of member count.\n\n"
                    "**Simple (Deprecated)**: All submissions are aggregated together equally. "
                    "Not recommended for multi-stakeholder decisions."
                ),
            )

            require_access_code = st.checkbox("Require invitation/access codes", value=False)
            allow_resubmission = st.checkbox("Allow resubmission before session lock", value=False)
            require_moderator_lock = st.checkbox("Require moderator lock before processing", value=True)
            submitted = st.form_submit_button("Create polling session", type="primary")
        if submitted:
            if not session_name.strip():
                st.error("Session name is required.")
                return
            try:
                session_id = create_session(
                    bundle=bundle,
                    session_name=session_name.strip(),
                    mode=mode,
                    selected_weighting_method=selected_weighting,
                    selected_ranking_method=selected_ranking,
                    require_access_code=require_access_code,
                    allow_resubmission=allow_resubmission,
                    require_moderator_lock=require_moderator_lock,
                    aggregation_strategy=aggregation_strategy,
                    created_by="moderator",
                )
                st.success(f"Created session: {session_id}")
                st.info("Next: add participants and assign voting power.")
            except Exception as exc:
                st.error(f"Could not create session: {exc}")

def render_lock_and_unlock_session() -> None:
    # Render filter interface
    filters = render_session_filters(
        session_state_key="lock_unlock_session_filters",
        available_statuses=["open", "locked"],
        default_statuses=["open", "locked"],
    )
    
    # Apply filters
    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return
    
    sessions_options = {f"{s['session_name']} | {s['status']} | {s['session_id']}": s for s in sessions}

    selected = st.selectbox("Session", list(sessions_options.keys()))
    session = sessions_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return
    
    st.title(session['session_name'])
    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    with st.container(border=True):
        render_scenario_card(bundle)
        summary = completion_summary(session["session_id"])

        col1, col2, col3= st.columns(3)
        col1.metric("Participants", summary["total_participants"])
        col2.metric("Submitted", summary["submitted_participants"])
        col3.metric("Completion", f"{summary['completion_ratio']:.0%}")
    
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("Lock Session", width="stretch", disabled=(session['status'] == "locked")):
                try:
                    transition_session(session['session_id'], "locked")
                    # st.toast("Session successfully locked. No more submissions are allowed.")
                    push_toast("success", "Session successfully locked. No more submissions are allowed.")
                    st.rerun()
                except SessionStateError as exc:
                    st.error(str(exc))
        
        with bc2:
            if st.button("Unlock Session", width="stretch", disabled=(session['status'] == "open")):
                try:
                    transition_session(session['session_id'], "open")
                    # st.toast("Session successfully locked. No more submissions are allowed.")
                    push_toast("success", "Session successfully unlocked. Additional submissions are allowed.")
                    st.rerun()
                except SessionStateError as exc:
                    st.error(str(exc))

# def render_edit_session() -> None:
#     sessions = list_sessions(["open", "locked"])

#     if not sessions:
#         st.info("No sessions found.")
#         return
    
#     sessions_options = {f"{s['session_name']} | {s['status']} | {s['session_id']}": s for s in sessions}

#     selected = st.selectbox("Session", list(sessions_options.keys()), key="edit_session")
#     session = sessions_options[selected]
#     bundle = get_bundle_for_session_id(session["session_id"])

#     if not bundle:
#         st.error("Scenario not available for this session.")
#         return
    
#     st.title(session['session_name'])
#     st.caption(
#         f"Session ID: {session['session_id']} | "
#         f"Scenario: {scenario_label(bundle)} | "
#         f"Mode: {session['mode']} | "
#         f"Status: {session['status']}"
#     )

#     # TODO: add the ability to import