from __future__ import annotations

from typing import Any

from dashboard.scenario_loader import ScenarioBundle


class PreferenceValidationError(ValueError):
    pass

DEFAULT_SCALE = {
    "type": "linguistic",
    "ordered": True,
    "values": [
        {"label": "Very Low", "numeric_value": 1, "fuzzy_value": [0.0, 0.1, 0.25]},
        {"label": "Low", "numeric_value": 2, "fuzzy_value": [0.15, 0.3, 0.45]},
        {"label": "Medium", "numeric_value": 3, "fuzzy_value": [0.35, 0.5, 0.65]},
        {"label": "High", "numeric_value": 4, "fuzzy_value": [0.55, 0.7, 0.85]},
        {"label": "Very High", "numeric_value": 5, "fuzzy_value": [0.75, 0.9, 1.0]},
    ],
}

def get_default_scale_id(bundle: ScenarioBundle) -> str:
    return bundle.preference_collection.get("default_scale_id", "five_level_importance")

def get_scale(bundle: ScenarioBundle) -> dict[str, Any]:
    scale_id = get_default_scale_id(bundle)
    return bundle.scales.get(scale_id, DEFAULT_SCALE)

def scale_labels(bundle: ScenarioBundle) -> list[str]:
    return [item["label"] for item in get_scale(bundle).get("values", [])]

def validate_preferences(bundle: ScenarioBundle, raw_preferences: dict[str, str]) -> None:
    labels = set(scale_labels(bundle))
    required_ids = [c["id"] for c in bundle.criteria if c.get("required", True)]
    missing = [criterion_id for criterion_id in required_ids if not raw_preferences.get(criterion_id)]
    if missing:
        raise PreferenceValidationError(f"Missing preference ratings for: {', '.join(missing)}")
    
    invalid = [value for value in raw_preferences.values() if value not in labels]
    if invalid:
        raise PreferenceValidationError(f"Invalid preference rating values: {', '.join(invalid)}")
    
def transform_linguistic_preferences(bundle: ScenarioBundle, raw_preferences: dict[str, str]) -> dict[str, Any]:
    """
    
    """
    validate_preferences(bundle, raw_preferences)
    scale = get_scale(bundle)
    by_label = {item["label"]: item for item in scale["values"]}
    criteria_order = [c["id"] for c in bundle.criteria]

    numeric_scores: dict[str, float] = {}
    fuzzy_values: dict[str, list[float]] = {}

    for criterion_id in criteria_order:
        label = raw_preferences[criterion_id]
        numeric_scores[criterion_id] = float(by_label[label]["numeric_value"])
        fuzzy_values[criterion_id] = by_label[label].get("fuzzy_value", [])

    total = sum(numeric_scores.values())
    normalized = {
        criterion_id: (score / total if total > 0 else 0.0)
        for criterion_id, score in numeric_scores.items()
    }

    matrix: list[list[float]] = []
    for row_id in criteria_order:
        row: list[float] = []
        for col_id in criteria_order:
            denom = numeric_scores[col_id]
            row.append(round(numeric_scores[row_id] / denom, 6) if denom > 0 else 0.0)
        matrix.append(row)

    pairwise_payload = {
        "criteria_order": criteria_order,
        "matrix": matrix,
        "generation_method": "score_ratio",
        "description": (
            "Generated from stakeholder linguistic ratings by converting labels to "
            "numeric scores and calculating score_i / score_j for each criterion pair."
        ),
    }

    return {
        "strategy": "criterion_linguistic_rating_to_ahp_pairwise_matrix",
        "scale_id": get_default_scale_id(bundle),
        "numeric_scores": numeric_scores,
        "normalized_direct_weights": normalized,
        "ahp_pairwise_matrix": pairwise_payload,
        "fuzzy_linguistic_values": fuzzy_values,
    }

def extract_preference_data(submissions: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract and organize preference matrices and scores from submissions for visualization.
    
    Args:
        submissions: List of submission dicts from repositories.list_submissions()
        
    Returns:
        Organized preference data with numeric scores, weights, and pairwise matrices by stakeholder
    """
    result = {
        "all_stakeholders": {
            "numeric_scores": {},  # {criterion_id: [stakeholder_scores]}
            "normalized_weights": {},  # {criterion_id: [stakeholder_weights]}
        },
        "by_stakeholder": {},  # {participant_id: {...detailed data...}}
    }

    criteria_list = []  # Track criteria order

    for submission in submissions:
        participant_id = submission["participant_id"]
        stakeholder_group = submission.get("stakeholder_group_id", "Unknown")
        
        try:
            transformed = submission.get("transformed_preferences_json")
            if isinstance(transformed, str):
                import json
                transformed = json.loads(transformed)
            
            if not transformed:
                continue
            
            # Extract numeric scores
            numeric_scores = transformed.get("numeric_scores", {})
            if not criteria_list and numeric_scores:
                criteria_list = list(numeric_scores.keys())
            
            # Initialize stakeholder entry if needed
            if participant_id not in result["by_stakeholder"]:
                result["by_stakeholder"][participant_id] = {
                    "stakeholder_group": stakeholder_group,
                    "numeric_scores": numeric_scores,
                    "normalized_weights": transformed.get("normalized_direct_weights", {}),
                    "pairwise_matrix": transformed.get("rating_derived_pairwise_matrix", {}),
                }
            
            # Add to aggregate scores
            for criterion_id, score in numeric_scores.items():
                if criterion_id not in result["all_stakeholders"]["numeric_scores"]:
                    result["all_stakeholders"]["numeric_scores"][criterion_id] = []
                result["all_stakeholders"]["numeric_scores"][criterion_id].append(score)
            
            # Add to aggregate weights
            weights = transformed.get("normalized_direct_weights", {})
            for criterion_id, weight in weights.items():
                if criterion_id not in result["all_stakeholders"]["normalized_weights"]:
                    result["all_stakeholders"]["normalized_weights"][criterion_id] = []
                result["all_stakeholders"]["normalized_weights"][criterion_id].append(weight)
        
        except Exception:
            # Skip submissions with unparseable preferences
            continue
    
    result["criteria_order"] = criteria_list
    return result