# Data Insighter

Data Insighter is a Flask-based data analysis workspace for uploading business datasets, profiling them, generating Plotly visualizations, creating saved dashboards, replaying dataset pipelines, and exporting insight summaries.

## What the app currently does

- User registration and login with hashed passwords and CSRF protection
- Upload CSV, TSV, JSON, JSONL, Excel, and Parquet files
- Load bundled sample datasets
- Generate dataset summaries, semantic profiles, quality alerts, and executive takeaways
- Apply lightweight transforms such as deduplication, text trimming, missing-value filling, date-part extraction, and calculated columns
- Rebuild derived datasets from recorded transform and join pipelines, or roll back to parent versions
- Suggest relationships across uploaded datasets and create joined datasets
- Define reusable business measures
- Refresh saved dataset versions, detect schema drift, and review workspace freshness states
- Review sensitivity hints, activity history, and downstream dashboard/measure impact for the active dataset
- Build dashboard layouts from analysis charts and export dashboards as interactive HTML or PNG
- Export executive reports as HTML or Markdown

## Current architecture

Backend modules:

- `app.py` - Flask routes, auth, session flow, export flow
- `data_processor.py` - dataset profiling and insight summary orchestration
- `insight_engine.py` - anomaly, contribution, funnel, cohort, seasonality, and variance insights
- `semantic_model.py` - semantic role inference for columns
- `data_model_service.py` - key profiling, relationship suggestions, and joins
- `dataset_pipeline_service.py` - replayable dataset rebuild helpers
- `dataset_refresh_service.py` - dataset refresh and schema-drift detection
- `governance_service.py` - sensitivity and trust summary helpers
- `measure_service.py` - reusable measure calculations
- `transform_service.py` - dataset transformation operations
- `visualization_generator.py` - Plotly chart generation and export
- `workspace_store.py` - SQLite-backed persistence for datasets, dashboards, relationships, measures, refresh jobs, and audit events
- `auth_store.py` - SQLite-backed account storage and legacy user migration
- `report_service.py` - executive summary report generation
- `file_utils.py` - multi-format ingestion with encoding handling

Frontend surface:

- `templates/` - Jinja views for home, upload, analysis, dashboard, auth, and export HTML
- `static/css/` - shared, upload, and analysis styling
- `static/js/upload.js` - upload and sample-dataset client flow

## Data storage model

- Uploaded files are stored under `uploads/`
- Sample files live under `sample_datasets/`
- Workspace metadata is stored in SQLite under `workspace_data/`
- Account metadata is stored in SQLite and legacy `users.json` data is migrated on demand
- The app uses Flask session cookies plus server-side workspace storage
- If `SECRET_KEY` is not set, the app creates a stable local fallback key in `.local_secret_key`

## Supported file types

- `.csv`
- `.tsv`
- `.json`
- `.jsonl`
- `.ndjson`
- `.xlsx`
- `.xls`
- `.parquet`

## Local development

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Optionally create a `.env` file with:

```env
SECRET_KEY=your_secret_key_here
```

4. Start the app with:

```bash
python app.py
```

5. Open `http://127.0.0.1:5000`.

PowerShell helpers:

- `scripts/start_local_server.ps1`
- `scripts/start_local_server.ps1 -OpenBrowser`
- `scripts/open_local_browser.ps1`
- `scripts/tail_local_logs.ps1`
- `scripts/verify_local_ui.ps1`
- `scripts/start_refresh_worker.ps1`

If the embedded browser blocks `localhost` or `127.0.0.1`, use the desktop-browser fallback helpers instead:

```powershell
.\scripts\start_local_server.ps1 -OpenBrowser
.\scripts\verify_local_ui.ps1
```

`verify_local_ui.ps1` captures real browser screenshots and DOM dumps into `output\ui_verification\...` using a clean Chrome/Edge profile with extensions disabled, which avoids `ERR_BLOCKED_BY_CLIENT` cases caused by browser-side blockers. You can also pass `-Username` and `-Password` to verify authenticated routes and generate both `verification_summary.json` and `verification_report.md`.

If you want refresh schedules to run outside the web process, start the durable worker helper:

```powershell
.\scripts\start_refresh_worker.ps1
```

## Tests

Run the current automated tests with:

```bash
python -m pytest -q
```

The current suite covers:

- connector/file ingestion behavior
- dataset pipeline replay, undo, and rebuild
- dataset refresh and schema drift detection
- governance summary behavior
- measure calculations
- relationship/key profiling
- advanced insight helpers
- report HTML escaping
- auth edge cases around duplicate emails and email login

## Notes

- Dashboard browser state is scoped by user and active dataset.
- Exported report and dashboard HTML is escaped to avoid embedding executable markup from uploaded data.
- Workspace persistence is currently file-backed, not database-backed.
