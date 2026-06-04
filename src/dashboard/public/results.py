from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from dashboard.app_context import get_bundle_for_session_id
from dashboard.repositories import (
    get_session,
    list_sessions,
    list_submissions_for_participant,
    get_submission_by_id,
    find_participant_by_access_code
)

RESULT_HISTORY_KEY = "result_submission_history"

def render_results_page() -> None:
    st.title("📊 View Submission and Results")
    st.write("Use your participant access code to view your submission status and available results.")

    browser_history = st.session_state.get(RESULT_HISTORY_KEY, [])

    tabs = st.tabs(["Previous Submissions", "Find Submission Via Access Code"])
    with tabs[0]:
        render_previous_submissions(browser_history)
    with tabs[1]:
        render_access_code_lookup()

    # if session_id and participant_id:
    #     st.success("Showing the submission from your current browser session.")
    #     # Load session/submission directly using these IDs.
    # else:
    #     st.success("No Access")
    #     # Fall back to access-code lookup.

def render_previous_submissions(history: list[dict]) -> None:
    if not history:
        st.info("No submissions are stored in the browser session yet.")
        return
    
    options = {f"{item.get('label', 'Submission')} | {item['submission_id']}": item for item in history}

    selected_label = st.selectbox(
        "Select one of your submissions",
        list(options.keys()),
        key="browser_submission_select",
    )

    selected = options[selected_label]

    # session_id = st.session_state.get("results_session_id")
    # participant_id = st.session_state.get("results_participant_id")
    # submission_id = st.session_state.get("results_submission_id")

    # Store current selection for other components.
    st.session_state["results_session_id"] = selected["session_id"]
    st.session_state["results_participant_id"] = selected["participant_id"]
    st.session_state["results_submission_id"] = selected["submission_id"]

    render_submission_result(
        session_id=selected["session_id"],
        participant_id=selected["participant_id"],
        submission_id=selected["submission_id"],
    )

def render_access_code_lookup() -> None:
    sessions = list_sessions(require_access_code=True)

    if not sessions:
        st.info("No polling sessions exist that require an access code.")
        return

    session_options = {f"{s['session_name']} | {s['status']} | {s['scenario_id']}": s for s in sessions}

    selected_session_label = st.selectbox(
        "Polling session",
        list(session_options.keys()),
        key="results_lookup_session_select",
    )

    session = session_options[selected_session_label]
    session_id = session["session_id"]

    with st.form("results_lookup_form"):
        access_code = st.text_input("Participant access code", type="password")
        submitted = st.form_submit_button("View my submissions", type="primary")

    if not submitted:
        return
    
    participant = find_participant_by_access_code(session_id, access_code)

    if not participant:
        st.error("Invalid access code for this session.")
        return

    participant_id = participant["participant_id"]

    submissions = list_submissions_for_participant(
        session_id=session_id,
        participant_id=participant_id,
        current_only=False,
    )

    if not submissions:
        st.warning("No submissions were found for this participant.")
        return

    submission_options = {f"{s['submitted_at']} | {s['submission_id']} | current={bool(s['is_current'])}": s for s in submissions}

    selected_submission_label = st.selectbox(
        "Select submission",
        list(submission_options.keys()),
        key="access_code_submission_select",
    )

    selected_submission = submission_options[selected_submission_label]

    # Add lookup result to browser history for easier switching later.
    remember_lookup_submission(
        session_id=session_id,
        participant_id=participant_id,
        submission_id=selected_submission["submission_id"],
        label=f"Lookup — {selected_submission['submission_id']}",
    )

    render_submission_result(
        session_id=session_id,
        participant_id=participant_id,
        submission_id=selected_submission["submission_id"],
    )

def remember_lookup_submission(session_id: str, participant_id: str, submission_id: str, label: str) -> None:
    history = st.session_state.setdefault(RESULT_HISTORY_KEY, [])

    existing_ids = {item["submission_id"] for item in history}

    if submission_id not in existing_ids:
        history.append(
            {
                "session_id": session_id,
                "participant_id": participant_id,
                "submission_id": submission_id,
                "label": label,
            }
        )

    st.session_state[RESULT_HISTORY_KEY] = history
    st.session_state["results_session_id"] = session_id
    st.session_state["results_participant_id"] = participant_id
    st.session_state["results_submission_id"] = submission_id

def render_submission_result(*, session_id: str, participant_id: str, submission_id: str) -> None:
    session = get_session(session_id)
    submission = get_submission_by_id(submission_id)
    bundle = get_bundle_for_session_id(session_id)

    if not session:
        st.error("The session for this submission could not be found.")
        return

    if session["status"] == "open":
        st.error("The session is still open")
        return
    
    if not submission:
        st.error("The selected submission could not be found.")
        return

    if submission["participant_id"] != participant_id:
        st.error("The selected submission does not belong to this participant.")
        return

    if bundle:
        st.subheader(bundle.title)
        st.write(bundle.scenario.get("summary", ""))

    with st.container(border=True):
        st.header("Submission Details")
        st.write(f"Session status: **{session['status']}**")
        st.write(f"Submission ID: `{submission_id}`")
        st.write(f"Submitted at: `{submission['submitted_at']}`")
        st.write(f"Current submission version: **{bool(submission['is_current'])}**")

        raw_preferences = json.loads(submission["raw_preferences_json"])
        transformed = json.loads(submission["transformed_preferences_json"])

        with st.expander("Raw preferences", expanded=False):
            st.json(raw_preferences)

        with st.expander("Numeric scores and generated weights", expanded=False):
            numeric_scores = transformed.get("numeric_scores", {})
            normalized = transformed.get("normalized_direct_weights", {})

            if numeric_scores:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Criterion": criterion,
                                "Numeric Score": score,
                                "Direct Normalized Weight": normalized.get(criterion),
                            }
                            for criterion, score in numeric_scores.items()
                        ]
                    ),
                    width="stretch",
                )
            else:
                st.info("No numeric scores found for this submission.")

        with st.expander("Generated AHP pairwise matrix", expanded=False):
            matrix_payload = (
                transformed.get("ahp_pairwise_matrix")
                or transformed.get("rating_derived_pairwise_matrix")
            )

            if not matrix_payload:
                st.info("No pairwise matrix found for this submission.")
            else:
                criteria_order = matrix_payload["criteria_order"]
                matrix = matrix_payload["matrix"]
                matrix_df = pd.DataFrame(
                    matrix,
                    index=criteria_order,
                    columns=criteria_order,
                )
                st.dataframe(matrix_df.round(4), width="stretch")
        
        # TODO: Add submission results and ai explanation.
        # TODO: Add the ability to export submission data