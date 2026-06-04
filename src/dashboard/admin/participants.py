from __future__ import annotations

import streamlit as st
import pandas as pd

from dashboard.auth import render_admin_login
from dashboard.app_context import get_bundle_for_session_id
from dashboard.ui_components import scenario_label, render_session_filters
from dashboard.repositories import (
    list_sessions,
    list_participants,
    create_participant,
    regenerate_participant_access_code
)

def render_admin_participants_page() -> None:
    if not render_admin_login():
        return
    
    st.title("👥 Participants and Access Codes")

    tabs = st.tabs(["Create Participant", "View Participants", "Regenerate Code"])
    with tabs[0]:
        render_create_participant()
    with tabs[1]:
        render_view_participants()
    with tabs[2]:
        render_regenerate_access_code()

def render_create_participant() -> None:
    # Render filter interface
    filters = render_session_filters(
        session_state_key="create_participant_filters",
        available_statuses=["open", "locked", "processing_ready", "completed"],
        default_statuses=["open", "locked", "processing_ready", "completed"],
    )
    
    # Apply filters
    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        require_access_code=True,  # Always require access code for participant creation
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()))
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return
    
    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    participants = list_participants(session["session_id"])

    # if not participants:
    #     st.error("There are no participants for this scenario")
    #     return

    active_count = len([
        p for p in participants
        if p["status"] not in {"excluded", "expired"}
    ])

    single_full = session["mode"] == "single_stakeholder" and active_count >= 1

    if session["mode"] == "single_stakeholder":
        st.info("Single-stakeholder session: only one active participant should be allowed.")

    if single_full:
        st.warning("This single-stakeholder session already has an active participant.")

    if session["status"] == "open" and not single_full:
        groups = {g["label"]: g for g in bundle.stakeholder_groups}

        with st.form("admin_add_participant_form"):
            st.subheader("Add Participant")
            display_name = st.text_input("Optional participant display name")
            group_label = st.selectbox("Stakeholder group", list(groups.keys()))
            default_power = float(groups[group_label].get("default_group_voting_power", 1.0))
            override_enabled = st.checkbox("Override default voting power")
            override = (
                st.number_input(
                    "Override voting power",
                    min_value=0.0,
                    value=default_power,
                    step=0.05,
                )
                if override_enabled
                else None
            )
            submitted = st.form_submit_button("Add participant", type="primary")

        if submitted:
            try:
                participant_id, code = create_participant(
                    session_id=session["session_id"],
                    stakeholder_group_id=groups[group_label]["id"],
                    default_voting_power=default_power,
                    display_name=display_name.strip() or None,
                    override_voting_power=override,
                    require_access_code=bool(session["require_access_code"]),
                )
                st.success(f"Participant created: {participant_id}")

                if code:
                    st.warning("Copy this participant access code now. It will not be shown again.")
                    st.code(code, language="text")

                st.rerun()

            except Exception as exc:
                st.error(f"Could not add participant: {exc}")

def render_view_participants() -> None:
    # Render filter interface
    filters = render_session_filters(
        session_state_key="view_participant_filters",
        available_statuses=["open", "locked", "processing_ready", "completed"],
        default_statuses=["open", "locked", "processing_ready", "completed"],
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

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()), key="view_participants")
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return
    
    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    participants = list_participants(session["session_id"])

    if not participants:
        st.info("No participants yet.")
        return
    
    st.dataframe(pd.DataFrame(participants), width="stretch")

def render_regenerate_access_code() -> None:
    # Render filter interface
    filters = render_session_filters(
        session_state_key="regen_code_participant_filters",
        available_statuses=["open", "locked", "processing_ready", "completed"],
        default_statuses=["open", "locked", "processing_ready", "completed"],
    )
    
    # Apply filters
    sessions = list_sessions(
        statuses=filters["statuses"] if filters["statuses"] else None,
        scenario_id=filters["scenario_id"],
        mode=filters["mode"],
        selected_weighting_method=filters["selected_weighting_method"],
        selected_ranking_method=filters["selected_ranking_method"],
        require_access_code=True,
        allow_resubmission=filters["allow_resubmission"],
        require_moderator_lock=filters["require_moderator_lock"],
    )

    if not sessions:
        st.info("No sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['session_id']}": s
        for s in sessions
    }

    selected = st.selectbox("Session", list(session_options.keys()), key="regen_code_participants")
    session = session_options[selected]
    bundle = get_bundle_for_session_id(session["session_id"])

    if not bundle:
        st.error("Scenario not available for this session.")
        return
    
    st.caption(
        f"Session ID: {session['session_id']} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Mode: {session['mode']} | "
        f"Status: {session['status']}"
    )

    participants = list_participants(session["session_id"])

    if not participants:
        st.info("No participants yet.")
        return

    participant_options = {
        f"{p['display_name'] or 'N/A'}": p
        for p in participants
    }

    selected_participant_label = st.selectbox(
        "Participant",
        list(participant_options.keys()),
        key="regen_participant_code_select",
    )

    target = participant_options[selected_participant_label]

    with st.container():
        st.header(f"Regenerate Access Code for {target['display_name'] or 'No display name'} ({target['participant_id']})")
        st.subheader("Current Participant Details")
        st.markdown(
            f"- **Alias**: {target['alias'] or 'N/A'}\n"
            f"- **Stakeholder Group**: {target['stakeholder_group_id']}\n"
            f"- **Status**: {target['status']}\n"
            f"- **Current code hint**: {target.get('access_code_hint') or 'N/A'}\n"
        )
    if st.button("Regenerate access code"):
        try:
            new_code = regenerate_participant_access_code(target["participant_id"])
            st.success("New access code generated. Copy it now.")
            st.code(new_code, language="text")
            st.warning("The old access code is now invalid.")
        except Exception as exc:
            st.error(f"Could not regenerate access code: {exc}")