from __future__ import annotations

import json

import numpy as np

from typing import Any
from collections import defaultdict
from pyDecision.algorithm import ahp_method, fuzzy_ahp_method

class WeightingError(RuntimeError):
    pass

def _json_loads(value: str | dict | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)

def get_pairwise_matrix_from_submission(
    submission: dict[str, Any],
) -> tuple[list[str], np.ndarray]:
    """
    Extract the generated AHP pairwise matrix from a stored stakeholder submission.

    The matrix is generated in preferences.transform_linguistic_preferences()
    from one linguistic rating per criterion.
    """
    transformed = _json_loads(submission.get("transformed_preferences_json"))

    # Check for both possible key names for robustness
    matrix_payload = (
        transformed.get("rating_derived_pairwise_matrix")
        or transformed.get("ahp_pairwise_matrix")
    )
    if not matrix_payload:
        raise WeightingError(
            f"Submission {submission.get('submission_id')} does not contain a pairwise matrix."
        )

    criteria_order = matrix_payload.get("criteria_order", [])
    matrix = matrix_payload.get("matrix", [])

    if not criteria_order or not matrix:
        raise WeightingError(
            f"Submission {submission.get('submission_id')} has an incomplete pairwise matrix."
        )

    np_matrix = np.array(matrix, dtype=float)

    if np_matrix.shape[0] != np_matrix.shape[1]:
        raise WeightingError("AHP pairwise matrix must be square.")

    if np_matrix.shape[0] != len(criteria_order):
        raise WeightingError("Matrix size does not match criteria order length.")

    return criteria_order, np_matrix

def compute_ahp_weights_from_matrix(
    criteria_order: list[str],
    pairwise_matrix: np.ndarray,
    weight_derivation: str = "geometric",
) -> dict[str, Any]:
    """
    Run pyDecision AHP on a pairwise comparison matrix.

    Returns:
        Dict with keys: method, weight_derivation, criteria_order, pairwise_matrix,
        weights (dict), weights_vector (list), consistency_ratio, is_consistent.
    """
    weights, rc = ahp_method(pairwise_matrix, wd=weight_derivation)

    weights_by_criterion = {
        criterion_id: float(weights[idx])
        for idx, criterion_id in enumerate(criteria_order)
    }

    return {
        "method": "AHP",
        "weight_derivation": weight_derivation,
        "criteria_order": criteria_order,
        "pairwise_matrix": pairwise_matrix.round(6).tolist(),
        "weights": weights_by_criterion,
        "weights_vector": [float(w) for w in weights],
        "consistency_ratio": float(rc),
        "is_consistent": bool(rc <= 0.10),
    }

def compute_submission_ahp_result(
    submission: dict[str, Any],
    weight_derivation: str = "geometric",
) -> dict[str, Any]:
    """
    Compute AHP weights for one stakeholder's preference submission.
    
    Validates that the submission has a valid pairwise matrix and extracts
    submission metadata (participant_id, stakeholder_group_id).
    """
    criteria_order, matrix = get_pairwise_matrix_from_submission(submission)

    result = compute_ahp_weights_from_matrix(
        criteria_order=criteria_order,
        pairwise_matrix=matrix,
        weight_derivation=weight_derivation,
    )

    result["submission_id"] = submission.get("submission_id")
    result["participant_id"] = submission.get("participant_id")
    result["stakeholder_group_id"] = submission.get("stakeholder_group_id")
    result["normalized_voting_power"] = float(submission.get("normalized_voting_power") or 0.0)

    return result

def _normalize_vector(values: list[float]) -> list[float]:
    """Normalize a vector of values to sum to 1.0."""
    total = sum(values)

    if total <= 0:
        return [1.0 / len(values)] * len(values)

    return [v / total for v in values]


def _weighted_geometric_mean_matrix(
    matrices: list[np.ndarray],
    weights: list[float],
) -> np.ndarray:
    """
    Aggregate pairwise matrices using weighted geometric mean.
    
    Computes: product(matrix_k ** weight_k) for each cell, using log-space for stability.
    
    Args:
        matrices: List of pairwise matrices (numpy arrays).
        weights: List of weights for each matrix (will be normalized).
    
    Returns:
        Aggregated pairwise matrix.
    
    Raises:
        WeightingError if matrices list is empty or contains non-positive values.
    """
    if not matrices:
        raise WeightingError("Cannot aggregate an empty matrix list.")

    weights = _normalize_vector(weights)

    stacked = np.stack(matrices, axis=0)
    weight_array = np.array(weights, dtype=float).reshape((-1, 1, 1))

    if np.any(stacked <= 0):
        raise WeightingError("Pairwise matrix values must be positive.")

    return np.exp(np.sum(weight_array * np.log(stacked), axis=0))


def compute_two_stage_group_ahp(
    submissions: list[dict[str, Any]],
    stakeholder_group_weights: dict[str, float],
    weight_derivation: str = "geometric",
) -> dict[str, Any]:
    """
    Two-stage group AHP aggregation by stakeholder group.

    Stage 1: Aggregate individual participant pairwise matrices within each
    stakeholder group using equal weighting (so participant count doesn't
    affect the group's final voting power).

    Stage 2: Aggregate per-group matrices using normalized stakeholder group
    voting powers from the database.

    Args:
        submissions: List of preference submissions with pairwise matrices.
                    Each submission must have stakeholder_group_id.
        stakeholder_group_weights: Dict mapping stakeholder_group_id → normalized voting power.
                                  Should come from database (session_stakeholder_groups).
        weight_derivation: AHP weight derivation method ('geometric' or other).

    Returns:
        Dict with method='GROUP_AHP_BY_STAKEHOLDER_GROUP', containing:
        - criteria_order: shared criteria order
        - stakeholder_group_results: per-group AHP results
        - aggregate_pairwise_matrix: final group matrix
        - group_weights: final criterion weights
        - consistency_ratio, is_consistent: final AHP metrics
    
    Raises:
        WeightingError if submissions missing stakeholder_group_id, criteria
        order mismatch, or unknown stakeholder group in submissions.
    """
    if not submissions:
        raise WeightingError("Cannot compute group AHP result without submissions.")

    # Group submissions by stakeholder group
    grouped_submissions: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for submission in submissions:
        stakeholder_group = submission.get("stakeholder_group_id")

        if not stakeholder_group:
            raise WeightingError(
                f"Submission {submission.get('submission_id')} is missing stakeholder_group_id."
            )

        grouped_submissions[stakeholder_group].append(submission)

    # Stage 1: Aggregate within each stakeholder group
    criteria_order: list[str] | None = None
    stakeholder_group_matrices: dict[str, np.ndarray] = {}
    stakeholder_group_details: dict[str, Any] = {}

    for group_id, group_submissions in grouped_submissions.items():
        group_matrices = []
        group_participant_powers = []
        group_individual_results = []

        for submission in group_submissions:
            current_order, matrix = get_pairwise_matrix_from_submission(submission)

            if criteria_order is None:
                criteria_order = current_order
            elif criteria_order != current_order:
                raise WeightingError("All submissions must use the same criteria order.")

            individual_result = compute_submission_ahp_result(
                submission=submission,
                weight_derivation=weight_derivation,
            )

            group_individual_results.append(individual_result)
            group_matrices.append(matrix)
            # Equal weighting within group (default): participant count doesn't change
            # the group's institutional voting power
            group_participant_powers.append(1.0)

        normalized_group_participant_powers = _normalize_vector(group_participant_powers)

        # Aggregate participant matrices within this group
        group_aggregate_matrix = _weighted_geometric_mean_matrix(
            matrices=group_matrices,
            weights=normalized_group_participant_powers,
        )

        stakeholder_group_matrices[group_id] = group_aggregate_matrix

        group_ahp_result = compute_ahp_weights_from_matrix(
            criteria_order=criteria_order or [],
            pairwise_matrix=group_aggregate_matrix,
            weight_derivation=weight_derivation,
        )

        stakeholder_group_details[group_id] = {
            "submission_count": len(group_submissions),
            "participant_powers_within_group": normalized_group_participant_powers,
            "aggregate_pairwise_matrix": group_aggregate_matrix.round(6).tolist(),
            "weights": group_ahp_result["weights"],
            "weights_vector": group_ahp_result["weights_vector"],
            "consistency_ratio": group_ahp_result["consistency_ratio"],
            "is_consistent": group_ahp_result["is_consistent"],
            "individual_results": group_individual_results,
        }

    # Stage 2: Aggregate per-group matrices using stakeholder group voting powers
    active_groups = list(stakeholder_group_matrices.keys())

    # Validate all submitted groups have database weights
    missing_groups = [g for g in active_groups if g not in stakeholder_group_weights]
    if missing_groups:
        raise WeightingError(
            f"Submitted stakeholder groups {missing_groups} do not have voting power "
            "configured in the session. Cannot complete aggregation."
        )

    raw_group_weights = [
        float(stakeholder_group_weights.get(group_id, 0.0))
        for group_id in active_groups
    ]

    normalized_group_weights = _normalize_vector(raw_group_weights)

    # Aggregate group matrices using normalized group voting powers
    final_aggregate_matrix = _weighted_geometric_mean_matrix(
        matrices=[stakeholder_group_matrices[g] for g in active_groups],
        weights=normalized_group_weights,
    )

    group_result = compute_ahp_weights_from_matrix(
        criteria_order=criteria_order or [],
        pairwise_matrix=final_aggregate_matrix,
        weight_derivation=weight_derivation,
    )

    return {
        "method": "GROUP_AHP_BY_STAKEHOLDER_GROUP",
        "aggregation_method": "two_stage_weighted_geometric_mean",
        "aggregation_strategy": "two_stage_by_group",
        "weight_derivation": weight_derivation,
        "criteria_order": criteria_order,
        "stakeholder_group_weights": {
            group_id: normalized_group_weights[idx]
            for idx, group_id in enumerate(active_groups)
        },
        "stakeholder_group_results": stakeholder_group_details,
        "aggregate_pairwise_matrix": final_aggregate_matrix.round(6).tolist(),
        "group_weights": group_result["weights"],
        "group_weights_vector": group_result["weights_vector"],
        "consistency_ratio": group_result["consistency_ratio"],
        "is_consistent": group_result["is_consistent"],
    }


def compute_within_group_ahp(
    submissions: list[dict[str, Any]],
    weight_derivation: str = "geometric",
) -> dict[str, Any]:
    """
    Compute group AHP result for submissions within a single stakeholder group.
    
    This performs Stage 1 aggregation only: aggregate individual participant pairwise
    matrices within the group using equal weighting (so participant count doesn't
    affect the group's institutional voting power).
    
    Args:
        submissions: List of preference submissions with pairwise matrices.
                    All submissions should belong to the same stakeholder group.
        weight_derivation: AHP weight derivation method ('geometric' or other).
    
    Returns:
        Dict with method='GROUP_AHP_WITHIN_GROUP', containing:
        - criteria_order: shared criteria order
        - aggregate_pairwise_matrix: aggregated group matrix
        - group_weights: final criterion weights
        - consistency_ratio, is_consistent: AHP metrics
        - stakeholder_powers: list of stakeholder powers used
        - individual_results: individual AHP results for each participant
    
    Raises:
        WeightingError if submissions list is empty or pairwise matrix is invalid.
    """
    if not submissions:
        raise WeightingError("Cannot compute group AHP result without submissions.")

    group_matrices = []
    group_participant_powers = []
    group_individual_results = []
    criteria_order: list[str] | None = None

    for submission in submissions:
        current_order, matrix = get_pairwise_matrix_from_submission(submission)

        if criteria_order is None:
            criteria_order = current_order
        elif criteria_order != current_order:
            raise WeightingError("All submissions must use the same criteria order.")

        individual_result = compute_submission_ahp_result(
            submission=submission,
            weight_derivation=weight_derivation,
        )

        group_individual_results.append(individual_result)
        group_matrices.append(matrix)
        # Equal weighting within group: participant count doesn't change
        # the group's institutional voting power
        group_participant_powers.append(1.0)

    normalized_group_participant_powers = _normalize_vector(group_participant_powers)

    # Aggregate participant matrices within this group
    group_aggregate_matrix = _weighted_geometric_mean_matrix(
        matrices=group_matrices,
        weights=normalized_group_participant_powers,
    )

    group_ahp_result = compute_ahp_weights_from_matrix(
        criteria_order=criteria_order or [],
        pairwise_matrix=group_aggregate_matrix,
        weight_derivation=weight_derivation,
    )

    return {
        "method": "GROUP_AHP_WITHIN_GROUP",
        "aggregation_method": "weighted_geometric_mean",
        "aggregation_strategy": "within_group",
        "weight_derivation": weight_derivation,
        "criteria_order": criteria_order,
        "aggregate_pairwise_matrix": group_aggregate_matrix.round(6).tolist(),
        "group_weights": group_ahp_result["weights"],
        "group_weights_vector": group_ahp_result["weights_vector"],
        "consistency_ratio": group_ahp_result["consistency_ratio"],
        "is_consistent": group_ahp_result["is_consistent"],
        "stakeholder_powers": normalized_group_participant_powers,
        "individual_results": group_individual_results,
    }


def compute_group_ahp_result_adaptive(
    submissions: list[dict[str, Any]],
    aggregation_strategy: str = "two_stage_by_group",
    stakeholder_group_weights: dict[str, float] | None = None,
    weight_derivation: str = "geometric",
) -> dict[str, Any]:
    """
    Adaptive group AHP aggregation based on strategy.

    Routes to two-stage aggregation. The 'simple' strategy is deprecated and will
    raise an error if used.

    Args:
        submissions: List of preference submissions with pairwise matrices.
        aggregation_strategy: Must be 'two_stage_by_group'. Other values raise error.
        stakeholder_group_weights: Dict mapping stakeholder_group_id to normalized voting power.
                                 Should come from database. Required.
        weight_derivation: AHP weight derivation method.

    Returns:
        Aggregation result dict with method='GROUP_AHP_BY_STAKEHOLDER_GROUP'.
    
    Raises:
        WeightingError if stakeholder_group_weights is missing or empty.
    """
    if not submissions:
        raise WeightingError("Cannot compute group AHP result without submissions.")

    if not stakeholder_group_weights:
        raise WeightingError(
            "Stakeholder group voting weights are required for two-stage aggregation. "
            "These should be fetched from the database (get_session_stakeholder_group_weights). "
            "The system cannot preserve stakeholder group voting power without them."
        )

    return compute_two_stage_group_ahp(
        submissions=submissions,
        stakeholder_group_weights=stakeholder_group_weights,
        weight_derivation=weight_derivation,
    )
