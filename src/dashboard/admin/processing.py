from __future__ import annotations

import json

import streamlit as st
import pandas as pd

from dashboard.auth import render_admin_login
from dashboard.app_context import get_bundle_for_session_id
from dashboard.ui_components import render_toasts, push_toast, render_session_filters, scenario_label
from dashboard.session_manager import SessionStateError, completion_summary, transition_session
from dashboard.preferences import extract_preference_data
from dashboard.weighting import WeightingError, compute_group_ahp_result_adaptive, compute_within_group_ahp
from dashboard.ranking import RankingError, run_topsis_for_weights
from dashboard.session_processing import SessionProcessingError, process_session_for_ai
from dashboard.repositories import (
    get_session,
    get_session_stakeholder_group_weights,
    list_sessions,
    list_participants,
    list_submissions,
    list_export_records,
)

def render_admin_session_processing_page() -> None:
    if not render_admin_login():
        return
    
    st.title("🧾 Session Processing")

    render_toasts()

    tabs = st.tabs(["Run Deterministic Processing", "View Processing Outputs"])
    with tabs[0]:
        render_process_sessions()
    with tabs[1]:
        render_view_processed_sessions()

def _parse_json_column(val):
    """Safely parse a JSON string value."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val

def render_process_sessions() -> None:
    filters = render_session_filters(
        session_state_key="process_sessions",
        available_statuses=["open", "locked", "preprocessing"],
        default_statuses=["open", "locked"]
    )

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

    with st.container(border=True):
        st.header(session["session_name"])
        st.caption(f"Mode: {session['mode']} | Session ID: {session['session_id']}")
        st.write(f"Status: `{session['status']}`")

        summary = completion_summary(session["session_id"])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Participants", summary["total_participants"])
        c2.metric("Submitted", summary["submitted_participants"])
        c3.metric("Completion", f"{summary['completion_ratio']:.0%}")
        c4.metric("Submitted Power", f"{summary['submitted_normalized_power']:.0%}")

        bc1, bc2 = st.columns(2)
        with bc1:
            if session["status"] == "open":
                if st.button("Lock session", type="primary", width='stretch'):
                    try:
                        transition_session(session["session_id"], "locked")
                        push_toast("success", "Session locked. No more submissions are allowed.")
                        st.rerun()
                    except SessionStateError as exc:
                        st.error(str(exc))
            else:
                st.button("Lock session", disabled=True, width='stretch')
        with bc2:
            can_process = session["status"] in {"locked", "processing_ready"}

            if st.button("Process Session", disabled=not can_process, width="stretch"):
                try:
                    if session["status"] == "locked":
                        transition_session(session["session_id"], "preprocessing")

                    result = process_session_for_ai(
                        bundle=bundle,
                        session_id=session["session_id"],
                        created_by="moderator",
                    )

                    transition_session(session["session_id"], "processing_ready")

                    st.session_state.last_processing_result = {
                        "status": "success",
                        "timestamp": pd.Timestamp.now().isoformat(),
                        "session_id": session["session_id"],
                        "export_id": result["export_id"],
                        "preprocessing_metadata": result["preprocessing_metadata"],
                        "group_result": result["group_result"],
                        "individual_result_count": len(result["individual_results"]),
                    }

                    push_toast(
                        "success",
                        "Session processed successfully. Decision matrix, AHP weights, TOPSIS rankings, and AI payload were generated."
                    )
                    st.rerun()
                except (SessionProcessingError, SessionStateError, Exception) as exc:
                    st.session_state.last_processing_result = {
                        "status": "failed",
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "timestamp": pd.Timestamp.now().isoformat(),
                        "session_id": session["session_id"],
                    }

                    rollback_message = ""

                    try:
                        current_session = get_session(session["session_id"])

                        if current_session and current_session["status"] == "preprocessing":
                            transition_session(session["session_id"], "locked")
                            rollback_message = "Session was rolled back to locked status."
                    except Exception as rollback_exc:
                        rollback_message = f" Rollback failed: {rollback_exc}"
                    
                    push_toast("error", f"Session processing failed: {type(exc).__name__}: {exc}.{rollback_message}")
                    push_toast("info", "Open the View Processing Outputs tab to inspect the failed run and failed step details.")
                    st.rerun()

        with st.expander("Participants"):
            participants = list_participants(session['session_id'])
            if not participants:
                st.error("No participants found for this session.")
            else:
                st.dataframe(pd.DataFrame(participants), width="stretch", hide_index=True)

        with st.expander("Aggregate Submissions"):
            submissions = list_submissions(session['session_id'])
            if not submissions:
                st.error("No submissions found for this session.")
                return
            
            agg_tabs = st.tabs([
                "Aggregate Scores by Criterion",
                "Numeric Participant Comparison",
                "Grouped Results",
                "Grouped Pairwise Matrices",
            ])

            pref_data = extract_preference_data(submissions)
            with agg_tabs[0]:
                st.subheader("Aggregate Scores")
                criteria_order = pref_data.get("criteria_order", [])
                if criteria_order and pref_data["all_stakeholders"]["numeric_scores"]:
                    agg_data = []
                    for criterion_id in criteria_order:
                        scores = pref_data["all_stakeholders"]["numeric_scores"].get(criterion_id, [])
                        weights = pref_data["all_stakeholders"]["normalized_weights"].get(criterion_id, [])

                        agg_data.append({
                            "Criterion": criterion_id,
                            "Avg Score": round(sum(scores) / len(scores), 2) if scores else 0,
                            "Min Score": min(scores) if scores else 0,
                            "Max Score": max(scores) if scores else 0,
                            "Avg Weight": round(sum(weights) / len(weights), 4) if weights else 0,
                            "# Responses": len(scores),
                        })

                    st.dataframe(pd.DataFrame(agg_data), width='stretch')
                else:
                    st.info("No aggregated data available.")

            with agg_tabs[1]:
                st.subheader("Numeric Participant Comparison")
                st.caption("All numeric scores side-by-side by stakeholder")
                criteria_order = pref_data.get("criteria_order", [])

                if criteria_order:
                    comparison_data = {}
                    for criterion_id in criteria_order:
                        comparison_data[criterion_id] = {}
                        for stakeholder_id, stakeholder_data in pref_data["by_stakeholder"].items():
                            score = stakeholder_data["numeric_scores"].get(criterion_id, "-")
                            comparison_data[criterion_id][stakeholder_id] = score
                    
                    # Transform to DataFrame format
                    rows = []
                    for criterion_id, stakeholder_scores in comparison_data.items():
                        row = {"Criterion": criterion_id}
                        row.update(stakeholder_scores)
                        rows.append(row)
                    
                    comparison_df = pd.DataFrame(rows)
                    st.dataframe(comparison_df, width='stretch')
                else:
                    st.info("No criteria data available.")
            
            with agg_tabs[2]:
                st.subheader("Grouped Results")
                st.caption("This uses the submitted stakeholder pairwise matrices and combines them using stakeholder voting power from the database.")

                try:
                    # Fetch live stakeholder group weights from database (not bundle defaults)
                    stakeholder_group_weights = get_session_stakeholder_group_weights(
                        session["session_id"]
                    )
                    
                    group_ahp = compute_group_ahp_result_adaptive(
                        submissions,
                        aggregation_strategy=session.get("aggregation_strategy", "two_stage_by_group"),
                        stakeholder_group_weights=stakeholder_group_weights,
                        weight_derivation="geometric",
                    )

                    group_weights_df = pd.DataFrame(
                        list(group_ahp["group_weights"].items()),
                        columns=["Criterion", "Group AHP Weight"],
                    )

                    st.write("**Group AHP Weights**")
                    st.dataframe(group_weights_df, width="stretch")

                    group_matrix_df = pd.DataFrame(
                        group_ahp["aggregate_pairwise_matrix"],
                        index=group_ahp["criteria_order"],
                        columns=group_ahp["criteria_order"],
                    )

                    st.write("**Aggregated Group Pairwise Matrix**")
                    st.dataframe(group_matrix_df.round(4), width="stretch")

                    metric_col_1, metric_col_2 = st.columns(2)
                    metric_col_1.metric(
                        "Group Consistency Ratio",
                        round(group_ahp["consistency_ratio"], 4),
                    )
                    metric_col_2.metric(
                        "Group Matrix Consistent?",
                        "Yes" if group_ahp["is_consistent"] else "No",
                    )

                except WeightingError as exc:
                    st.warning(f"Could not compute group AHP result: {exc}")
                except Exception as exc:
                    st.error(f"Unexpected group AHP calculation error: {exc}")

            with agg_tabs[3]:
                st.subheader("Grouped Pairwise Matrices by Stakeholder Group")
                st.caption(
                    "View aggregated AHP pairwise matrices grouped by stakeholder group. "
                    "Each group uses power-weighted geometric mean aggregation of individual stakeholder matrices."
                )

                grouped_submissions = _group_submissions_by_stakeholder_group(submissions)

                if not grouped_submissions:
                    st.info("No submissions available for grouping.")
                else:
                    for stakeholder_group in sorted(grouped_submissions.keys()):
                        type_submissions = grouped_submissions[stakeholder_group]
                        group_result = _compute_group_ahp_for_type(type_submissions)

                        with st.container(border=True):
                            st.markdown(f"#### {stakeholder_group}")

                            if group_result is None:
                                st.warning(f"Could not compute group AHP for {stakeholder_group}.")
                                continue

                            # Metrics
                            mc1, mc2, mc3, mc4 = st.columns(4)
                            mc1.metric("Submissions", len(type_submissions))
                            mc2.metric(
                                "Total Power",
                                f"{group_result['stakeholder_powers'].__len__()} stakeholders",
                            )
                            mc3.metric(
                                "Consistency Ratio",
                                _safe_round(group_result.get("consistency_ratio"), 4),
                            )
                            mc4.metric(
                                "Consistent?",
                                "Yes" if group_result.get("is_consistent") else "No",
                            )

                            # View toggle
                            view_type = st.radio(
                                "Matrix View",
                                ["Table", "Heatmap"],
                                horizontal=True,
                                key=f"group_matrix_view_{stakeholder_group}",
                            )

                            criteria_order = group_result.get("criteria_order", [])
                            agg_matrix = group_result.get("aggregate_pairwise_matrix", [])

                            if view_type == "Table":
                                st.markdown("**Aggregated Pairwise Matrix (Table)**")
                                matrix_df = pd.DataFrame(
                                    agg_matrix,
                                    index=criteria_order,
                                    columns=criteria_order,
                                )
                                st.dataframe(
                                    matrix_df.round(4),
                                    width="stretch",
                                    column_config={
                                        col: st.column_config.NumberColumn(col, format="%.4f")
                                        for col in matrix_df.columns
                                    },
                                )
                            else:
                                st.markdown("**Aggregated Pairwise Matrix (Heatmap)**")
                                _render_pairwise_matrix_heatmap(
                                    agg_matrix,
                                    criteria_order,
                                    f"{stakeholder_group} Pairwise Matrix",
                                )

                            # Weights from this group
                            st.markdown("**Group Weights**")
                            weights_dict = group_result.get("group_weights", {})
                            weights_df = pd.DataFrame(
                                [
                                    {"Criterion": crit, "Weight": float(weight)}
                                    for crit, weight in weights_dict.items()
                                ]
                            ).sort_values("Weight", ascending=False)

                            st.dataframe(
                                weights_df,
                                width="stretch",
                                hide_index=True,
                                column_config={
                                    "Weight": st.column_config.ProgressColumn(
                                        "Weight",
                                        min_value=0,
                                        max_value=1,
                                        format="%.4f",
                                    )
                                },
                            )

        with st.expander("Individual Submissions"):
            submissions = list_submissions(session['session_id'])
            if not submissions:
                st.error("No submissions found for this session.")
                return
            
            df = pd.DataFrame(submissions)

            JSON_COLS = ["raw_preferences_json", "transformed_preferences_json"]
            SUMMARY_COLS = [c for c in df.columns if c not in JSON_COLS]

            st.caption(f"{len(df)} submission(s) — select a row to inspect JSON details.")

            # Summary Table
            event = st.dataframe(
                df[SUMMARY_COLS],
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
            )

            selected_rows = event.selection.rows
            if not selected_rows:
                st.info("⬆️ Click a row above to view its preference details.", icon="ℹ️")
                return

            row_idx = selected_rows[0]
            row = df.iloc[row_idx]

            # Detail Panel
            st.divider()
            st.markdown(
                f"**Submission:** `{row.get('submission_id', row_idx)}`  "
                f"&nbsp;|&nbsp; **Participant:** `{row.get('participant_id', '—')}`  "
                f"&nbsp;|&nbsp; **Method:** `{row.get('preference_method', '—')}`  "
                f"&nbsp;|&nbsp; **Status:** `{row.get('validation_status', '—')}`"
            )

            present_json_cols = [c for c in JSON_COLS if c in df.columns]
            if not present_json_cols:
                return
            
            tabs = st.tabs([col.replace("_", " ").title() for col in present_json_cols])

            for tab, col in zip(tabs, present_json_cols):
                with tab:
                    parsed = _parse_json_column(row.get(col))
                    if parsed is None:
                        st.caption("No data.")
                    elif isinstance(parsed, dict):
                        _render_parsed_json(parsed)
                    else:
                        st.json(parsed)

def _render_parsed_json(data: dict) -> None:
    """Render a parsed JSON dict with special handling for known structures."""

    # ── Raw preferences: flat key→label dict ───────────────────────────────────
    if all(isinstance(v, str) for v in data.values()):
        st.dataframe(
            pd.DataFrame(
                [{"Criterion": k, "Rating": v} for k, v in data.items()]
            ),
            width="stretch",
            hide_index=True,
        )
        return

    # ── Transformed preferences: rich nested structure ─────────────────────────
    # Numeric scores summary
    if "numeric_scores" in data and "normalized_direct_weights" in data:
        scores = data["numeric_scores"]
        weights = data["normalized_direct_weights"]
        fuzzy = data.get("fuzzy_linguistic_values", {})

        summary_rows = []
        for criterion in scores:
            fuzzy_vals = fuzzy.get(criterion, [None, None, None])
            summary_rows.append({
                "Criterion": criterion,
                "Score": scores[criterion],
                "Weight": round(weights.get(criterion, 0), 4),
                "Fuzzy (l, m, u)": (
                    f"({fuzzy_vals[0]:.2f}, {fuzzy_vals[1]:.2f}, {fuzzy_vals[2]:.2f})"
                    if len(fuzzy_vals) == 3 else "—"
                ),
            })

        st.markdown("**Criterion Scores & Weights**")
        st.dataframe(
            pd.DataFrame(summary_rows),
            width="stretch",
            hide_index=True,
            column_config={
                "Weight": st.column_config.ProgressColumn(
                    "Weight", min_value=0, max_value=1, format="%.4f"
                ),
            },
        )

    # AHP pairwise matrix
    if "ahp_pairwise_matrix" in data:
        ahp = data["ahp_pairwise_matrix"]
        criteria = ahp.get("criteria_order", [])
        matrix = ahp.get("matrix", [])
        if criteria and matrix:
            st.markdown("**AHP Pairwise Matrix**")
            st.dataframe(
                pd.DataFrame(matrix, index=criteria, columns=criteria).round(4),
                width="stretch",
            )
            if desc := ahp.get("description"):
                st.caption(desc)

    # Remaining top-level keys as raw JSON
    skip = {"numeric_scores", "normalized_direct_weights",
            "fuzzy_linguistic_values", "ahp_pairwise_matrix"}
    leftover = {k: v for k, v in data.items() if k not in skip}
    if leftover:
        with st.expander("Remaining fields (raw JSON)"):
            st.json(leftover)

# ── CHANGE 2: Updated to handle locked sessions with failed/missing processing ──

def render_view_processed_sessions() -> None:
    filters = render_session_filters(
        session_state_key="view_processed_sessions",
        available_statuses=["locked", "processing_ready", "completed"],
        default_statuses=["locked", "processing_ready", "completed"],
    )

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
        st.info("No processed sessions found matching the selected filters.")
        return

    session_options = {
        f"{s['session_name']} | {s['status']} | {s['scenario_id']} | {s['session_id']}": s
        for s in sessions
    }

    selected_session_label = st.selectbox(
        "Processed Session",
        list(session_options.keys()),
        key="view_processed_session_select",
    )

    session = session_options[selected_session_label]
    session_id = session["session_id"]
    bundle = get_bundle_for_session_id(session_id)

    if session["status"] != "processing_ready":
        st.warning(
            "This session has a saved processing output, but the session status is not "
            "`processing_ready`. The previous run may have partially succeeded before an error."
        )
        
    if not bundle:
        st.error("Scenario not available for this session.")
        return

    exports = list_export_records(session_id)

    processing_exports = [
        export for export in exports
        if export.get("export_type") == "ai_interpretation_input"
    ]

    # Sessions that are locked but have no successful processing export may have
    # failed processing attempts. Show a dedicated view for those cases.
    if not processing_exports:
        _render_unprocessed_session_view(
            session=session,
            bundle=bundle,
            all_exports=exports,
        )
        return

    export_options = {
        _format_export_option(export): export
        for export in processing_exports
    }

    selected_export_label = st.selectbox(
        "Processing Output Export",
        list(export_options.keys()),
        key="view_processed_export_select",
    )

    export_record = export_options[selected_export_label]
    payload = _parse_json_column(export_record.get("payload_json"))

    if not isinstance(payload, dict):
        st.error("The selected export payload could not be parsed as JSON.")
        return

    decision_matrix_df = _decision_matrix_from_payload(payload)
    group_result = _get_nested(payload, ["mcdm_results", "group_result"], default={})
    individual_results = _get_nested(payload, ["mcdm_results", "individual_results"], default=[])
    preprocessing = payload.get("preprocessing", {})
    scenario_payload = payload.get("scenario", {})
    session_payload = payload.get("session", {})

    st.caption(
        f"Session ID: {session_id} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Status: {session['status']} | "
        f"Export ID: {export_record.get('export_id')}"
    )

    _render_processed_session_header(
        session=session,
        payload=payload,
        export_record=export_record,
        decision_matrix_df=decision_matrix_df,
        individual_results=individual_results,
        group_result=group_result,
    )

    tabs = st.tabs(
        [
            "Overview",
            "Decision Matrix",
            "Group TOPSIS / Fuzzy TOPSIS",
            "Individual TOPSIS / Fuzzy TOPSIS",
            "Stakeholder Group Analysis",
            "Group-Level Aggregation",
            "Sensitivity Tests",
            "AI Payload JSON",
            "Diagnostics",
        ]
    )

    with tabs[0]:
        _render_processing_overview(
            session=session,
            payload=payload,
            bundle=bundle,
            export_record=export_record,
            decision_matrix_df=decision_matrix_df,
            individual_results=individual_results,
            group_result=group_result,
        )

    with tabs[1]:
        _render_decision_matrix_section(
            decision_matrix_df=decision_matrix_df,
            payload=payload,
            preprocessing=preprocessing,
        )

    with tabs[2]:
        _render_group_ranking_section(
            group_result=group_result,
            selected_ranking_method=session_payload.get(
                "selected_ranking_method",
                session.get("selected_ranking_method"),
            ),
        )

    with tabs[3]:
        _render_individual_ranking_section(
            individual_results=individual_results,
            selected_ranking_method=session_payload.get(
                "selected_ranking_method",
                session.get("selected_ranking_method"),
            ),
        )

    with tabs[4]:
        _render_stakeholder_group_analysis(
            individual_results=individual_results,
            session=session,
        )

    with tabs[5]:
        _render_group_level_aggregation_tab(
            group_result=group_result,
            session=session,
        )

    with tabs[6]:
        _render_sensitivity_tests_section(
            session=session,
            bundle=bundle,
            payload=payload,
            decision_matrix_df=decision_matrix_df,
            group_result=group_result,
            individual_results=individual_results,
        )

    with tabs[7]:
        _render_ai_payload_json_section(
            payload=payload,
            export_record=export_record,
        )

    with tabs[8]:
        _render_processing_diagnostics_section(
            payload=payload,
            preprocessing=preprocessing,
            individual_results=individual_results,
            group_result=group_result,
        )


def _render_unprocessed_session_view(
    *,
    session: dict,
    bundle,
    all_exports: list[dict],
) -> None:
    """
    Render a view for sessions that are locked but have no successful
    ai_interpretation_input export. Shows all export records (which may
    include partial or failed processing artifacts) and any in-session
    error state stored during the current browser session.
    """
    session_id = session["session_id"]
    status = session.get("status", "unknown")

    st.caption(
        f"Session ID: {session_id} | "
        f"Scenario: {scenario_label(bundle)} | "
        f"Status: {status}"
    )

    if status == "locked":
        st.warning(
            "This session is **locked** but has no successful processing output. "
            "It may not have been processed yet, or all previous processing attempts failed. "
            "Re-run deterministic processing from the **Run Deterministic Processing** tab.",
            icon="⚠️",
        )
    else:
        st.warning(
            f"This session has status **{status}** but no AI interpretation input export was found. "
            "Re-run deterministic processing to generate a valid output.",
            icon="⚠️",
        )

    # ── In-session error state (populated when processing fails in this browser session) ──
    last_failed = st.session_state.get("last_preprocessing_result")
    if (
        last_failed
        and last_failed.get("status") == "failed"
        and last_failed.get("session_id") == session_id
    ):
        st.markdown("### Most Recent Processing Attempt (This Session)")
        with st.container(border=True):
            ec1, ec2 = st.columns(2)
            ec1.metric("Error Type", last_failed.get("error_type", "Unknown"))
            ec2.metric("Timestamp", last_failed.get("timestamp", "—"))
            st.error(f"**Error:** {last_failed.get('error', 'No error message recorded.')}")

    # ── All export records for this session ──
    st.markdown("### Processing Attempt History")
    st.caption(
        "All export records for this session are shown below. A successful processing run "
        "produces an export of type `ai_interpretation_input`. Other types or records with "
        "error metadata indicate failed or partial attempts."
    )

    if not all_exports:
        st.info("No export records exist for this session. The session has not been processed yet.")
        return

    # Flatten export records into a display-friendly summary, extracting any
    # error/status fields that may be stored in the record itself.
    summary_rows = []
    for export in all_exports:
        payload_raw = export.get("payload_json")
        payload_parsed = _parse_json_column(payload_raw) if payload_raw else {}

        error_message = export.get("error_message") or (
            payload_parsed.get("error") if isinstance(payload_parsed, dict) else None
        )
        error_type = export.get("error_type") or (
            payload_parsed.get("error_type") if isinstance(payload_parsed, dict) else None
        )
        processing_status = export.get("status") or (
            payload_parsed.get("status") if isinstance(payload_parsed, dict) else "unknown"
        )
        step_failed = export.get("failed_step") or (
            payload_parsed.get("failed_step") if isinstance(payload_parsed, dict) else None
        )

        summary_rows.append({
            "Export ID": export.get("export_id", "—"),
            "Export Type": export.get("export_type", "—"),
            "Status": processing_status,
            "Created At": export.get("created_at", "—"),
            "Created By": export.get("created_by", "—"),
            "Failed Step": step_failed or "—",
            "Error Type": error_type or "—",
            "Error Message": error_message or "—",
        })

    summary_df = pd.DataFrame(summary_rows)

    event = st.dataframe(
        summary_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    selected_rows = event.selection.rows

    if not selected_rows:
        st.info("Select a row above to inspect the full export record.")
        return

    selected_export = all_exports[selected_rows[0]]
    selected_payload = _parse_json_column(selected_export.get("payload_json"))

    st.divider()
    st.markdown(
        f"**Export:** `{selected_export.get('export_id', '—')}` | "
        f"**Type:** `{selected_export.get('export_type', '—')}` | "
        f"**Created:** `{selected_export.get('created_at', '—')}`"
    )

    detail_tabs = st.tabs(["Error Details", "Raw Export Record"])

    with detail_tabs[0]:
        _render_export_error_details(selected_export, selected_payload)

    with detail_tabs[1]:
        st.json(selected_export)


def _render_export_error_details(export: dict, payload) -> None:
    """
    Render a structured error/diagnostic view for a single export record.
    Handles both top-level record fields and payload-embedded diagnostics.
    """
    # Collect error information from both the record and the payload.
    error_message = export.get("error_message")
    error_type = export.get("error_type")
    failed_step = export.get("failed_step")
    traceback_text = export.get("traceback")

    if isinstance(payload, dict):
        error_message = error_message or payload.get("error")
        error_type = error_type or payload.get("error_type")
        failed_step = failed_step or payload.get("failed_step")
        traceback_text = traceback_text or payload.get("traceback")

        # Some preprocessing payloads embed step logs with failure markers.
        preprocessing = payload.get("preprocessing", {})
        step_logs = preprocessing.get("step_logs", []) if isinstance(preprocessing, dict) else []
    else:
        step_logs = []

    has_any_detail = any([error_message, error_type, failed_step, traceback_text, step_logs])

    if not has_any_detail:
        st.info(
            "No structured error details were found for this export record. "
            "The record may be a partial artifact from an interrupted processing run, "
            "or the error was not captured in the export payload."
        )
        return

    if error_type or failed_step:
        col1, col2 = st.columns(2)
        col1.metric("Error Type", error_type or "—")
        col2.metric("Failed Step", failed_step or "—")

    if error_message:
        st.error(f"**Error message:** {error_message}")

    if traceback_text:
        with st.expander("Full Traceback"):
            st.code(traceback_text, language="python")

    if step_logs:
        st.markdown("#### Preprocessing Step Logs")
        step_df = pd.DataFrame(step_logs)
        st.dataframe(step_df, width="stretch", hide_index=True)

        # Highlight any steps that have a failure marker.
        failed_steps = [
            s for s in step_logs
            if str(s.get("status", "")).lower() in {"failed", "error"}
            or s.get("error")
        ]
        if failed_steps:
            st.warning(
                f"{len(failed_steps)} step(s) recorded a failure status. "
                "See the rows highlighted above."
            )


# ─────────────────────────────────────────────────────────────────────────────

def _format_export_option(export: dict) -> str:
    return (
        f"{export.get('created_at', 'unknown time')} | "
        f"{export.get('export_type', 'unknown export')} | "
        f"{export.get('export_id', 'unknown id')}"
    )


def _get_nested(data: dict, path: list[str], default=None):
    current = data

    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)

    return current if current is not None else default


def _decision_matrix_from_payload(payload: dict) -> pd.DataFrame:
    records = _get_nested(payload, ["decision_matrix", "records"], default=[])

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


def _render_processed_session_header(
    *,
    session: dict,
    payload: dict,
    export_record: dict,
    decision_matrix_df: pd.DataFrame,
    individual_results: list[dict],
    group_result: dict,
) -> None:
    group_ranking = group_result.get("ranking", {}) if isinstance(group_result, dict) else {}
    group_weighting = group_result.get("weighting", {}) if isinstance(group_result, dict) else {}

    ranking_df = _ranking_result_to_df(group_ranking)

    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric("Session Status", session.get("status", "unknown"))
    c2.metric("Alternatives", len(decision_matrix_df))
    c3.metric("Individual Runs", len(individual_results))
    c4.metric(
        "Ranking Method",
        _get_nested(payload, ["session", "selected_ranking_method"], default="—"),
    )

    if not ranking_df.empty and "Alternative" in ranking_df.columns:
        top_alt = ranking_df.sort_values("Rank").iloc[0]["Alternative"]
        c5.metric("Group Top Alternative", str(top_alt))
    else:
        c5.metric("Group Top Alternative", "—")

    if group_weighting:
        cr = group_weighting.get("consistency_ratio")
        if cr is not None:
            st.caption(
                f"Group AHP consistency ratio: `{round(float(cr), 4)}` | "
                f"Consistent: `{group_weighting.get('is_consistent', 'unknown')}`"
            )

    st.caption(
        f"Export ID: `{export_record.get('export_id')}` | "
        f"Created: `{export_record.get('created_at')}` | "
        f"Payload hash: `{export_record.get('payload_hash', 'n/a')}`"
    )

# ── CHANGE 1: Added aggregate pairwise matrix to the Overview tab ─────────────

def _render_processing_overview(
    *,
    session: dict,
    payload: dict,
    bundle,
    export_record: dict,
    decision_matrix_df: pd.DataFrame,
    individual_results: list[dict],
    group_result: dict,
) -> None:
    st.subheader("Processed Session Overview")

    scenario = payload.get("scenario", {})
    session_payload = payload.get("session", {})

    with st.container(border=True):
        st.markdown(f"### {scenario.get('title', bundle.title)}")
        st.write(scenario.get("summary") or bundle.scenario.get("summary", ""))
        st.info(scenario.get("policy_question") or bundle.scenario.get("policy_question", ""))

    group_weighting = group_result.get("weighting", {}) if isinstance(group_result, dict) else {}
    group_ranking = group_result.get("ranking", {}) if isinstance(group_result, dict) else {}
    group_ranking_df = _ranking_result_to_df(group_ranking)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Weighting Method", session_payload.get("selected_weighting_method", session.get("selected_weighting_method")))
    c2.metric("Ranking Method", session_payload.get("selected_ranking_method", session.get("selected_ranking_method")))
    c3.metric("Decision Matrix Rows", len(decision_matrix_df))
    c4.metric("Processed Stakeholders", len(individual_results))
    c5.metric(
        "Aggregation Strategy",
        group_weighting.get("aggregation_strategy", "simple"),
    )

    if group_weighting:
        st.markdown("### Group Weighting Summary")

        weight_df = _weights_to_df(
            group_weighting.get("group_weights")
            or group_weighting.get("weights")
            or {}
        )

        if not weight_df.empty:
            st.dataframe(
                weight_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "Weight": st.column_config.ProgressColumn(
                        "Weight",
                        min_value=0,
                        max_value=1,
                        format="%.4f",
                    )
                },
            )

        wc1, wc2, wc3 = st.columns(3)
        wc1.metric(
            "Consistency Ratio",
            _safe_round(group_weighting.get("consistency_ratio"), 4),
        )
        wc2.metric(
            "Consistent?",
            "Yes" if group_weighting.get("is_consistent") else "No",
        )
        wc3.metric(
            "Aggregation",
            group_weighting.get("aggregation_method", "—"),
        )

        # ── NEW: Aggregate pairwise matrix ────────────────────────────────────
        agg_matrix = (
            group_weighting.get("aggregate_pairwise_matrix")
            or group_weighting.get("pairwise_matrix")
        )
        criteria_order = group_weighting.get("criteria_order", [])

        if agg_matrix and criteria_order:
            st.markdown("### Group Aggregate Pairwise Matrix")
            st.caption(
                "The power-weighted geometric mean of all stakeholder pairwise matrices, "
                "used to derive the group AHP criterion weights above."
            )
            st.dataframe(
                pd.DataFrame(
                    agg_matrix,
                    index=criteria_order,
                    columns=criteria_order,
                ).round(4),
                width="stretch",
            )
        # ── END NEW ───────────────────────────────────────────────────────────

    if not group_ranking_df.empty:
        st.markdown("### Group Ranking Preview")
        st.dataframe(
            group_ranking_df.head(10),
            width="stretch",
            hide_index=True,
        )

        score_col = _find_score_column(group_ranking_df)
        if score_col and "Alternative" in group_ranking_df.columns:
            chart_df = (
                group_ranking_df
                .sort_values("Rank")
                .set_index("Alternative")[[score_col]]
            )
            st.bar_chart(chart_df)

    with st.expander("AI Task Instructions"):
        ai_task = payload.get("ai_task", {})
        st.json(ai_task)

def _render_decision_matrix_section(
    *,
    decision_matrix_df: pd.DataFrame,
    payload: dict,
    preprocessing: dict,
) -> None:
    st.subheader("Decision Matrix Used for TOPSIS / Fuzzy TOPSIS")

    if decision_matrix_df.empty:
        st.warning("No decision matrix records were found in the AI payload.")
        return

    matrix_metadata = _get_nested(payload, ["decision_matrix", "metadata"], default={})

    c1, c2, c3 = st.columns(3)
    c1.metric("Rows / Alternatives", len(decision_matrix_df))
    c2.metric("Columns", len(decision_matrix_df.columns))
    c3.metric(
        "Alternative ID Column",
        matrix_metadata.get("alternative_id_column", "—"),
    )

    st.dataframe(decision_matrix_df, width="stretch", hide_index=True)

    with st.expander("Decision Matrix Metadata"):
        st.json(matrix_metadata)

    with st.expander("Preprocessing Metadata"):
        st.json(preprocessing)

    if "step_logs" in preprocessing:
        st.markdown("### Preprocessing Step Logs")
        step_logs = preprocessing.get("step_logs", [])
        if step_logs:
            st.dataframe(pd.DataFrame(step_logs), width="stretch", hide_index=True)
        else:
            st.info("No preprocessing step logs were included in this payload.")

def _render_group_ranking_section(
    *,
    group_result: dict,
    selected_ranking_method: str | None,
) -> None:
    st.subheader("Group Ranking Output")
    st.caption(
        "This section shows the ranking produced from the aggregated/group AHP weights. "
        "It is the main deterministic result that should be summarized for the AI layer."
    )

    if not group_result:
        st.warning("No group result found in the AI payload.")
        return

    weighting = group_result.get("weighting", {})
    ranking = group_result.get("ranking", {})

    st.markdown(f"### {selected_ranking_method or ranking.get('method', 'Ranking')} Result")

    _render_weighting_result(weighting, title="Group AHP / Fuzzy AHP Weighting")
    _render_ranking_result(ranking, title="Group TOPSIS / Fuzzy TOPSIS Ranking", key_prefix="group")

    with st.expander("Raw Group Result JSON"):
        st.json(group_result)

def _render_individual_ranking_section(
    *,
    individual_results: list[dict],
    selected_ranking_method: str | None,
) -> None:
    st.subheader("Individual Stakeholder Ranking Outputs")
    st.caption(
        "Each row represents one stakeholder submission processed through AHP/Fuzzy AHP "
        "and then TOPSIS/Fuzzy TOPSIS."
    )

    if not individual_results:
        st.info("No individual results were found in this payload.")
        return

    summary_rows = []

    for result in individual_results:
        ranking_df = _ranking_result_to_df(result.get("ranking", {}))
        top_alt = "—"

        if not ranking_df.empty and "Alternative" in ranking_df.columns:
            top_alt = ranking_df.sort_values("Rank").iloc[0]["Alternative"]

        weighting = result.get("weighting", {})

        summary_rows.append(
            {
                "Participant": result.get("participant_id"),
                "Submission": result.get("submission_id"),
                "Stakeholder Group": result.get("stakeholder_group_id"),
                "Voting Power": result.get("normalized_voting_power"),
                "Status": result.get("status", "success"),
                "Top Alternative": top_alt,
                "Consistency Ratio": weighting.get("consistency_ratio"),
                "Consistent": weighting.get("is_consistent"),
            }
        )

    summary_df = pd.DataFrame(summary_rows)

    event = st.dataframe(
        summary_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    selected_rows = event.selection.rows

    if not selected_rows:
        st.info("Select a stakeholder result row to inspect the ranking and weighting details.")
        return

    selected_idx = selected_rows[0]
    selected_result = individual_results[selected_idx]

    st.divider()
    st.markdown(
        f"### Selected Stakeholder Result\n"
        f"Participant: `{selected_result.get('participant_id')}` | "
        f"Submission: `{selected_result.get('submission_id')}` | "
        f"Method: `{selected_ranking_method}`"
    )

    left, right = st.columns(2)

    with left:
        _render_weighting_result(
            selected_result.get("weighting", {}),
            title="Individual AHP / Fuzzy AHP Weighting",
        )

    with right:
        _render_ranking_result(
            selected_result.get("ranking", {}),
            title="Individual TOPSIS / Fuzzy TOPSIS Ranking",
            key_prefix=f"individual_{selected_idx}",
        )

    with st.expander("Raw Individual Result JSON"):
        st.json(selected_result)

def _render_weighting_result(weighting: dict, title: str) -> None:
    st.markdown(f"### {title}")

    if not weighting:
        st.info("No weighting output found.")
        return

    weights = weighting.get("group_weights") or weighting.get("weights") or {}
    weights_df = _weights_to_df(weights)

    if not weights_df.empty:
        st.dataframe(
            weights_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Weight": st.column_config.ProgressColumn(
                    "Weight",
                    min_value=0,
                    max_value=1,
                    format="%.4f",
                )
            },
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("Method", weighting.get("method", "—"))
    c2.metric("Consistency Ratio", _safe_round(weighting.get("consistency_ratio"), 4))
    c3.metric("Consistent?", "Yes" if weighting.get("is_consistent") else "No")

    matrix = (
        weighting.get("aggregate_pairwise_matrix")
        or weighting.get("pairwise_matrix")
    )
    criteria_order = weighting.get("criteria_order", [])

    if matrix and criteria_order:
        with st.expander("Pairwise Matrix"):
            st.dataframe(
                pd.DataFrame(matrix, index=criteria_order, columns=criteria_order).round(4),
                width="stretch",
            )


def _render_ranking_result(ranking: dict, title: str, key_prefix: str) -> None:
    st.markdown(f"### {title}")

    if not ranking:
        st.info("No ranking output found.")
        return

    ranking_df = _ranking_result_to_df(ranking)

    if ranking_df.empty:
        st.warning("Ranking output exists, but could not be converted to a table.")
        st.json(ranking)
        return

    score_col = _find_score_column(ranking_df)

    metric_cols = st.columns(4)
    metric_cols[0].metric("Method", ranking.get("method", "—"))
    metric_cols[1].metric("Alternatives", len(ranking_df))

    if "Alternative" in ranking_df.columns:
        top_alt = ranking_df.sort_values("Rank").iloc[0]["Alternative"]
        metric_cols[2].metric("Top Alternative", str(top_alt))
    else:
        metric_cols[2].metric("Top Alternative", "—")

    if score_col:
        spread = float(ranking_df[score_col].max() - ranking_df[score_col].min())
        metric_cols[3].metric("Score Spread", round(spread, 4))
    else:
        metric_cols[3].metric("Score Spread", "—")

    st.dataframe(
        ranking_df,
        width="stretch",
        hide_index=True,
        column_config={
            score_col: st.column_config.ProgressColumn(
                score_col,
                min_value=0,
                max_value=1,
                format="%.4f",
            )
            if score_col
            else None
        },
    )

    if score_col and "Alternative" in ranking_df.columns:
        chart_df = (
            ranking_df
            .sort_values("Rank")
            .set_index("Alternative")[[score_col]]
        )
        st.bar_chart(chart_df)

    with st.expander("Ranking Metadata"):
        metadata = {
            "method": ranking.get("method"),
            "criteria_order": ranking.get("criteria_order"),
            "criterion_types": ranking.get("criterion_types"),
            "weights": ranking.get("weights"),
            "alternative_id_column": ranking.get("alternative_id_column"),
        }
        st.json(metadata)

def _render_stakeholder_group_analysis(
    *,
    individual_results: list[dict],
    session: dict,
) -> None:
    """
    Render stakeholder group analysis with grouped matrices, metrics, and graphs.
    """
    st.subheader("Stakeholder Group Analysis")
    st.caption(
        "View aggregated AHP matrices, consistency metrics, voting power distributions, "
        "and participation statistics grouped by stakeholder group."
    )

    if not individual_results:
        st.info("No individual results available for group analysis.")
        return

    grouped_results = _group_individual_results_by_stakeholder_group(individual_results)
    
    if not grouped_results:
        st.info("Could not group individual results by stakeholder group.")
        return

    # ── SECTION 1: Grouped Pairwise Matrices ──────────────────────────────────
    # st.markdown("## Grouped Pairwise Matrices by Stakeholder Group")

    # for stakeholder_group in sorted(grouped_results.keys()):
    #     type_results = grouped_results[stakeholder_group]
        
    #     # Extract weighting info from results
    #     weighting_data_list = [
    #         result.get("weighting", {}) for result in type_results
    #         if result.get("weighting")
    #     ]

    #     if not weighting_data_list:
    #         st.warning(f"No weighting data for {stakeholder_group}.")
    #         continue

    #     # Compute group AHP on-the-fly from individual pairwise matrices
    #     # Reconstruct submission-like dicts from individual results
    #     group_submissions = []
    #     for result in type_results:
    #         weighting = result.get("weighting", {})
    #         pairwise_matrix = weighting.get("pairwise_matrix")
    #         criteria_order = weighting.get("criteria_order", [])
            
    #         if pairwise_matrix and criteria_order:
    #             group_submissions.append({
    #                 "participant_id": result.get("participant_id"),
    #                 "submission_id": result.get("submission_id"),
    #                 "stakeholder_group_id": stakeholder_group,
    #                 "normalized_voting_power": result.get("normalized_voting_power", 1.0),
    #                 "criteria_order": criteria_order,
    #                 "pairwise_matrix": pairwise_matrix,
    #             })

    #     if not group_submissions:
    #         st.warning(f"No pairwise matrices found for {stakeholder_group}.")
    #         continue

    #     # Compute group AHP within this stakeholder group
    #     try:
    #         group_ahp_result = compute_within_group_ahp(
    #             submissions=group_submissions,
    #             weight_derivation="geometric",
    #         )
    #     except Exception as e:
    #         st.warning(f"Could not compute group AHP for {stakeholder_group}: {e}")
    #         continue

    #     criteria_order = group_ahp_result.get("criteria_order", [])
    #     agg_matrix = group_ahp_result.get("aggregate_pairwise_matrix", [])
    #     consistency_ratio = group_ahp_result.get("consistency_ratio")

    #     with st.container(border=True):
    #         st.markdown(f"#### {stakeholder_group}")

    #         # Metrics
    #         mc1, mc2, mc3, mc4 = st.columns(4)
    #         mc1.metric("Results Count", len(type_results))
    #         mc2.metric(
    #             "Avg Voting Power",
    #             f"{sum(r.get('normalized_voting_power', 0) for r in type_results) / len(type_results):.4f}",
    #         )
    #         mc3.metric("Consistency Ratio", _safe_round(consistency_ratio, 4))
    #         mc4.metric(
    #             "Consistent?",
    #             "Yes" if group_ahp_result.get("is_consistent") else "No",
    #         )

    #         # View toggle
    #         view_type = st.radio(
    #             "Matrix View",
    #             ["Table", "Heatmap"],
    #             horizontal=True,
    #             key=f"stakeholder_group_matrix_view_{stakeholder_group}",
    #         )

    #         if view_type == "Table":
    #             st.markdown("**Aggregated Pairwise Matrix (Table)**")
    #             matrix_df = pd.DataFrame(agg_matrix, index=criteria_order, columns=criteria_order)
    #             st.dataframe(
    #                 matrix_df.round(4),
    #                 width="stretch",
    #                 column_config={
    #                     col: st.column_config.NumberColumn(col, format="%.4f")
    #                     for col in matrix_df.columns
    #                 },
    #             )
    #         else:
    #             st.markdown("**Aggregated Pairwise Matrix (Heatmap)**")
    #             _render_pairwise_matrix_heatmap(agg_matrix, criteria_order, f"{stakeholder_group} Pairwise Matrix")

    #         # Weights
    #         st.markdown("**Group Weights**")
    #         weights_dict = group_ahp_result.get("group_weights", {}) or group_ahp_result.get("weights", {})
    #         weights_df = pd.DataFrame(
    #             [{"Criterion": crit, "Weight": float(weight)} for crit, weight in weights_dict.items()]
    #         ).sort_values("Weight", ascending=False)

    #         st.dataframe(
    #             weights_df,
    #             width="stretch",
    #             hide_index=True,
    #             column_config={
    #                 "Weight": st.column_config.ProgressColumn("Weight", min_value=0, max_value=1, format="%.4f")
    #             },
    #         )

    # ── SECTION 2: Group Metrics Cards ─────────────────────────────────────
    st.markdown("## Group Metrics Summary")

    group_metrics = {}
    for stakeholder_group, type_results in grouped_results.items():
        group_metrics[stakeholder_group] = _compute_stakeholder_group_metrics(
            [],
            type_results,
        )

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Stakeholder Groups", len(grouped_results))
    mc2.metric("Total Participants", len(individual_results))

    consistency_ratios = [
        m.get("avg_consistency_ratio") for m in group_metrics.values()
        if m.get("avg_consistency_ratio") is not None
    ]
    if consistency_ratios:
        mc3.metric("Min Consistency Ratio", _safe_round(min(consistency_ratios), 4))
        mc4.metric("Max Consistency Ratio", _safe_round(max(consistency_ratios), 4))
    else:
        mc3.metric("Min Consistency Ratio", "—")
        mc4.metric("Max Consistency Ratio", "—")

    # ── SECTION 3: Consistency Ratios Graph ────────────────────────────────
    st.markdown("## Consistency Ratios by Stakeholder Group")

    consistency_data = []
    for stakeholder_group in sorted(grouped_results.keys()):
        type_results = grouped_results[stakeholder_group]
        consistency_values = [
            float(r.get("weighting", {}).get("consistency_ratio", 0))
            for r in type_results
            if r.get("weighting", {}).get("consistency_ratio") is not None
        ]
        if consistency_values:
            consistency_data.append({
                "Stakeholder Group": stakeholder_group,
                "Avg Consistency Ratio": sum(consistency_values) / len(consistency_values),
                "Count": len(consistency_values),
            })

    if consistency_data:
        consistency_df = pd.DataFrame(consistency_data)
        consistency_df = consistency_df.sort_values("Avg Consistency Ratio", ascending=True)

        import plotly.express as px
        fig_consistency = px.bar(
            consistency_df,
            x="Stakeholder Group",
            y="Avg Consistency Ratio",
            title="Average AHP Consistency Ratio by Stakeholder Group",
            labels={"Avg Consistency Ratio": "Consistency Ratio"},
            hover_data={"Count": True},
        )
        fig_consistency.update_layout(height=400)
        st.plotly_chart(fig_consistency, width='stretch')
    else:
        st.info("No consistency data available for visualization.")

    # ── SECTION 4: Voting Power Distribution ───────────────────────────────
    st.markdown("## Voting Power Distribution by Stakeholder Group")

    power_data = []
    for stakeholder_group in sorted(grouped_results.keys()):
        type_results = grouped_results[stakeholder_group]
        total_power = sum(r.get("normalized_voting_power", 0) for r in type_results)
        power_data.append({
            "Stakeholder Group": stakeholder_group,
            "Total Voting Power": total_power,
            "Count": len(type_results),
        })

    if power_data:
        power_df = pd.DataFrame(power_data)
        
        import plotly.express as px
        fig_power = px.bar(
            power_df,
            x="Stakeholder Group",
            y="Total Voting Power",
            title="Total Voting Power by Stakeholder Group",
            labels={"Total Voting Power": "Voting Power"},
            hover_data={"Count": True},
        )
        fig_power.update_layout(height=400)
        st.plotly_chart(fig_power, width='stretch')
    else:
        st.info("No voting power data available for visualization.")

    # ── SECTION 5: Participation Metrics ───────────────────────────────────
    st.markdown("## Participation Metrics by Stakeholder Group")

    participation_data = []
    for stakeholder_group in sorted(grouped_results.keys()):
        type_results = grouped_results[stakeholder_group]
        participation_data.append({
            "Stakeholder Group": stakeholder_group,
            "Submissions": len(type_results),
            "Avg Power": sum(r.get("normalized_voting_power", 0) for r in type_results) / len(type_results) if type_results else 0,
        })

    if participation_data:
        participation_df = pd.DataFrame(participation_data)
        
        import plotly.express as px
        fig_participation = px.bar(
            participation_df,
            x="Stakeholder Group",
            y="Submissions",
            title="Submission Count by Stakeholder Group",
            labels={"Submissions": "Number of Submissions"},
            hover_data={"Avg Power": ":.4f"},
        )
        fig_participation.update_layout(height=400)
        st.plotly_chart(fig_participation, width='stretch')
    else:
        st.info("No participation data available for visualization.")

    # ── SECTION 6: Weight Distributions ────────────────────────────────────
    st.markdown("## Criterion Weight Distributions by Stakeholder Group")

    weight_comparison_data = []
    for stakeholder_group in sorted(grouped_results.keys()):
        type_results = grouped_results[stakeholder_group]
        for result in type_results:
            weighting = result.get("weighting", {})
            weights = weighting.get("group_weights", {}) or weighting.get("weights", {})
            for criterion, weight in weights.items():
                weight_comparison_data.append({
                    "Stakeholder Group": stakeholder_group,
                    "Criterion": criterion,
                    "Weight": float(weight),
                })

    if weight_comparison_data:
        weight_comparison_df = pd.DataFrame(weight_comparison_data)
        
        import plotly.express as px
        fig_weights = px.box(
            weight_comparison_df,
            x="Stakeholder Group",
            y="Weight",
            color="Criterion",
            title="Weight Distribution by Stakeholder Group and Criterion",
            labels={"Weight": "Criterion Weight"},
        )
        fig_weights.update_layout(height=500)
        st.plotly_chart(fig_weights, width='stretch')
    else:
        st.info("No weight data available for visualization.")

def _render_sensitivity_tests_section(
    *,
    session: dict,
    bundle,
    payload: dict,
    decision_matrix_df: pd.DataFrame,
    group_result: dict,
    individual_results: list[dict],
) -> None:
    st.subheader("Sensitivity and Stability Tests")
    st.caption(
        "These tests help evaluate whether TOPSIS/Fuzzy TOPSIS rankings are stable "
        "under different stakeholder weights and small weight perturbations."
    )

    if not group_result:
        st.warning("No group result is available for sensitivity testing.")
        return

    group_ranking = group_result.get("ranking", {})
    group_ranking_df = _ranking_result_to_df(group_ranking)

    if group_ranking_df.empty:
        st.warning("The group ranking output could not be converted into a rank table.")
        return

    tabs = st.tabs(
        [
            "Individual vs Group",
            "Stakeholder Pairwise Correlations",
            "Weight Perturbation",
            "Interpretation Notes",
        ]
    )

    with tabs[0]:
        _render_individual_vs_group_sensitivity(
            group_ranking_df=group_ranking_df,
            individual_results=individual_results,
        )

    with tabs[1]:
        _render_pairwise_stakeholder_rank_correlations(
            individual_results=individual_results,
        )

    with tabs[2]:
        _render_weight_perturbation_sensitivity(
            session=session,
            bundle=bundle,
            decision_matrix_df=decision_matrix_df,
            group_result=group_result,
            baseline_group_ranking_df=group_ranking_df,
        )

    with tabs[3]:
        _render_sensitivity_interpretation_notes()

def _render_individual_vs_group_sensitivity(
    *,
    group_ranking_df: pd.DataFrame,
    individual_results: list[dict],
) -> None:
    st.markdown("### Individual Stakeholder Rankings vs Group Ranking")
    st.write(
        "This compares each stakeholder's TOPSIS/Fuzzy TOPSIS ranking against the final "
        "group ranking using Spearman correlation, Kendall correlation, top-1 agreement, "
        "top-3 overlap, and rank-shift metrics."
    )

    rows = []

    for result in individual_results:
        ranking_df = _ranking_result_to_df(result.get("ranking", {}))

        if ranking_df.empty:
            continue

        comparison = _compare_rankings(
            baseline_df=group_ranking_df,
            comparison_df=ranking_df,
            comparison_label=result.get("participant_id", "unknown"),
        )

        comparison.update(
            {
                "Participant": result.get("participant_id"),
                "Submission": result.get("submission_id"),
                "Stakeholder Group": result.get("stakeholder_group_id"),
                "Voting Power": result.get("normalized_voting_power"),
            }
        )

        rows.append(comparison)

    if not rows:
        st.info("No individual rankings could be compared.")
        return

    result_df = pd.DataFrame(rows)

    preferred_order = [
        "Participant",
        "Submission",
        "Stakeholder Group",
        "Voting Power",
        "Spearman Rho",
        "Kendall Tau",
        "Top-1 Match",
        "Top-3 Overlap",
        "Mean Absolute Rank Shift",
        "Max Rank Shift",
        "Rank Reversal Count",
    ]

    existing_cols = [col for col in preferred_order if col in result_df.columns]
    remaining_cols = [col for col in result_df.columns if col not in existing_cols]

    st.dataframe(
        result_df[existing_cols + remaining_cols],
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Quick Reading")
    st.write(
        "- **Spearman rho near 1.0** means the stakeholder ranking is very similar to the group ranking.\n"
        "- **Kendall tau near 1.0** means the pairwise ordering between alternatives is similar.\n"
        "- **Top-1 match** shows whether both rankings chose the same best alternative.\n"
        "- **Rank reversal count** shows how many alternatives changed rank position."
    )

def _render_pairwise_stakeholder_rank_correlations(
    *,
    individual_results: list[dict],
) -> None:
    st.markdown("### Pairwise Stakeholder Ranking Correlations")
    st.write(
        "This compares stakeholder rankings against each other. It is useful for identifying "
        "preference clusters or stakeholders whose rankings strongly disagree."
    )

    comparable = []

    for result in individual_results:
        ranking_df = _ranking_result_to_df(result.get("ranking", {}))

        if not ranking_df.empty:
            comparable.append(
                {
                    "label": f"{result.get('participant_id')} | {result.get('stakeholder_group_id')}",
                    "participant_id": result.get("participant_id"),
                    "stakeholder_group_id": result.get("stakeholder_group_id"),
                    "ranking_df": ranking_df,
                }
            )

    if len(comparable) < 2:
        st.info("At least two individual rankings are needed for pairwise comparison.")
        return

    rows = []

    for i in range(len(comparable)):
        for j in range(i + 1, len(comparable)):
            left = comparable[i]
            right = comparable[j]

            comparison = _compare_rankings(
                baseline_df=left["ranking_df"],
                comparison_df=right["ranking_df"],
                comparison_label=right["label"],
            )

            rows.append(
                {
                    "Stakeholder A": left["label"],
                    "Stakeholder B": right["label"],
                    "Spearman Rho": comparison["Spearman Rho"],
                    "Kendall Tau": comparison["Kendall Tau"],
                    "Top-1 Match": comparison["Top-1 Match"],
                    "Top-3 Overlap": comparison["Top-3 Overlap"],
                    "Mean Absolute Rank Shift": comparison["Mean Absolute Rank Shift"],
                    "Max Rank Shift": comparison["Max Rank Shift"],
                    "Rank Reversal Count": comparison["Rank Reversal Count"],
                }
            )

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

def _render_weight_perturbation_sensitivity(
    *,
    session: dict,
    bundle,
    decision_matrix_df: pd.DataFrame,
    group_result: dict,
    baseline_group_ranking_df: pd.DataFrame,
) -> None:
    st.markdown("### Group Weight Perturbation Test")
    st.write(
        "This test increases and decreases one criterion weight at a time, renormalizes "
        "the weight vector, reruns TOPSIS, and compares the new ranking to the original "
        "group ranking."
    )

    selected_ranking_method = session.get("selected_ranking_method")

    if selected_ranking_method not in {"TOPSIS"}:
        st.warning(
            "Automatic perturbation reruns currently support crisp TOPSIS only. "
            "For Fuzzy TOPSIS, implement a fuzzy ranking rerun function and call it here."
        )
        return

    if decision_matrix_df.empty:
        st.warning("No decision matrix is available for perturbation testing.")
        return

    group_weighting = group_result.get("weighting", {})
    group_ranking = group_result.get("ranking", {})

    weights = (
        group_weighting.get("group_weights")
        or group_weighting.get("weights")
        or {}
    )

    if not weights:
        st.warning("No group weights found for perturbation testing.")
        return

    alternative_id_column = (
        group_ranking.get("alternative_id_column")
        or _get_nested(group_ranking, ["metadata", "alternative_id_column"])
        or _get_nested(group_result, ["ranking", "alternative_id_column"])
    )

    if not alternative_id_column:
        # Try to infer from payload conventions.
        possible_cols = [
            "alternative",
            "alternative_id",
            "school_name",
            "name",
        ]
        alternative_id_column = next(
            (col for col in possible_cols if col in decision_matrix_df.columns),
            None,
        )

    if not alternative_id_column:
        st.error(
            "Could not determine the alternative ID column. Ensure preprocessing.final_output "
            "sets alternative_id_column and that ranking output preserves it."
        )
        return

    perturb_pct = st.slider(
        "Perturbation size",
        min_value=0.01,
        max_value=0.50,
        value=0.10,
        step=0.01,
        format="%.2f",
        help="0.10 means each selected criterion is tested at +10% and -10% before renormalization.",
    )

    criteria = list(weights.keys())

    selected_criteria = st.multiselect(
        "Criteria to perturb",
        criteria,
        default=criteria,
    )

    if not selected_criteria:
        st.info("Select at least one criterion to perturb.")
        return

    if st.button("Run Weight Perturbation Test", type="primary"):
        rows = []
        detailed_results = {}

        for criterion in selected_criteria:
            for direction_label, multiplier in [
                ("Increase", 1.0 + perturb_pct),
                ("Decrease", max(0.0, 1.0 - perturb_pct)),
            ]:
                perturbed_weights = _perturb_and_normalize_weights(
                    weights=weights,
                    criterion=criterion,
                    multiplier=multiplier,
                )

                try:
                    rerun_result = run_topsis_for_weights(
                        bundle=bundle,
                        decision_matrix_df=decision_matrix_df,
                        weights_by_criterion=perturbed_weights,
                        alternative_id_column=alternative_id_column,
                    )

                    rerun_df = _ranking_result_to_df(rerun_result)

                    comparison = _compare_rankings(
                        baseline_df=baseline_group_ranking_df,
                        comparison_df=rerun_df,
                        comparison_label=f"{criterion} {direction_label}",
                    )

                    row = {
                        "Criterion Perturbed": criterion,
                        "Direction": direction_label,
                        "Multiplier": round(multiplier, 4),
                        "Perturbation": f"{direction_label} {perturb_pct:.0%}",
                    }
                    row.update(comparison)
                    rows.append(row)

                    detailed_results[f"{criterion}_{direction_label}"] = {
                        "perturbed_weights": perturbed_weights,
                        "ranking": rerun_result,
                        "comparison": comparison,
                    }

                except Exception as exc:
                    rows.append(
                        {
                            "Criterion Perturbed": criterion,
                            "Direction": direction_label,
                            "Multiplier": round(multiplier, 4),
                            "Perturbation": f"{direction_label} {perturb_pct:.0%}",
                            "Error": str(exc),
                        }
                    )

        if not rows:
            st.info("No perturbation results were generated.")
            return

        results_df = pd.DataFrame(rows)

        st.markdown("#### Perturbation Summary")
        st.dataframe(results_df, width="stretch", hide_index=True)

        if "Spearman Rho" in results_df.columns:
            valid = results_df.dropna(subset=["Spearman Rho"])
            if not valid.empty:
                lowest = valid.sort_values("Spearman Rho").iloc[0]
                st.warning(
                    f"Most sensitive perturbation: `{lowest['Criterion Perturbed']}` "
                    f"({lowest['Direction']}) with Spearman rho = "
                    f"`{round(float(lowest['Spearman Rho']), 4)}`."
                )

        with st.expander("Detailed Perturbation JSON"):
            st.json(detailed_results)

def _ranking_result_to_df(ranking_result: dict) -> pd.DataFrame:
    if not ranking_result:
        return pd.DataFrame()

    rankings = ranking_result.get("rankings", [])

    if not rankings:
        return pd.DataFrame()

    df = pd.DataFrame(rankings)

    if df.empty:
        return df

    alt_col = _find_alternative_column(df)

    if alt_col and alt_col != "Alternative":
        df = df.rename(columns={alt_col: "Alternative"})

    score_col = _find_score_column(df)

    if "Rank" not in df.columns:
        if score_col:
            df["Rank"] = df[score_col].rank(
                ascending=False,
                method="min",
            ).astype(int)
        else:
            df["Rank"] = range(1, len(df) + 1)

    if "Alternative" in df.columns:
        sort_cols = ["Rank", "Alternative"]
    else:
        sort_cols = ["Rank"]

    return df.sort_values(sort_cols).reset_index(drop=True)


def _find_alternative_column(df: pd.DataFrame) -> str | None:
    candidates = [
        "Alternative",
        "alternative",
        "alternative_id",
        "Alternative ID",
        "school_name",
        "name",
    ]

    for col in candidates:
        if col in df.columns:
            return col

    # Fallback: first object/string column.
    for col in df.columns:
        if df[col].dtype == "object":
            return col

    return None


def _find_score_column(df: pd.DataFrame) -> str | None:
    candidates = [
        "TOPSIS Score",
        "Topsis Score",
        "Score",
        "score",
        "Closeness",
        "closeness",
        "Preference",
        "preference",
        "performance",
    ]

    for col in candidates:
        if col in df.columns and pd.api.types.is_numeric_dtype(df[col]):
            return col

    numeric_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
        and col.lower() not in {"rank", "index"}
    ]

    if numeric_cols:
        return numeric_cols[-1]

    return None


def _weights_to_df(weights: dict) -> pd.DataFrame:
    if not weights:
        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "Criterion": criterion,
                "Weight": float(weight),
            }
            for criterion, weight in weights.items()
        ]
    ).sort_values("Weight", ascending=False)


def _compare_rankings(
    *,
    baseline_df: pd.DataFrame,
    comparison_df: pd.DataFrame,
    comparison_label: str,
) -> dict:
    base = _standardize_rank_df(baseline_df)
    comp = _standardize_rank_df(comparison_df)

    if base.empty or comp.empty:
        return {
            "Comparison": comparison_label,
            "Spearman Rho": None,
            "Kendall Tau": None,
            "Top-1 Match": False,
            "Top-3 Overlap": 0,
            "Mean Absolute Rank Shift": None,
            "Max Rank Shift": None,
            "Rank Reversal Count": None,
        }

    merged = base.merge(
        comp,
        on="Alternative",
        how="inner",
        suffixes=("_baseline", "_comparison"),
    )

    if merged.empty:
        return {
            "Comparison": comparison_label,
            "Spearman Rho": None,
            "Kendall Tau": None,
            "Top-1 Match": False,
            "Top-3 Overlap": 0,
            "Mean Absolute Rank Shift": None,
            "Max Rank Shift": None,
            "Rank Reversal Count": None,
        }

    spearman = merged["Rank_baseline"].corr(
        merged["Rank_comparison"],
        method="spearman",
    )

    try:
        kendall = merged["Rank_baseline"].corr(
            merged["Rank_comparison"],
            method="kendall",
        )
    except Exception:
        kendall = None

    merged["Absolute Rank Shift"] = (
        merged["Rank_baseline"] - merged["Rank_comparison"]
    ).abs()

    top1_baseline = _top_k_alternatives(base, k=1)
    top1_comparison = _top_k_alternatives(comp, k=1)

    top3_baseline = _top_k_alternatives(base, k=3)
    top3_comparison = _top_k_alternatives(comp, k=3)

    top3_overlap = len(top3_baseline.intersection(top3_comparison))

    return {
        "Comparison": comparison_label,
        "Spearman Rho": _safe_round(spearman, 4),
        "Kendall Tau": _safe_round(kendall, 4),
        "Top-1 Match": top1_baseline == top1_comparison,
        "Top-3 Overlap": top3_overlap,
        "Mean Absolute Rank Shift": _safe_round(merged["Absolute Rank Shift"].mean(), 4),
        "Max Rank Shift": _safe_round(merged["Absolute Rank Shift"].max(), 4),
        "Rank Reversal Count": int((merged["Absolute Rank Shift"] > 0).sum()),
    }


def _standardize_rank_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    working = df.copy()

    alt_col = _find_alternative_column(working)

    if alt_col and alt_col != "Alternative":
        working = working.rename(columns={alt_col: "Alternative"})

    if "Alternative" not in working.columns:
        return pd.DataFrame()

    if "Rank" not in working.columns:
        score_col = _find_score_column(working)

        if score_col:
            working["Rank"] = working[score_col].rank(
                ascending=False,
                method="min",
            ).astype(int)
        else:
            working["Rank"] = range(1, len(working) + 1)

    return working[["Alternative", "Rank"]].copy()


def _top_k_alternatives(df: pd.DataFrame, k: int) -> set:
    if df.empty or "Alternative" not in df.columns or "Rank" not in df.columns:
        return set()

    return set(
        df.sort_values("Rank")
        .head(k)["Alternative"]
        .astype(str)
        .tolist()
    )


def _perturb_and_normalize_weights(
    *,
    weights: dict[str, float],
    criterion: str,
    multiplier: float,
) -> dict[str, float]:
    updated = {
        key: float(value)
        for key, value in weights.items()
    }

    updated[criterion] = max(0.0, updated[criterion] * multiplier)

    total = sum(updated.values())

    if total <= 0:
        raise ValueError("Perturbed weights sum to zero.")

    return {
        key: value / total
        for key, value in updated.items()
    }


def _safe_round(value, digits: int = 4):
    try:
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)
    except Exception:
        return value


# ── Helper Functions for Stakeholder Group Analysis ──────────────────────────

def _group_submissions_by_stakeholder_group(submissions: list[dict]) -> dict[str, list[dict]]:
    """
    Group submissions by stakeholder_group_id.
    
    Returns dict mapping stakeholder_group_id → list of submissions for that type.
    """
    grouped = {}
    for submission in submissions:
        stakeholder_group = submission.get("stakeholder_group_id", "Unknown")
        if stakeholder_group not in grouped:
            grouped[stakeholder_group] = []
        grouped[stakeholder_group].append(submission)
    return grouped


def _group_individual_results_by_stakeholder_group(individual_results: list[dict]) -> dict[str, list[dict]]:
    """
    Group individual results by stakeholder_group_id.
    
    Returns dict mapping stakeholder_group_id → list of individual results for that type.
    """
    grouped = {}
    for result in individual_results:
        stakeholder_group = result.get("stakeholder_group_id", "Unknown")
        if stakeholder_group not in grouped:
            grouped[stakeholder_group] = []
        grouped[stakeholder_group].append(result)
    return grouped


def _compute_group_ahp_for_type(submissions: list[dict]) -> dict:
    """
    Compute group AHP result for a set of submissions (typically grouped by stakeholder group).
    
    Returns the group AHP result dict or None if computation fails.
    """
    if not submissions:
        return None
    
    try:
        return compute_within_group_ahp(submissions)
    except Exception:
        return None


def _render_pairwise_matrix_heatmap(
    matrix: list[list[float]],
    criteria_order: list[str],
    title: str,
) -> None:
    """
    Render a pairwise comparison matrix as an actual heatmap using Plotly.
    """
    import plotly.express as px
    import plotly.graph_objects as go

    df = pd.DataFrame(matrix, index=criteria_order, columns=criteria_order)

    fig = px.imshow(
        df,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale="RdBu_r",
        zmin=1 / len(criteria_order) if criteria_order else 0,
        zmax=len(criteria_order) if criteria_order else 1,
    )
    fig.update_layout(
        title=title,
        xaxis_title="Criteria",
        yaxis_title="Criteria",
        height=400,
    )
    st.plotly_chart(fig, width='stretch')


def _compute_stakeholder_group_metrics(
    submissions: list[dict],
    individual_results: list[dict] | None = None,
) -> dict:
    """
    Compute aggregated metrics for a stakeholder group:
    - participation count
    - total voting power
    - average power per stakeholder
    - consistency ratio (if individual_results provided)
    """
    metrics = {
        "submission_count": len(submissions),
        "total_voting_power": sum(
            float(sub.get("normalized_voting_power", 0)) for sub in submissions
        ),
    }
    
    if submissions:
        metrics["avg_voting_power"] = metrics["total_voting_power"] / len(submissions)
    else:
        metrics["avg_voting_power"] = 0.0
    
    if individual_results:
        consistency_ratios = [
            float(result.get("weighting", {}).get("consistency_ratio", 0))
            for result in individual_results
            if result.get("weighting", {}).get("consistency_ratio") is not None
        ]
        if consistency_ratios:
            metrics["avg_consistency_ratio"] = sum(consistency_ratios) / len(consistency_ratios)
            metrics["consistency_count"] = len(consistency_ratios)
        else:
            metrics["avg_consistency_ratio"] = None
            metrics["consistency_count"] = 0
    else:
        metrics["avg_consistency_ratio"] = None
        metrics["consistency_count"] = 0
    
    return metrics

    
def _render_ai_payload_json_section(
    *,
    payload: dict,
    export_record: dict,
) -> None:
    st.subheader("AI Interpretation Payload")
    st.write(
        "This is the deterministic package that will be passed to the AI layer. "
        "It should contain the scenario, decision matrix, preprocessing metadata, "
        "individual AHP/TOPSIS results, group AHP/TOPSIS results, and AI task instructions."
    )

    st.download_button(
        "Download AI Payload JSON",
        data=json.dumps(payload, indent=2),
        file_name=f"{export_record.get('export_id', 'ai_payload')}.json",
        mime="application/json",
    )

    view_mode = st.radio(
        "JSON View",
        ["Structured Viewer", "Raw JSON"],
        horizontal=True,
        key="ai_payload_json_view_mode",
    )

    if view_mode == "Structured Viewer":
        st.json(payload)
    else:
        st.code(json.dumps(payload, indent=2), language="json")

def _render_processing_diagnostics_section(
    *,
    payload: dict,
    preprocessing: dict,
    individual_results: list[dict],
    group_result: dict,
) -> None:
    st.subheader("Processing Diagnostics")

    warnings = []

    if not payload.get("decision_matrix", {}).get("records"):
        warnings.append("Decision matrix records are missing.")

    if not group_result:
        warnings.append("Group result is missing.")

    failed_individual = [
        result for result in individual_results
        if result.get("status") == "failed"
    ]

    if failed_individual:
        warnings.append(f"{len(failed_individual)} individual stakeholder result(s) failed.")

    group_weighting = group_result.get("weighting", {}) if isinstance(group_result, dict) else {}

    if group_weighting and not group_weighting.get("is_consistent", True):
        warnings.append(
            "Group AHP matrix has a consistency ratio above the configured threshold."
        )

    if warnings:
        for warning in warnings:
            st.warning(warning)
    else:
        st.success("No major processing diagnostics were detected.")

    st.markdown("### Failed Individual Results")

    if failed_individual:
        st.dataframe(pd.DataFrame(failed_individual), width="stretch", hide_index=True)
    else:
        st.info("No failed individual result records.")

    st.markdown("### Payload Section Availability")

    availability = {
        "session": bool(payload.get("session")),
        "scenario": bool(payload.get("scenario")),
        "decision_matrix": bool(payload.get("decision_matrix", {}).get("records")),
        "preprocessing": bool(preprocessing),
        "individual_results": bool(individual_results),
        "group_result": bool(group_result),
        "ai_task": bool(payload.get("ai_task")),
    }

    availability_df = pd.DataFrame(
        [{"Section": key, "Available": value} for key, value in availability.items()]
    )

    st.dataframe(
        availability_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Available": st.column_config.CheckboxColumn("Available")
        },
    )

    with st.expander("Raw Diagnostics JSON"):
        st.json(
            {
                "warnings": warnings,
                "preprocessing_status": preprocessing.get("status"),
                "preprocessing_run_id": preprocessing.get("run_id"),
                "individual_result_count": len(individual_results),
                "failed_individual_result_count": len(failed_individual),
                "group_consistency_ratio": group_weighting.get("consistency_ratio"),
            }
        )


def _render_group_level_aggregation_tab(
    *,
    group_result: dict,
    session: dict,
) -> None:
    """
    Render group-level aggregation details when using two-stage aggregation strategy.
    
    Displays:
    - Aggregation strategy indicator
    - Per-type group matrices, weights, and metrics
    - Final aggregated group matrix
    - Group-level consistency and weights
    """
    group_weighting = group_result.get("weighting", {}) if isinstance(group_result, dict) else {}
    
    if not group_weighting:
        st.info("No group weighting result available.")
        return
    
    aggregation_strategy = group_weighting.get("aggregation_strategy", "simple")
    
    st.subheader("Group-Level Aggregation Analysis")
    
    # Strategy indicator
    if aggregation_strategy == "two_stage_by_group":
        st.success(
            "**Aggregation Strategy:** Two-Stage by Stakeholder Group\n\n"
            "Stage 1: Individual submissions within each stakeholder group are aggregated "
            "(with equal weighting).\n\n"
            "Stage 2: Group matrices are aggregated using stakeholder-type voting weights."
        )
    else:
        st.info(
            "**Aggregation Strategy:** Simple (All submissions aggregated together)\n\n"
            "All stakeholder submissions are aggregated using their individual voting weights."
        )
    
    # Only show detailed per-type breakdown if two-stage aggregation was used
    if aggregation_strategy == "two_stage_by_group":
        stakeholder_group_results = group_weighting.get("stakeholder_group_results", {})
        
        if stakeholder_group_results:
            st.markdown("### Per-Stakeholder-Type Analysis")
            st.caption(
                "Each stakeholder group's aggregated pairwise matrix and derived weights. "
                "Individual submissions within each type are equally weighted in this view."
            )
            
            stakeholder_group_weights = group_weighting.get("stakeholder_group_weights", {})
            
            for idx, (stakeholder_group, type_details) in enumerate(stakeholder_group_results.items()):
                with st.expander(
                    f"📊 {stakeholder_group} ({type_details.get('submission_count', 0)} members)",
                    expanded=(idx == 0),
                ):
                    # Metrics
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Members", type_details.get("submission_count", 0))
                    m2.metric("Type Weight", f"{stakeholder_group_weights.get(stakeholder_group, 0):.4f}")
                    m3.metric(
                        "Consistency Ratio",
                        _safe_round(type_details.get("consistency_ratio"), 4),
                    )
                    m4.metric(
                        "Consistent?",
                        "Yes" if type_details.get("is_consistent") else "No",
                    )
                    
                    # Weights
                    st.markdown("**Aggregated Weights**")
                    weights_dict = type_details.get("weights", {})
                    if weights_dict:
                        type_weights_df = _weights_to_df(weights_dict)
                        st.dataframe(
                            type_weights_df,
                            width="stretch",
                            hide_index=True,
                            column_config={
                                "Weight": st.column_config.ProgressColumn(
                                    "Weight",
                                    min_value=0,
                                    max_value=1,
                                    format="%.4f",
                                )
                            },
                        )
                    
                    # Pairwise matrix
                    agg_matrix = type_details.get("aggregate_pairwise_matrix", [])
                    criteria_order = group_weighting.get("criteria_order", [])
                    
                    if agg_matrix and criteria_order:
                        st.markdown("**Aggregated Pairwise Matrix**")
                        _render_pairwise_matrix_heatmap(
                            matrix=agg_matrix,
                            criteria_order=criteria_order,
                            title=f"{stakeholder_group} Pairwise Matrix",
                        )
            
            st.divider()
            st.markdown("### Final Group-Level Result")
            st.caption(
                "Final aggregation combining all stakeholder groups, weighted by their voting power."
            )
        else:
            st.info("No per-type stakeholder group analysis available for simple aggregation strategy.")
    
    # Final group metrics
    st.markdown("### Final Group Metrics")
    fg1, fg2, fg3 = st.columns(3)
    fg1.metric(
        "Final Consistency Ratio",
        _safe_round(group_weighting.get("consistency_ratio"), 4),
    )
    fg2.metric(
        "Consistent?",
        "Yes" if group_weighting.get("is_consistent") else "No",
    )
    fg3.metric(
        "Aggregation Method",
        group_weighting.get("aggregation_method", "—"),
    )
    
    # Final group weights
    st.markdown("**Final Group Criterion Weights**")
    final_weights_dict = group_weighting.get("group_weights", group_weighting.get("weights", {}))
    if final_weights_dict:
        final_weights_df = _weights_to_df(final_weights_dict)
        st.dataframe(
            final_weights_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Weight": st.column_config.ProgressColumn(
                    "Weight",
                    min_value=0,
                    max_value=1,
                    format="%.4f",
                )
            },
        )
    
    # Final aggregate pairwise matrix
    final_agg_matrix = group_weighting.get("aggregate_pairwise_matrix", group_weighting.get("pairwise_matrix", []))
    criteria_order = group_weighting.get("criteria_order", [])
    
    if final_agg_matrix and criteria_order:
        st.markdown("**Final Aggregate Pairwise Matrix**")
        st.caption(
            "The power-weighted geometric mean of all stakeholder matrices. "
            "Used to derive the final group criterion weights above."
        )
        _render_pairwise_matrix_heatmap(
            matrix=final_agg_matrix,
            criteria_order=criteria_order,
            title="Final Group Pairwise Matrix",
        )


def _render_sensitivity_interpretation_notes() -> None:
    st.markdown("### How to Read These Tests")

    st.markdown(
        """
        **Spearman rank correlation** measures whether two rankings move in the same order.
        A value near `1.0` means the rankings are very similar. A value near `0.0` means weak
        rank relationship. A negative value means the rankings tend to move in opposite directions.

        **Kendall tau** focuses on pairwise ordering agreement. It is often useful when the
        number of alternatives is small because it directly reflects how often pairs of
        alternatives are ordered the same way.

        **Top-1 match** checks whether two rankings select the same best alternative.

        **Top-3 overlap** checks how many alternatives appear in both top-three sets.

        **Mean absolute rank shift** shows the average amount alternatives moved up or down.

        **Max rank shift** shows the largest movement by any one alternative.

        **Rank reversal count** counts how many alternatives changed rank position.
        """
    )

    st.info(
        "For thesis evaluation, report both correlation metrics and practical stability metrics. "
        "A ranking can have a high Spearman correlation but still change the top-ranked alternative, "
        "which may matter more in policy decision-making."
    )