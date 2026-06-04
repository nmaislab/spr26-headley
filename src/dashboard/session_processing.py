from __future__ import annotations

from typing import Any

from dashboard.ai_payloads import build_ai_interpretation_payload
from dashboard.preprocessing import execute_preprocessing
from dashboard.ranking import (
    RankingError,
    run_fuzzy_topsis_for_weights,
    run_topsis_for_weights,
)
from dashboard.repositories import (
    get_session,
    get_session_stakeholder_group_weights,
    list_submissions,
    save_export_record,
    save_stakeholder_group_aggregation_results,
    list_session_stakeholder_groups,
)
from dashboard.weighting import (
    WeightingError,
    compute_group_ahp_result_adaptive,
    compute_submission_ahp_result,
)


class SessionProcessingError(RuntimeError):
    pass


def process_session_for_ai(
    *,
    bundle,
    session_id: str,
    created_by: str = "moderator",
) -> dict[str, Any]:
    """
    Full deterministic processing pipeline before AI.

    Steps:
    1. Load and preprocess scenario data.
    2. Load submissions.
    3. Compute AHP weights for individual submissions.
    4. Compute group AHP weights.
    5. Run TOPSIS/Fuzzy TOPSIS for individual and group weights.
    6. Package deterministic outputs for AI.
    7. Save an export record.
    """
    session = get_session(session_id)

    if not session:
        raise SessionProcessingError(f"Unknown session: {session_id}")

    submissions = list_submissions(session_id)

    if not submissions:
        raise SessionProcessingError("Cannot process session without submissions.")

    decision_matrix_df, preprocessing_metadata = execute_preprocessing(
        bundle,
        session_id=session_id,
    )

    final_output_config = bundle.preprocessing.get("final_output", {}) if bundle.preprocessing else {}

    alternative_id_column = final_output_config.get("alternative_id_column")

    if not alternative_id_column:
        raise SessionProcessingError(
            "preprocessing.json final_output must define alternative_id_column."
        )

    selected_ranking_method = session["selected_ranking_method"]

    individual_results = []

    for submission in submissions:
        try:
            ahp_result = compute_submission_ahp_result(submission)

            ranking_result = _run_selected_ranking(
                selected_ranking_method=selected_ranking_method,
                bundle=bundle,
                decision_matrix_df=decision_matrix_df,
                weights_by_criterion=ahp_result["weights"],
                alternative_id_column=alternative_id_column,
            )

            individual_results.append(
                {
                    "participant_id": submission["participant_id"],
                    "submission_id": submission["submission_id"],
                    "stakeholder_group_id": submission.get("stakeholder_group_id"),
                    "normalized_voting_power": submission.get("normalized_voting_power"),
                    "weighting": ahp_result,
                    "ranking": ranking_result,
                }
            )

        except Exception as exc:
            individual_results.append(
                {
                    "participant_id": submission.get("participant_id"),
                    "submission_id": submission.get("submission_id"),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    successful_individual_results = [
        result for result in individual_results
        if result.get("status") != "failed"
    ]

    if not successful_individual_results:
        raise SessionProcessingError(
            "All individual stakeholder rankings failed. Cannot continue processing."
        )

    # Fetch stakeholder group voting weights from database (source of truth for live session state)
    stakeholder_group_weights = get_session_stakeholder_group_weights(session_id)
    
    if not stakeholder_group_weights:
        raise SessionProcessingError(
            f"No stakeholder group weights found for session {session_id}. "
            "Session may not be initialized."
        )

    group_ahp = compute_group_ahp_result_adaptive(
        submissions=submissions,
        aggregation_strategy=session.get("aggregation_strategy", "two_stage_by_group"),
        stakeholder_group_weights=stakeholder_group_weights,
        weight_derivation="geometric",
    )

    group_ranking = _run_selected_ranking(
        selected_ranking_method=selected_ranking_method,
        bundle=bundle,
        decision_matrix_df=decision_matrix_df,
        weights_by_criterion=group_ahp["group_weights"],
        alternative_id_column=alternative_id_column,
    )

    group_result = {
        "weighting": group_ahp,
        "ranking": group_ranking,
    }

    ai_payload = build_ai_interpretation_payload(
        session=session,
        bundle=bundle,
        decision_matrix_df=decision_matrix_df,
        preprocessing_metadata=preprocessing_metadata,
        individual_results=individual_results,
        group_result=group_result,
    )

    export_id = save_export_record(
        session_id=session_id,
        export_type="ai_interpretation_input",
        contract_version="1.0",
        payload=ai_payload,
        created_by=created_by,
    )

    # Persist stakeholder group aggregation matrices for later review and audit
    _persist_stakeholder_group_aggregation_results(
        session_id=session_id,
        export_id=export_id,
        group_ahp=group_ahp,
        total_submission_count=len(submissions),
    )

    return {
        "status": "success",
        "session_id": session_id,
        "export_id": export_id,
        "decision_matrix": decision_matrix_df,
        "preprocessing_metadata": preprocessing_metadata,
        "individual_results": individual_results,
        "group_result": group_result,
        "ai_payload": ai_payload,
    }


def _run_selected_ranking(
    *,
    selected_ranking_method: str,
    bundle,
    decision_matrix_df,
    weights_by_criterion: dict[str, float],
    alternative_id_column: str,
) -> dict[str, Any]:
    if selected_ranking_method == "TOPSIS":
        return run_topsis_for_weights(
            bundle=bundle,
            decision_matrix_df=decision_matrix_df,
            weights_by_criterion=weights_by_criterion,
            alternative_id_column=alternative_id_column,
        )

    if selected_ranking_method in {"FUZZY_TOPSIS", "Fuzzy TOPSIS"}:
        return run_fuzzy_topsis_for_weights(
            bundle=bundle,
            decision_matrix_df=decision_matrix_df,
            weights_by_criterion=weights_by_criterion,
            alternative_id_column=alternative_id_column,
        )

    raise RankingError(f"Unsupported ranking method: {selected_ranking_method}")


def _persist_stakeholder_group_aggregation_results(
    *,
    session_id: str,
    export_id: str,
    group_ahp: dict[str, Any],
    total_submission_count: int,
) -> None:
    """
    Persist stakeholder group aggregation matrices and weights for later review.
    
    Called after group AHP computation to save per-group matrices and the final
    aggregate matrix. Enables dashboard review of aggregation process and audit trail.
    """
    from dashboard.utils.ids import new_id
    
    criteria_order = group_ahp.get("criteria_order", [])
    
    # Save per-stakeholder-group aggregation results
    group_results = group_ahp.get("stakeholder_group_results", {})
    for group_id, group_data in group_results.items():
        save_stakeholder_group_aggregation_results(
            result_id=new_id("agr"),
            session_id=session_id,
            export_id=export_id,
            stakeholder_group_id=group_id,
            aggregation_level="stakeholder_group",
            criteria_order=criteria_order,
            pairwise_matrix=group_data.get("aggregate_pairwise_matrix", []),
            weights=group_data.get("weights", {}),
            consistency_ratio=group_data.get("consistency_ratio"),
            is_consistent=group_data.get("is_consistent"),
            submission_count=group_data.get("submission_count"),
        )
    
    # Save final aggregate matrix
    save_stakeholder_group_aggregation_results(
        result_id=new_id("agr"),
        session_id=session_id,
        export_id=export_id,
        stakeholder_group_id=None,
        aggregation_level="final_group",
        criteria_order=criteria_order,
        pairwise_matrix=group_ahp.get("aggregate_pairwise_matrix", []),
        weights=group_ahp.get("group_weights", {}),
        consistency_ratio=group_ahp.get("consistency_ratio"),
        is_consistent=group_ahp.get("is_consistent"),
        submission_count=total_submission_count,
    )