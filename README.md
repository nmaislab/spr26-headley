# Stakeholder Polling Research Project

This repository contains a Streamlit-based web application for conducting Multi-Criteria Decision Making (MCDM) stakeholder polling sessions, along with validation test data and scenario configurations.

## Project Structure

```
spr26-headley/
├── README.md                                   # This file
├── src/                                        # Application source code
│   ├── app.py                                  # Main entry point
│   ├── requirements.txt                        # Python dependencies
│   ├── README.md                               # Application documentation
│   ├── dashboard/                              # Application modules
│   └── scenarios/                              # Scenario configurations
│       ├── _template/                          # Template scenario
│       ├── public_safety_resource_allocation/  # Public safety scenario
│       └── seattle_school_closure/             # School closure scenario
├── validation-test-data/                       # Synthetic test data
│   ├── README.md                               # Test data documentation
│   └── *.csv                                   # Submission trial sets
└── jupyter-notebooks/                          # Analysis notebooks
```

## Quick Links

| Component | Description | Link |
|-----------|-------------|------|
| **Jupyter Notebooks** | Notebooks for validation testing | [jupyter-notebooks/](jupyter-notebooks/)
| **Application** | Full application documentation | [src/README.md](src/README.md) |
| **Test Data** | Synthetic submission data for validation | [validation-test-data/README.md](validation-test-data/README.md) |
| **Scenarios** | Policy scenario configurations | [src/scenarios/](src/scenarios/README.md) |

## Getting Started

### Running the Application

```bash
cd src
pip install -r requirements.txt
streamlit run app.py
```

### Using Test Data

1. Create a session with the **Public Safety Resource Allocation** scenario
2. Import submissions from the trial CSV files in [validation-test-data/](validation-test-data/)
3. Process and export results

See [validation-test-data/README.md](validation-test-data/README.md) for detailed usage instructions.

### Running Jupyter Notebooks

> **Note**: Before opening any notebooks in [jupyter-notebooks/](jupyter-notebooks/), you must install the required packages:

```bash
cd src
pip install -r requirements.txt
```

Then open the notebooks using Jupyter Lab or VS Code.

## Features

- **AHP Weighting**: Analytic Hierarchy Process for criteria weight calculation
- **TOPSIS Ranking**: Technique for Order Preference by Similarity to Ideal Solution
- **Multi-stakeholder Aggregation**: Combine preferences from multiple stakeholder groups
- **Access Control**: Session-based access with optional access codes
- **Excel Export**: Export results and submissions

## Scenarios

### Public Safety Resource Allocation

Prioritize public safety resource allocation across different criteria (crime reduction, response time, community coverage, etc.).

### Seattle School Closure

Evaluate school closure decisions based on demographic and budgetary criteria.

## Documentation

- [Application README](src/README.md) — Complete setup and usage guide
- [Test Data README](validation-test-data/README.md) — Synthetic data descriptions and import instructions
