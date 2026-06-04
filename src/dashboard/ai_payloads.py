from __future__ import annotations

from typing import Any

import pandas as pd

def build_ai_interpretation_payload(
    *,
    session: dict,
    bundle,
    decision_matrix_df: pd.DataFrame,
    preprocessing_metadata: dict[str, Any],
    individual_results: list[dict[str, Any]],
    group_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Package deterministic MCDM outputs for AI interpretation.
    """
    return {
        "contract_type": "ai_interpretation_input",
        "contract_version": "1.0",
        "session": {
            "session_id": session["session_id"],
            "session_name": session["session_name"],
            "scenario_id": session["scenario_id"],
            "scenario_version": session["scenario_version"],
            "selected_weighting_method": session["selected_weighting_method"],
            "selected_ranking_method": session["selected_ranking_method"],
        },
        "scenario": {
            "title": bundle.title,
            "domain": bundle.domain,
            "summary": bundle.scenario.get("summary"),
            "policy_question": bundle.scenario.get("policy_question"),
            "alternatives": bundle.scenario.get("alternatives", []),
            "criteria": bundle.criteria,
        },
        "decision_matrix": {
            "records": decision_matrix_df.to_dict(orient="records"),
            "metadata": preprocessing_metadata.get("decision_matrix", {}),
        },
        "preprocessing": preprocessing_metadata,
        "mcdm_results": {
            "individual_results": individual_results,
            "group_result": group_result,
        },
        "ai_task": {
            "instructions": [
                "Explain the deterministic ranking results.",
                "Identify the main criteria driving the top-ranked alternatives.",
                "Discuss trade-offs between criteria.",
                "Do not invent data not present in the decision matrix or MCDM results.",
                "Clearly distinguish mathematical results from advisory interpretation.",
            ],
            "expected_outputs": [
                "plain_language_summary",
                "ranking_explanation",
                "tradeoff_analysis",
                "stakeholder_sensitivity_notes",
                "verification_warnings",
            ],
        },
    }