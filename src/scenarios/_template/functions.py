"""
Custom functions for scenario data preprocessing.

IMPORTANT CONSTRAINTS:
- Only pandas, numpy, and math imports are allowed
- All functions must have type hints (input and return types)
- Input and output must be DataFrames
- Only functions listed in preprocessing.jsonc approved_functions can be called
- Function must be async-safe (no blocking I/O)

This is a TEMPLATE file. Do not use directly.
Copy this entire directory and customize the function implementations for your scenario.
"""

import pandas as pd
import numpy as np
import math


def compute_scenario_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute derived metrics for your scenario.
    
    This is a template function showing common patterns for metric calculation.
    
    TEMPLATE: Customize this function for your specific scenario needs.
    
    Args:
        df: DataFrame containing base data (enrollment, budget, etc.)
           Must have columns referenced in preprocessing steps
    
    Returns:
        DataFrame with original columns plus new computed columns
    
    Example Input Columns:
        - enrollment (int): number of students/users
        - budget (float): allocation in dollars
        - grade_9, grade_10, grade_11, grade_12 (int): enrollment by level
    
    Example Output Columns (added by this function):
        - efficiency_ratio (float): budget / enrollment
        - retention_rate (float): grade_12 / grade_9
        - grade_imbalance (float): coefficient of variation across grades
    """
    
    # Always make a copy to avoid modifying input
    df = df.copy()
    
    # ========== PATTERN 1: SIMPLE RATIO ==========
    # Calculate budget per student (if columns exist)
    if 'budget' in df.columns and 'enrollment' in df.columns:
        df['efficiency_ratio'] = df['budget'] / df['enrollment']
    
    # ========== PATTERN 2: CONDITIONAL CALCULATION ==========
    # Calculate retention: handle division by zero
    if all(col in df.columns for col in ['grade_12', 'grade_9']):
        df['retention_rate'] = df.apply(
            lambda row: (
                row['grade_12'] / row['grade_9']
                if row['grade_9'] > 0 else 0
            ),
            axis=1
        )
    
    # ========== PATTERN 3: MULTI-COLUMN AGGREGATION ==========
    # Calculate coefficient of variation (imbalance) across multiple columns
    if all(col in df.columns for col in ['grade_9', 'grade_10', 'grade_11', 'grade_12']):
        grade_cols = ['grade_9', 'grade_10', 'grade_11', 'grade_12']
        grades = df[grade_cols]
        
        # Coefficient of variation: std / mean
        # Represents how unbalanced grade distribution is
        df['grade_imbalance'] = (
            grades.std(axis=1) / grades.mean(axis=1)
        ).fillna(0)
    
    # ========== PATTERN 4: CUSTOM LOGIC WITH numpy ==========
    # Example: Calculate satisfaction categories based on score
    if 'satisfaction_score' in df.columns:
        df['satisfaction_level'] = pd.cut(
            df['satisfaction_score'],
            bins=[0, 20, 40, 60, 80, 100],
            labels=['Very Low', 'Low', 'Medium', 'High', 'Very High']
        )
    
    # ========== PATTERN 5: CUMULATIVE CALCULATION ==========
    # Example: Rank items and calculate percentile rank
    if 'enrollment' in df.columns:
        df['enrollment_percentile'] = df['enrollment'].rank(pct=True)
    
    return df


# ========== ADDITIONAL FUNCTION TEMPLATES ==========
# Add more functions as needed. Each must:
# 1. Have a unique name
# 2. Have type hints (input: pd.DataFrame, output: pd.DataFrame)
# 3. Be listed in preprocessing.jsonc approved_functions
# 4. Only use pandas, numpy, math imports


def advanced_metric_calculation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Example of more complex metric calculation.
    
    TEMPLATE: Customize for your scenario.
    
    Args:
        df: Input DataFrame
    
    Returns:
        DataFrame with additional computed columns
    """
    df = df.copy()
    
    # Example: Multi-step calculation
    if 'population' in df.columns and 'resources' in df.columns:
        # Step 1: Calculate per-capita resources
        df['per_capita_resources'] = df['resources'] / df['population']
        
        # Step 2: Normalize to 0-1 scale
        max_val = df['per_capita_resources'].max()
        if max_val > 0:
            df['resource_index'] = df['per_capita_resources'] / max_val
        else:
            df['resource_index'] = 0.0
    
    return df


# ========== GUIDELINES FOR CUSTOM FUNCTIONS ==========
#
# 1. COLUMN EXISTENCE CHECKS:
#    Always check if columns exist before using them
#    if 'column_name' in df.columns:
#        ...do something...
#
# 2. HANDLE DIVISION BY ZERO:
#    Use .fillna() or conditional expressions
#    df['ratio'] = df.apply(lambda x: x['a']/x['b'] if x['b']>0 else 0, axis=1)
#
# 3. NORMALIZE VALUES:
#    If creating indices/scores, normalize to consistent range (0-1, 1-5)
#    df['normalized'] = (df['raw'] - df['raw'].min()) / (df['raw'].max() - df['raw'].min())
#
# 4. HANDLE NULL VALUES:
#    Use .fillna() strategically
#    df['metric'].fillna(df['metric'].median(), inplace=True)
#
# 5. DOCUMENT ASSUMPTIONS:
#    In docstring, specify required input columns and expected output format
#
# 6. KEEP IT SIMPLE:
#    Complex calculations should be documented clearly
#    Add comments for non-obvious logic
