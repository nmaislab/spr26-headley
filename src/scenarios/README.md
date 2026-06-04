# Scenario Creation Guide

This guide explains how to create new scenarios for the stakeholder-polling application. A scenario represents a specific decision-making problem (e.g., which school to close, how to allocate public safety resources) with defined criteria, alternatives, stakeholders, and preferences.

---

## Quick Start

1. **Copy the template**: `cp -r _template your_scenario_name/`
2. **Customize**: Edit all `.jsonc` files to define your scenario
3. **Add data**: Place CSV files in `data/` directory
4. **Validate**: Check against the validation checklist below
5. **Test**: Run the scenario loader to verify parsing

---

## Scenario Structure Overview

Every scenario is a directory containing **8 required components**:

| Component | File | Type | Purpose |
|-----------|------|------|---------|
| Criteria | `criteria.jsonc` | JSON | Define evaluation dimensions (e.g., cost, benefit) |
| Scenario Metadata | `scenario.jsonc` | JSON | Define alternatives, stakeholder groups, MCDM methods |
| Data Sources | `data_sources.jsonc` | JSON | Reference external CSV files |
| ETL Pipeline | `preprocessing.jsonc` | JSON | Define data transformation steps |
| Session Rules | `session_rules.json` | JSON | Define voting/access rules |
| UI Configuration | `ui_config.jsonc` | JSON | Configure voting interface behavior |
| Custom Functions | `functions.py` | Python | Define custom calculation functions |
| Data Files | `data/` | CSV | Actual data for alternatives |

---

## File Dependencies & Data Flow

```
data/
  ├─ source1.csv
  └─ source2.csv
        ↓
   data_sources.jsonc ──────┐
        ↓                    ↓
   preprocessing.jsonc ← functions.py
        ↓
   (transformed data)
        ↓
   criteria.jsonc
        ↓
   scenario.jsonc 
        ↓
   session_rules.jsonc + ui_config.jsonc
        ↓
   [voting interface & analysis]
```

**Key Points**:
- `preprocessing.jsonc` defines the ETL pipeline that transforms raw CSVs into criteria values
- `criteria.jsonc` defines what metrics exist and their types (cost/benefit)
- `scenario.jsonc` brings everything together with metadata, alternatives, and MCDM configuration
- `session_rules.jsonc` and `ui_config.jsonc` control how voting happens

---

## Field Reference: All Files Explained

### 1. `criteria.jsonc` — Evaluation Criteria

Defines the **dimensions** used to evaluate alternatives.

```jsonc
{
  "schema_version": "1.0",
  "criteria": [
    {
      // Unique identifier (used in preprocessing.jsonc and scenario.jsonc)
      "id": "student_enrollment",
      
      // Display name in voting interface
      "name": "Student Enrollment",
      
      // Explanation shown to voters
      "description": "Total number of students at the school",
      
      // CRITICAL: Whether lower/higher is better
      // "cost": lower is better (minimize)
      // "benefit": higher is better (maximize)
      "criteria_type": "cost",
      
      // Data type (currently only "numeric" supported)
      "data_type": "numeric",
      
      // Unit of measurement (for display)
      "unit": "students",
      
      // Must be true (required for voting)
      "required": true,
      
      // Column name in preprocessing output that maps to this criterion
      "source_column": "total_population",
      
      // Display order in voting interface (ascending)
      "display_order": 1
    }
  ]
}
```

**Example Values from seattle_school_closure**:
- `total_population` (cost, students) — Enrollment count
- `total_budget` (benefit, USD) — Budget allocation
- `budget_per_student` (benefit, USD per student) — Per-capita spending
- `retention_rate` (cost, ratio) — Grade 12/Grade 9 ratio
- `grade_imbalance` (benefit, imbalance_score) — Grade distribution variance

---

### 2. `scenario.jsonc` — Core Scenario Definition

The master configuration file that brings together alternatives, criteria, stakeholders, and MCDM methods.

```jsonc
{
  "schema_version": "1.0",
  
  // ========== SCENARIO IDENTITY ==========
  
  // Unique ID (lowercase, hyphens, no spaces)
  // Used to identify scenario in URL and database
  "scenario_id": "my_scenario_name",
  
  // Version in YYYY.MM format (update when scenario changes)
  "scenario_version": "2026.05",
  
  // "active" or "archived"
  "status": "active",
  
  // Short title displayed in UI
  "title": "My Decision Scenario",
  
  // Domain/category (e.g., "education", "public_safety", "environment")
  "domain": "my_domain",
  
  // Tags for search/filtering
  "tags": ["tag1", "tag2"],
  
  // One-sentence summary
  "summary": "Brief explanation of what this scenario is about.",
  
  // Multi-paragraph detailed description
  "description": "Detailed explanation of the context, background, and decision being made.",
  
  // The core decision question being asked
  "policy_question": "Which alternative should be chosen and why?",
  
  // ========== ALTERNATIVES ==========
  
  "alternatives": [
    {
      // ID that matches a row identifier in your processed data
      // (typically matches a column in the final preprocessing output)
      "id": "alternative_1",
      
      // Display name shown to voters
      "name": "Alternative One",
      
      // Description explaining this alternative
      "description": "Why someone might choose this alternative."
    }
    // ... more alternatives
  ],
  
  // ========== FILE REFERENCES ==========
  
  // These must match file names in the scenario directory
  "criteria_file": "criteria.jsonc",
  "data_sources_file": "data_sources.jsonc",
  "preprocessing_file": "preprocessing.jsonc",
  "session_rules_file": "session_rules.jsonc",
  "ui_config_file": "ui_config.jsonc",
  
  // ========== STAKEHOLDER GROUPS ==========
  
  "stakeholder_groups": [
    {
      // Unique ID for this group
      "id": "students",
      
      // Display label
      "label": "Students",
      
      // Explanation of who this group represents
      "description": "K-12 students in the school district",
      
      // Default voting power (sum of all groups should equal 1.0)
      // Can be overridden per-session
      "default_group_voting_power": 0.25
    }
    // ... more groups
  ],
  
  // ========== PREFERENCE COLLECTION METHOD ==========
  
  "preference_collection": {
    // Default method for collecting preferences
    // Options:
    //   - "criterion_linguistic_rating": Rate each criterion on a scale
    //   - "pairwise_comparison": Compare alternatives pairwise
    "default_method": "criterion_linguistic_rating",
    
    // Methods this scenario supports
    "supported_methods": ["criterion_linguistic_rating", "pairwise_comparison"],
    
    // Default scale ID (must exist in scales object below)
    "default_scale_id": "linguistic_5_point"
  },
  
  // ========== RATING SCALES ==========
  
  "scales": {
    "linguistic_5_point": {
      // "linguistic" or "numeric" (typically linguistic)
      "type": "linguistic",
      
      // True if scale is ordered (true for linguistic scales)
      "ordered": true,
      
      // Scale values from lowest to highest
      "values": [
        {
          // Display label
          "label": "Very Low",
          
          // Numeric equivalent (used internally for calculations)
          "numeric_value": 1,
          
          // Fuzzy triangular membership: [low, peak, high]
          // Describes fuzzy membership function for this linguistic value
          // Range: 0.0 to 1.0
          // Example: [0.0, 0.0, 0.3] means very low membership
          "fuzzy_value": [0.0, 0.0, 0.3]
        },
        {
          "label": "Low",
          "numeric_value": 2,
          "fuzzy_value": [0.0, 0.3, 0.5]
        },
        {
          "label": "Medium",
          "numeric_value": 3,
          "fuzzy_value": [0.3, 0.5, 0.7]
        },
        {
          "label": "High",
          "numeric_value": 4,
          "fuzzy_value": [0.5, 0.7, 1.0]
        },
        {
          "label": "Very High",
          "numeric_value": 5,
          "fuzzy_value": [0.7, 1.0, 1.0]
        }
      ]
    }
  },
  
  // ========== MCDM METHODS ==========
  
  "mcdm_methods": {
    // Weighting methods supported
    // "AHP": Analytic Hierarchy Process
    "weighting_supported": ["AHP"],
    
    // Ranking methods supported
    // "TOPSIS": Technique for Order of Preference by Similarity to Ideal Solution
    // "FUZZY_TOPSIS": TOPSIS with fuzzy membership
    "ranking_supported": ["TOPSIS", "FUZZY_TOPSIS"],
    
    // Default method to use if not specified
    "default_weighting": "AHP",
    "default_ranking": "FUZZY_TOPSIS"
  },
  
  // ========== HANDOFF CONTRACT ==========
  
  "handoff": {
    // Version of export contract for external systems
    "contract_version": "1.0",
    
    // Expected exports/outputs from this scenario
    "expected_exports": ["aggregated_weights", "rankings", "sensitivity_analysis"]
  }
}
```

**Key Rules**:
- Voting power across all stakeholder groups should sum to 1.0
- Scale IDs referenced in `default_scale_id` must exist in `scales` object
- Alternative IDs must match the identifier column in your processed data
- Criteria IDs must match IDs in `criteria.jsonc`

---

### 3. `data_sources.jsonc` — Data File References

Tells the system where to find the raw data files.

```jsonc
{
  "schema_version": "1.0",
  
  "data_sources": [
    {
      // Unique ID (used in preprocessing.jsonc to reference this source)
      "id": "raw_population_data",
      
      // Type: "csv" (only type currently supported)
      "type": "csv",
      
      // Path relative to scenario directory
      "path": "data/school_population.csv",
      
      // What this data contains
      "description": "Student enrollment by grade level for each school",
      
      // Whether this source must be present (true = required)
      "required": true
    }
  ]
}
```

---

### 4. `preprocessing.jsonc` — ETL Pipeline

Defines the transformation pipeline from raw data to criteria values. This is where the magic happens.

```jsonc
{
  "schema_version": "1.0",
  
  // Identifier for this pipeline
  "pipeline_id": "school_closure_etl",
  
  // Description of what this pipeline does
  "description": "Load school data, merge sources, compute metrics",
  
  // ========== TRANSFORMATION STEPS ==========
  
  // Steps execute in order. Each step takes input from previous step.
  "steps": [
    {
      // Unique step ID
      "id": "step_1",
      
      // Operation type: one of the following
      // - "load_csv": Load CSV file into a table
      // - "rename_columns": Rename columns
      // - "join": Join two tables
      // - "convert_types": Convert column data types
      // - "missing_values": Handle missing/null values
      // - "derive_column": Create new column from expression
      // - "approved_function": Call a Python function
      // - "select_columns": Keep only specific columns
      "type": "load_csv",
      
      // Reference to a data source ID (for load_csv only)
      "source_ref": "raw_population_data",
      
      // Input: name of table from previous step
      // (omit for first step)
      "input": null,
      
      // Output: name of table for next step
      "output": "population_data"
    },
    
    {
      "id": "step_2",
      "type": "rename_columns",
      "input": "population_data",
      "output": "population_data_renamed",
      
      // For rename_columns: old_name -> new_name mappings
      "columns": {
        "School": "school_name",
        "Grade9": "grade_9",
        "Grade10": "grade_10",
        "Grade11": "grade_11",
        "Grade12": "grade_12"
      }
    },
    
    {
      "id": "step_3",
      "type": "convert_types",
      "input": "population_data_renamed",
      "output": "population_data_typed",
      
      // For convert_types: column -> type mappings
      "columns": {
        "school_name": "string",
        "grade_9": "int",
        "grade_10": "int",
        "grade_11": "int",
        "grade_12": "int"
      }
    },
    
    {
      "id": "step_4",
      "type": "derive_column",
      "input": "population_data_typed",
      "output": "population_data_computed",
      
      // New column name
      "operation": "total_population",
      
      // Expression: column names separated by operators
      // Operators: +, -, *, /, parentheses supported
      "operands": ["grade_9", "grade_10", "grade_11", "grade_12"]
    },
    
    {
      "id": "step_5",
      "type": "approved_function",
      "input": "population_data_computed",
      "output": "population_with_metrics",
      
      // Reference to an approved function (defined below)
      "function_ref": "compute_grade_metrics"
    },
    
    {
      "id": "step_6",
      "type": "select_columns",
      "input": "population_with_metrics",
      "output": "final_output",
      
      // Keep only these columns
      "columns": {
        "school_name": true,
        "total_population": true,
        "grade_9_share": true,
        "retention_rate": true,
        "grade_imbalance": true
      }
    }
  ],
  
  // ========== APPROVED FUNCTIONS ==========
  
  // Only functions listed here can be called via approved_function steps
  "approved_functions": [
    {
      // ID referenced in preprocessing steps
      "id": "compute_grade_metrics",
      
      // File path where function is defined
      "file": "functions.py",
      
      // Function name in that file
      "callable": "compute_school_closure_metrics",
      
      // Input type: "dataframe" (only type currently supported)
      "input_type": "dataframe",
      
      // Output type: "dataframe" (only type currently supported)
      "output_type": "dataframe",
      
      // Allowed imports (strict whitelist for security)
      "allowed_imports": ["pandas", "numpy", "math"]
    }
  ],
  
  // ========== FINAL OUTPUT SPECIFICATION ==========
  
  "final_output": {
    // Name of the output table from last step
    "table": "final_output",
    
    // Column name that identifies each alternative (must be unique)
    // This matches the "id" field in scenario.json alternatives
    "alternative_id_column": "school_name",
    
    // Column names that correspond to criteria IDs
    // These MUST match "id" fields in criteria.jsonc
    "criteria_columns": {
      "total_population": "total_population",
      "total_budget": "total_budget",
      "budget_per_student": "budget_per_student",
      "grade_9_share": "grade_9_share",
      "retention_rate": "retention_rate",
      "grade_imbalance": "grade_imbalance"
    }
  },
  
  // ========== VALIDATION RULES ==========
  
  "validation": {
    // Columns that must be present in final output
    "required_columns": ["school_name", "total_population"],
    
    // Columns that cannot have null/missing values
    "no_nulls_in": ["school_name", "total_population"],
    
    // Minimum number of rows required
    "minimum_rows": 1
  }
}
```

**Supported Operation Types**:
- `load_csv`: Load CSV from data_sources
- `rename_columns`: Rename columns via mapping
- `join`: Join two tables on a column
- `convert_types`: Convert column types (int, float, string, etc.)
- `missing_values`: Handle nulls (drop rows, fill with mean/median, etc.)
- `derive_column`: Create new column from mathematical expression
- `approved_function`: Call Python function for complex transformations
- `select_columns`: Keep only specific columns

---

### 5. `session_rules.jsonc` — Voting & Session Configuration

Controls how voting sessions work and who can participate.

```jsonc
{
  "schema_version": "1.0",
  
  // Voting mode: "multi_stakeholder" (typical) or other modes
  "default_mode": "multi_stakeholder",
  
  // Require access code to join voting session?
  "require_access_code": true,
  
  // Allow same stakeholder to resubmit preferences after first submission?
  "allow_resubmission": false,
  
  // Require a moderator to lock/finalize the session before analysis?
  "require_moderator_lock": true,
  
  // Minimum number of preference submissions required before moderator can lock
  "minimum_submissions": 1,
  
  // Stakeholder groups required to participate (empty array = all optional)
  // If not empty, session cannot be locked without submissions from these groups
  "required_stakeholder_groups": []
}
```

---

### 6. `ui_config.jsonc` — Voting Interface Configuration

Controls the appearance and behavior of the voting/preference-entry interface.

```jsonc
{
  "schema_version": "1.0",
  
  // HTML/Markdown text shown at start of voting
  "intro_text": "Welcome to the voting process. Please provide your preferences...",
  
  // Instructions for rating/comparing alternatives
  "voting_instructions": "For each criterion, select the rating that best reflects your opinion...",
  
  // Show whether each criterion is a cost or benefit to voters?
  "show_criteria_type": true,
  
  // Show alternatives list before showing criteria?
  "show_alternatives_before_criteria": true
}
```

---

### 7. `functions.py` — Custom Calculation Functions

Python module containing approved functions for preprocessing.

```python
"""
Custom functions for scenario processing.

IMPORTANT CONSTRAINTS:
- Only pandas, numpy, and math imports allowed
- All functions must have type hints
- Input/output must be DataFrames
- Function must be listed in preprocessing.jsonc approved_functions
"""

import pandas as pd
import numpy as np


def compute_school_closure_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived metrics for school closure scenario.
    
    Input columns required:
      - grade_9, grade_10, grade_11, grade_12
    
    Output columns added:
      - retention_rate: grade_12 / grade_9 (0 if grade_9 == 0)
      - grade_imbalance: coefficient of variation across grades
    
    Args:
        df: DataFrame with grade enrollment columns
    
    Returns:
        DataFrame with original columns plus new metric columns
    """
    df = df.copy()
    
    # Retention rate: what proportion of Grade 9 students reach Grade 12?
    df['retention_rate'] = df.apply(
        lambda row: row['grade_12'] / row['grade_9'] if row['grade_9'] > 0 else 0,
        axis=1
    )
    
    # Grade imbalance: coefficient of variation (std / mean) across grades
    grade_cols = ['grade_9', 'grade_10', 'grade_11', 'grade_12']
    grades = df[grade_cols]
    df['grade_imbalance'] = (
        grades.std(axis=1) / grades.mean(axis=1)
    ).fillna(0)
    
    return df
```

**Rules**:
- Only imports: `pandas`, `numpy`, `math`
- All functions must have type hints (input/output DataFrame)
- Function must be async-safe (no blocking I/O)
- Must be listed in `preprocessing.jsonc` `approved_functions`

---

### 8. Sample Data Files — `data/`

CSV files containing raw data for alternatives.

**Example: `data/schools.csv`**
```
school_name,grade_9,grade_10,grade_11,grade_12,budget
Ballard HS,450,440,420,410,12500000
Center School,120,115,110,105,3500000
Roosevelt HS,500,485,475,460,15000000
```

**Data Type Handling**:
- Numeric columns: integers or floats (will be converted via preprocessing)
- String columns: category names (school names, region codes, etc.)
- Missing values: Use empty cells or NULL (handled in preprocessing)

---

## Validation Checklist

Before running your scenario, verify:

- [ ] **Scenario ID**: Unique, lowercase, hyphenated
- [ ] **Alternatives**: At least 2, matching `alternative_id_column` in preprocessing
- [ ] **Criteria**: At least 1, IDs match `criteria_columns` in preprocessing
- [ ] **Stakeholder Groups**: Sum of voting powers equals 1.0
- [ ] **Scale**: Default scale ID exists in scales object
- [ ] **Preprocessing**: 
  - [ ] All data sources exist in `data/` directory
  - [ ] All steps execute in logical order
  - [ ] Final output has `alternative_id_column` and all `criteria_columns`
  - [ ] All approved functions are defined in `functions.py`
- [ ] **Files**: All 8 components present (7 configs + data directory)
- [ ] **Extensions**: JSON files are `.jsonc`, not `.json` (except session_rules which can be `.jsonc`)

---

## Common Patterns & Examples

### Adding a New Criterion

1. Add entry to `criteria.jsonc` with unique ID, name, type (cost/benefit), unit
2. Add transformation step in `preprocessing.jsonc` to derive this column
3. Add mapping in `preprocessing.jsonc` `final_output.criteria_columns`
4. Update `scenario.jsonc` if this affects stakeholder understanding

### Adding a New Alternative

1. Ensure data row exists in CSV with unique identifier
2. Add entry to `scenario.jsonc` `alternatives` array with matching ID
3. Verify preprocessing produces row for this alternative

### Changing Stakeholder Groups

1. Update `scenario.jsonc` `stakeholder_groups` array
2. Adjust voting powers so they sum to 1.0
3. Update `session_rules.jsonc` `required_stakeholder_groups` if needed

---

## Loader Integration Note

**IMPORTANT**: The scenario loader is configured to **skip the `_template` directory** to prevent it from being loaded as an actual scenario. The template is for reference only.

---

## Working Example

For a complete, working example, see the `seattle_school_closure/` directory.

To understand how a scenario is loaded and used, examine:
- `stakeholder_polling/dashboard/scenario_loader.py` — How scenarios are discovered and loaded
- `stakeholder_polling/dashboard/preprocessing.py` — How the ETL pipeline executes

---

## Troubleshooting

**Scenario not loading?**
- Check that scenario directory name matches `scenario_id` in `scenario.jsonc`
- Verify all file extensions (.jsonc for configs, .py for functions)
- Check JSON syntax in all .jsonc files (use a JSON validator)

**Preprocessing failing?**
- Verify CSV file paths in `data_sources.jsonc` are relative to scenario directory
- Check column names in preprocessing steps match actual CSV columns
- Ensure approved functions are defined in `functions.py`

**Voting interface not showing data?**
- Verify `final_output.criteria_columns` mappings match criterion IDs
- Check that preprocessing output has all required columns
- Ensure alternative IDs match `scenario.json` alternatives

---
