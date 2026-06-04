# Stakeholder Polling Application

A Streamlit-based web application for conducting Multi-Criteria Decision Making (MCDM) stakeholder polling sessions. This application enables collaborative decision-making by allowing stakeholders to express preferences using AHP (Analytic Hierarchy Process) and aggregate their preferences using TOPSIS (Technique for Order Preference by Similarity to Ideal Solution).

## Features

- **Public Interface**: Stakeholders can submit preferences for policy scenarios
- **Preference Collection**: Linguistic rating scales for criteria evaluation
- **AHP Weighting**: Automatic pairwise comparison matrix generation and weight calculation
- **TOPSIS Ranking**: Aggregate stakeholder preferences to rank policy alternatives
- **Admin Dashboard**: Moderators can manage sessions, participants, submissions, and exports
- **Access Control**: Session-based access with optional access codes
- **Results Visualization**: Interactive charts showing submission analytics and final rankings
- **Data Export**: Export results and submissions to Excel spreadsheets
- **SQLite Database**: Portable database for session data, participants, and submissions

## Prerequisites

- **Python 3.9** or higher
- **pip** (Python package manager)
- **Git** (for cloning the repository)

## Installation

### 1. Clone the Repository

```bash
cd /path/to/your/workspace
git clone <repository-url>
cd stakeholder-polling
```

### 2. Create a Virtual Environment (Recommended)

```bash
# macOS/Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- **streamlit** (web application framework)
- **pydecision** (MCDM algorithms: AHP, fuzzy AHP, TOPSIS)
- **pandas** (data manipulation and analysis)
- **numpy** (numerical computations)
- **plotly** (interactive visualizations)
- **openpyxl** (Excel file export)

## Configuration

### Environment Variables

The application supports the following environment variables for configuration:

#### Database Path
- **`POLLING_DB_PATH`** (optional): Path to the SQLite database file
  - Default: `database/stakeholder_polling.sqlite`
  - Example: `export POLLING_DB_PATH=/tmp/polling.sqlite`

#### Admin Authentication
You must configure admin authentication using one of these methods:

##### Option 1: Pre-hashed Password (Recommended for Production)
```bash
export ADMIN_PASSWORD_HASH="<sha256-hash-of-password>"
```

To generate a password hash in Python:
```python
import hashlib
password = "your_password"
hashed = hashlib.sha256(password.encode("utf-8")).hexdigest()
print(hashed)
```

##### Option 2: Plain Text Password (Development Only)
```bash
export ADMIN_PASSWORD="your_password"
```

Note: The application will hash plain passwords internally. For production, use `ADMIN_PASSWORD_HASH` instead.

#### Streamlit Secrets (Alternative Configuration)
You can also configure these using Streamlit's `.streamlit/secrets.toml` file:

```toml
# .streamlit/secrets.toml
ADMIN_PASSWORD_HASH = "sha256_hash_here"
```

## Running the Application

### Start the Development Server

```bash
streamlit run app.py
```

The application will launch and display a URL (typically `http://localhost:8501`). Open this URL in your web browser.

### Command-Line Options

```bash
# Run on a specific port
streamlit run app.py --server.port 8502

# Run in headless mode (no browser auto-open)
streamlit run app.py --logger.level=debug

# For additional Streamlit options
streamlit run app.py --help
```

## Application Structure

### Directory Layout

```
stakeholder-polling/
├── app.py                          # Main application entry point
├── requirements.txt                # Python dependencies
├── database/                       # SQLite database files (auto-created)
│   └── stakeholder_polling.sqlite
├── exports/                        # Exported Excel files (auto-created)
├── logs/                           # Application logs (auto-created)
├── scenarios/                      # Scenario configuration files
│   └── seattle_school_closure/     # Example scenario
│       ├── scenario.json           # Scenario metadata
│       ├── criteria.json           # Evaluation criteria
│       ├── data_sources.json       # Data source references
│       ├── preprocessing.json      # Data preprocessing pipeline
│       ├── session_rules.json      # Session configuration rules
│       ├── ui_config.json          # UI customization settings
│       └── data/                   # Scenario data files
└── dashboard/                      # Application modules
    ├── db.py                       # Database initialization
    ├── auth.py                     # Authentication logic
    ├── repositories.py             # Data access layer
    ├── scenario_loader.py          # Scenario configuration loader
    ├── preferences.py              # Preference validation
    ├── weighting.py                # AHP weight calculation
    ├── ranking.py                  # TOPSIS ranking
    ├── preprocessing.py            # Data preprocessing
    ├── ui_components.py            # Shared UI components
    ├── session_manager.py          # Session state management
    ├── page_registry.py            # Page routing configuration
    ├── public/                     # Public user pages
    │   ├── submit.py               # Preference submission page
    │   ├── results.py              # Results visualization page
    │   └── about.py                # About page
    └── admin/                      # Admin/moderator pages
        ├── home.py                 # Admin login & home
        ├── sessions.py             # Manage polling sessions
        ├── participants.py         # Manage session participants
        ├── submissions.py          # Review & analyze submissions
        ├── processing.py           # Process session data
        └── exports.py              # Export results
```

## Admin Setup & Authentication

### First-Time Admin Access

1. **Set the admin password** using environment variables (see Configuration section above)
2. **Navigate to the application** in your browser
3. **Click "Moderator Login"** (or navigate to `/admin`)
4. **Enter your password** to authenticate

### Admin Dashboard Features

Once authenticated, administrators can:

- **Manage Sessions**: Create, update, and manage polling sessions
- **Manage Participants**: Add stakeholders, assign voting weights, and manage access codes
- **Manage Submissions**: View, filter, and analyze stakeholder submissions
- **Session Processing**: Configure weighting and ranking methods
- **Exports**: Export submission data and results to Excel

## Usage Guide

### For Public Users (Stakeholders)

1. **Access the Submit Page**: Navigate to the home page or "Submit Preference"
2. **Select a Session**: Choose an open polling session from the dropdown
3. **Enter Access Code**: If required, provide the access code sent by the moderator
4. **Review Scenario**: Read the policy scenario and understand the criteria
5. **Rate Criteria**: Select a linguistic rating (Very Low to Very High) for each criterion
6. **Submit Preferences**: Submit your preferences to complete the process
7. **View Results**: Navigate to "View Results" to see aggregated polling results

### For Administrators (Moderators)

1. **Create a Session**: Go to "Manage Sessions" and create a new polling session
2. **Configure Session**: Set weighting method (AHP), ranking method (TOPSIS), and access rules
3. **Add Participants**: Add stakeholders and assign voting weights
4. **Generate Access Codes**: Create and distribute access codes to participants
5. **Monitor Submissions**: Track submission progress on the "Manage Submissions" page
6. **Process Results**: Run aggregation to calculate weights and final rankings
7. **Export Results**: Export submission analytics and final rankings to Excel

## Database

The application uses **SQLite** for data persistence. Key tables include:

- **scenarios**: Scenario configurations and metadata
- **polling_sessions**: Active and completed polling sessions
- **stakeholders**: Stakeholder information and aliases
- **session_participants**: Participants in specific sessions
- **preference_submissions**: Submitted preferences and rankings
- **voting_power_assignments**: Voting weight assignments

The database is automatically initialized on first run. No manual migration steps are required.

## Troubleshooting

### Admin Login Not Working
- **Verify**: Confirm `ADMIN_PASSWORD` or `ADMIN_PASSWORD_HASH` is set correctly
- **Check Logs**: Look for authentication errors in the Streamlit console output
- **Reset**: Stop the app, clear `.streamlit/` cache, and restart

### Missing Scenarios
- **Add Scenarios**: Place scenario folders in the `scenarios/` directory with proper JSON structure
- **Verify Structure**: Ensure each scenario has `scenario.json`, `criteria.json`, and other required config files

### Database Errors
- **Check Permissions**: Ensure the `database/` directory is writable
- **Check Path**: If using `POLLING_DB_PATH`, verify the path is correct and the parent directory exists
- **Clear Database**: Delete the SQLite file to reset (this will clear all data)

### Port Already in Use
```bash
streamlit run app.py --server.port 8502
```

## Development Notes

- The application uses **Streamlit**'s multi-page routing for navigation
- **pydecision** library provides MCDM algorithms (AHP for weighting, TOPSIS for ranking)
- **Pandas** and **NumPy** handle numerical computations
- **Plotly** provides interactive visualizations
- **SQLite** database uses WAL mode for better concurrency
