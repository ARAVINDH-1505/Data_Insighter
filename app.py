from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for, session
from io import BytesIO
import os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import json
from functools import wraps
from data_processor import DataProcessor
from visualization_generator import VisualizationGenerator
import numpy as np
from datetime import datetime
import time
import secrets
import tempfile
from dataset_pipeline_service import build_dataset_from_record, dataset_pipeline_steps, supports_pipeline_rebuild
from file_utils import SUPPORTED_EXTENSIONS, read_data_file
from dotenv import load_dotenv
from workspace_store import (
    create_dashboard_record,
    create_dataset_record,
    create_measure_record,
    create_relationship_record,
    ensure_workspace_dirs,
    get_dashboard_record,
    get_dataset_record,
    list_all_dataset_records,
    list_dashboard_records,
    list_dataset_records,
    list_measure_records,
    list_relationship_records,
)
from transform_service import apply_transform
from report_service import build_report_payload
from data_model_service import join_datasets, suggest_relationships
from measure_service import evaluate_measure

# Load environment variables from .env file
load_dotenv()

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_SECRET_KEY_FILE = os.path.join(APP_ROOT, '.local_secret_key')
SAVING_DASHBOARD_TEMPLATE = os.path.join(APP_ROOT, 'templates', 'saving_dashboard.html')


def load_app_secret_key():
    configured = os.environ.get('SECRET_KEY')
    if configured:
        return configured

    if os.path.exists(LOCAL_SECRET_KEY_FILE):
        try:
            with open(LOCAL_SECRET_KEY_FILE, 'r', encoding='utf-8') as handle:
                persisted = handle.read().strip()
            if persisted:
                return persisted
        except OSError:
            pass

    generated = secrets.token_hex(32)
    try:
        with open(LOCAL_SECRET_KEY_FILE, 'w', encoding='utf-8') as handle:
            handle.write(generated)
    except OSError:
        pass
    return generated


app = Flask(__name__)
# FIX #1: Secret key from environment variable, otherwise a stable local fallback file
app.secret_key = load_app_secret_key()

app.config['UPLOAD_FOLDER'] = os.path.join(APP_ROOT, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  
app.config['SAMPLE_DATASETS'] = os.path.join(APP_ROOT, 'sample_datasets')

# Ensure required folders exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['SAMPLE_DATASETS'], exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'css'), exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'js'), exist_ok=True)
os.makedirs(os.path.join(app.static_folder, 'images'), exist_ok=True)
ensure_workspace_dirs()

# ---------- User store helpers (FIX #2 + #3) ----------
USERS_FILE = os.path.join(APP_ROOT, 'users.json')


def _normalize_email(email):
    return (email or '').strip().lower()

def _load_users():
    """Load users from JSON file."""
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_json_file_atomic(path, payload):
    temp_path = None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=os.path.dirname(path),
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _save_users(users):
    """Save users to JSON file."""
    _write_json_file_atomic(USERS_FILE, users)


def _email_exists(users, email, exclude_username=None):
    normalized = _normalize_email(email)
    for username, details in users.items():
        if exclude_username and username == exclude_username:
            continue
        if _normalize_email(details.get('email')) == normalized:
            return True
    return False

# Start with an empty user store; accounts should be created through registration.
if not os.path.exists(USERS_FILE):
    _save_users({})

# ---------- CSRF helpers (FIX #4) ----------
def generate_csrf_token():
    """Generate a CSRF token and store it in the session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def validate_csrf_token():
    """Validate the CSRF token from form data or JSON against the session."""
    payload = request.get_json(silent=True) or {}
    token = request.form.get('_csrf_token') or payload.get('_csrf_token', '')
    if not token or token != session.get('_csrf_token'):
        return False
    return True

# Make csrf_token available in all templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token


@app.context_processor
def inject_template_globals():
    return {'current_year': datetime.now().year}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in SUPPORTED_EXTENSIONS

def error_response(message, status_code):
    return jsonify({'success': False, 'error': message}), status_code

def is_path_within_directory(directory, target_path):
    """Ensure target_path resolves inside directory."""
    try:
        base_dir = os.path.abspath(directory)
        target = os.path.abspath(target_path)
        return os.path.commonpath([base_dir, target]) == base_dir
    except ValueError:
        return False

def cleanup_uploaded_file(filepath):
    """Delete files only when they are inside the uploads directory."""
    if filepath and os.path.exists(filepath) and is_path_within_directory(app.config['UPLOAD_FOLDER'], filepath):
        try:
            os.remove(filepath)
        except OSError:
            pass


def safe_filename_stem(value, default='export'):
    """Create a safe filename stem for generated artifacts."""
    normalized = secure_filename((value or '').strip())
    if not normalized:
        return default
    return normalized[:80]


def escape_embedded_json(payload):
    """Escape JSON so it is safe to embed inside HTML script tags."""
    return (
        json.dumps(payload, cls=NumpyEncoder)
        .replace('&', '\\u0026')
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
    )


def is_tracked_dataset_path(username, filepath):
    """Return True when the path belongs to a saved dataset for this user."""
    if not filepath:
        return False

    normalized = os.path.abspath(filepath)
    for dataset in list_dataset_records(username):
        stored_path = dataset.get('stored_path')
        if stored_path and os.path.abspath(stored_path) == normalized:
            return True
    return False


def is_any_tracked_dataset_path(filepath):
    """Return True when the path belongs to any saved dataset record."""
    if not filepath:
        return False

    normalized = os.path.abspath(filepath)
    for dataset in list_all_dataset_records():
        stored_path = dataset.get('stored_path')
        if stored_path and os.path.abspath(stored_path) == normalized:
            return True
    return False

def store_derived_dataset(df, base_name):
    safe_base = secure_filename(os.path.splitext(base_name)[0] or 'dataset')
    filename = f"{safe_base}_derived_{int(time.time())}_{secrets.token_hex(4)}.csv"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    df.to_csv(filepath, index=False)
    return filepath, filename


def dataset_metadata(display_name, columns, extra=None):
    metadata = {
        'display_name': display_name,
        'columns': columns,
        'lineage_steps': [],
        'pipeline_steps': [],
    }
    if extra:
        metadata.update(extra)
    return metadata

def login_required(view):
    """Require an authenticated session for app pages and APIs."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get('user'):
            return view(*args, **kwargs)

        if request.is_json or request.method != 'GET':
            return error_response('Authentication required', 401)

        flash('Please log in to continue.', 'error')
        return redirect(url_for('login'))
    return wrapped_view

def flatten_json_data(data):
    """Recursively flatten nested JSON structures"""
    def flatten_dict(d, parent_key='', sep='_'):
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(flatten_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                if len(v) > 0:
                    if isinstance(v[0], dict):
                        # Handle list of dictionaries
                        for i, item in enumerate(v):
                            items.extend(flatten_dict(item, f"{new_key}_{i}", sep=sep).items())
                    else:
                        # Handle list of simple values
                        items.append((new_key, v))
                else:
                    items.append((new_key, v))
            else:
                items.append((new_key, v))
        return dict(items)

    if isinstance(data, list):
        return [flatten_dict(item) if isinstance(item, dict) else item for item in data]
    elif isinstance(data, dict):
        return flatten_dict(data)
    else:
        return data

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8,
                          np.int16, np.int32, np.int64, np.uint8,
                          np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        elif isinstance(obj, pd.Series):
            return obj.tolist()
        elif isinstance(obj, pd.Timestamp):
            return obj.strftime('%Y-%m-%d %H:%M:%S')
        return super().default(obj)

@app.route('/')
@login_required
def index():
    sample_datasets = [f for f in os.listdir(app.config['SAMPLE_DATASETS']) 
                      if allowed_file(f)]
    recent_datasets = list_dataset_records(session['user'])[:6]
    current_dataset = None
    if session.get('current_dataset_id'):
        current_dataset = get_dataset_record(session['user'], session['current_dataset_id'])
    return render_template(
        'index.html',
        sample_datasets=sample_datasets,
        recent_datasets=recent_datasets,
        current_dataset=current_dataset,
    )

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        if not validate_csrf_token():
            return error_response('Invalid form submission. Please refresh and try again.', 400)

        if 'file' not in request.files:
            return error_response('No file part', 400)
        
        file = request.files['file']
        if file.filename == '':
            return error_response('No selected file', 400)
        
        if file and allowed_file(file.filename):
            filepath = None
            try:
                # Create a safe filename
                original_name = secure_filename(file.filename)
                extension = file.filename.rsplit('.', 1)[1].lower()
                basename = os.path.splitext(original_name)[0] or 'upload'
                filename = f"{basename}_{int(time.time())}_{secrets.token_hex(4)}.{extension}"
                
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                
                # Store filepath in session
                session['current_filepath'] = filepath
                
                # Read the file
                df = read_data_file(filepath)
                dataset_record = create_dataset_record(
                    session['user'],
                    source_name=file.filename,
                    stored_path=filepath,
                    source_type='upload',
                    row_count=len(df),
                    column_count=len(df.columns),
                    metadata=dataset_metadata(
                        file.filename,
                        df.columns.tolist(),
                        {
                            'source_extension': extension,
                        },
                    ),
                )
                session['current_dataset_id'] = dataset_record['id']
                
                # Prepare preview data
                preview_data = []
                for _, row in df.head(10).iterrows():
                    row_dict = {}
                    for col in df.columns:
                        value = row[col]
                        if isinstance(value, (np.integer, np.floating)):
                            value = float(value)
                        elif isinstance(value, np.bool_):
                            value = bool(value)
                        elif isinstance(value, (pd.Timestamp, datetime)):
                            value = value.strftime('%Y-%m-%d %H:%M:%S')
                        elif isinstance(value, (list, dict)):
                            value = json.dumps(value)
                        elif pd.isna(value):
                            value = None
                        else:
                            value = str(value)
                        row_dict[col] = value
                    preview_data.append(row_dict)

                # Prepare column information
                columns_info = []
                for col in df.columns:
                    col_type = str(df[col].dtype)
                    non_null_count = df[col].count()
                    null_count = df[col].isna().sum()
                    unique_count = df[col].nunique()
                    
                    columns_info.append({
                        'name': col,
                        'type': col_type,
                        'non_null_count': int(non_null_count),
                        'null_count': int(null_count),
                        'unique_count': int(unique_count)
                    })

                data_info = {
                    'filename': filename,
                    'total_rows': len(df),
                    'total_columns': len(df.columns),
                    'columns': columns_info,
                    'preview_data': preview_data
                }
                
                return jsonify({
                    'success': True,
                    'data': data_info
                })
                
            except Exception as e:
                # Clean up the file if there was an error
                cleanup_uploaded_file(filepath)
                return error_response(str(e), 400)

        return error_response('Unsupported file format. Please upload a CSV or JSON file.', 400)
    
    # Get list of sample datasets for the template
    sample_datasets = [f for f in os.listdir(app.config['SAMPLE_DATASETS']) 
                      if allowed_file(f)]
    recent_datasets = list_dataset_records(session['user'])[:6]
    return render_template('upload.html', sample_datasets=sample_datasets, recent_datasets=recent_datasets)

@app.route('/sample/<filename>')
@login_required
def use_sample(filename):
    filepath = os.path.join(app.config['SAMPLE_DATASETS'], secure_filename(filename))
    # FIX #5: Validate the path stays inside SAMPLE_DATASETS directory
    if not is_path_within_directory(app.config['SAMPLE_DATASETS'], filepath):
        return error_response('Invalid file path', 400)
    
    if os.path.exists(filepath) and allowed_file(filename):
        try:
            df = read_data_file(filepath)
            # FIX #5: Store filepath only in server session, never send to client
            session['current_filepath'] = filepath
            dataset_record = create_dataset_record(
                session['user'],
                source_name=filename,
                stored_path=filepath,
                source_type='sample',
                row_count=len(df),
                column_count=len(df.columns),
                metadata=dataset_metadata(
                    filename,
                    df.columns.tolist(),
                    {
                        'source_extension': filename.rsplit('.', 1)[1].lower(),
                    },
                ),
            )
            session['current_dataset_id'] = dataset_record['id']
            
            data_info = {
                'columns': df.columns.tolist(),
                'num_rows': len(df),
                'num_columns': len(df.columns),
                'dtypes': df.dtypes.astype(str).to_dict(),
                'preview': df.head(5).to_dict(orient='records'),
                'missing_values': df.isnull().sum().to_dict()
            }
            
            return jsonify({
                'success': True,
                'data': data_info
            })
        except Exception as e:
            return error_response(str(e), 400)
    return error_response('Sample dataset not found', 404)

@app.route('/analysis')
@login_required
def analysis():
    # FIX #5: Never read filepath from query params — use session only
    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        flash('Please upload a file or select a sample dataset first', 'error')
        return redirect(url_for('index'))
    try:
        df = read_data_file(filepath)
        columns = df.columns.tolist()
        dataset_record = None
        if session.get('current_dataset_id'):
            dataset_record = get_dataset_record(session['user'], session['current_dataset_id'])
        return render_template('analysis.html', columns=columns, dataset_record=dataset_record)
    except Exception as e:
        flash(f'Error loading data file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/analysis_summary')
@login_required
def analysis_summary():
    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No data file loaded. Please upload a file first.', 400)

    try:
        processor = DataProcessor(filepath)
        dataset_record = None
        if session.get('current_dataset_id'):
            dataset_record = get_dataset_record(session['user'], session['current_dataset_id'])
        return jsonify({
            'success': True,
            'summary': processor.get_analysis_summary(),
            'dataset': dataset_record,
        })
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/executive_report')
@login_required
def executive_report():
    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No data file loaded. Please upload a file first.', 400)

    try:
        processor = DataProcessor(filepath)
        dataset_record = None
        if session.get('current_dataset_id'):
            dataset_record = get_dataset_record(session['user'], session['current_dataset_id'])
        report = build_report_payload(processor.get_analysis_summary(), dataset_record)
        return jsonify({'success': True, 'report': report})
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/export_report', methods=['POST'])
@login_required
def export_report():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No data file loaded. Please upload a file first.', 400)

    payload = request.get_json(silent=True) or {}
    export_type = payload.get('type', 'html')

    try:
        processor = DataProcessor(filepath)
        dataset_record = None
        if session.get('current_dataset_id'):
            dataset_record = get_dataset_record(session['user'], session['current_dataset_id'])
        report = build_report_payload(processor.get_analysis_summary(), dataset_record)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dataset_name = safe_filename_stem(report['dataset_name'], default='dataset_report')

        if export_type == 'markdown':
            output_file = f'{dataset_name}_report_{timestamp}.md'
            output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_file)
            if not is_path_within_directory(app.config['UPLOAD_FOLDER'], output_path):
                return error_response('Unsafe export path generated.', 400)
            with open(output_path, 'w', encoding='utf-8') as handle:
                handle.write(report['markdown'])
            return send_file(output_path, as_attachment=True, download_name=output_file, mimetype='text/markdown')

        output_file = f'{dataset_name}_report_{timestamp}.html'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_file)
        if not is_path_within_directory(app.config['UPLOAD_FOLDER'], output_path):
            return error_response('Unsafe export path generated.', 400)
        with open(output_path, 'w', encoding='utf-8') as handle:
            handle.write(report['html'])
        return send_file(output_path, as_attachment=True, download_name=output_file, mimetype='text/html')
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/apply_transform', methods=['POST'])
@login_required
def apply_dataset_transform():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No active dataset found. Load a dataset first.', 400)

    payload = request.get_json(silent=True) or {}
    operation = payload.get('operation')
    options = payload.get('options') or {}

    if not operation:
        return error_response('No transform operation was provided.', 400)

    try:
        df = read_data_file(filepath)
        transformed_df, description = apply_transform(df, operation, options)
        if transformed_df.empty:
            return error_response('This transform produced an empty dataset, so it was not applied.', 400)

        current_record = None
        if session.get('current_dataset_id'):
            current_record = get_dataset_record(session['user'], session['current_dataset_id'])

        source_name = current_record['source_name'] if current_record else os.path.basename(filepath)
        current_metadata = current_record.get('metadata', {}) if current_record else {}
        parent_lineage = current_metadata.get('lineage_steps', [])
        parent_pipeline = current_metadata.get('pipeline_steps', [])
        lineage_step = {
            'kind': 'transform',
            'operation': operation,
            'description': description,
            'options': options,
            'created_at': datetime.utcnow().isoformat() + 'Z',
        }
        derived_path, derived_filename = store_derived_dataset(transformed_df, source_name)
        dataset_record = create_dataset_record(
            session['user'],
            source_name=derived_filename,
            stored_path=derived_path,
            source_type='derived',
            row_count=len(transformed_df),
            column_count=len(transformed_df.columns),
            parent_dataset_id=current_record['id'] if current_record else None,
            metadata=dataset_metadata(
                f"{current_metadata.get('display_name', source_name) if current_record else source_name} / transformed",
                transformed_df.columns.tolist(),
                {
                    'transform_operation': operation,
                    'transform_description': description,
                    'lineage_steps': parent_lineage + [lineage_step],
                    'pipeline_steps': parent_pipeline + [lineage_step],
                },
            ),
        )

        session['current_dataset_id'] = dataset_record['id']
        session['current_filepath'] = derived_path

        processor = DataProcessor(derived_path)
        return jsonify({
            'success': True,
            'message': description,
            'dataset': dataset_record,
            'summary': processor.get_analysis_summary(),
        })
    except Exception as e:
        return error_response(str(e), 400)


@app.route('/datasets/<dataset_id>/pipeline')
@login_required
def dataset_pipeline(dataset_id):
    record = get_dataset_record(session['user'], dataset_id)
    if not record:
        return error_response('Dataset not found in your workspace.', 404)

    parent_record = None
    if record.get('parent_dataset_id'):
        parent_record = get_dataset_record(session['user'], record['parent_dataset_id'])

    return jsonify({
        'success': True,
        'dataset': {
            'id': record['id'],
            'source_type': record.get('source_type'),
            'display_name': record.get('metadata', {}).get('display_name', record['source_name']),
        },
        'pipeline': {
            'steps': dataset_pipeline_steps(record),
            'can_undo': bool(parent_record),
            'can_rebuild': supports_pipeline_rebuild(record),
            'parent_dataset': parent_record,
        },
    })


@app.route('/datasets/<dataset_id>/undo', methods=['POST'])
@login_required
def undo_dataset_version(dataset_id):
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    record = get_dataset_record(session['user'], dataset_id)
    if not record:
        return error_response('Dataset not found in your workspace.', 404)
    parent_id = record.get('parent_dataset_id')
    if not parent_id:
        return error_response('This dataset has no parent version to restore.', 400)

    parent_record = get_dataset_record(session['user'], parent_id)
    if not parent_record:
        return error_response('The parent dataset version is no longer available.', 404)
    if not os.path.exists(parent_record['stored_path']):
        return error_response('The parent dataset file is no longer available.', 404)

    session['current_dataset_id'] = parent_record['id']
    session['current_filepath'] = parent_record['stored_path']
    return jsonify({
        'success': True,
        'message': f"Restored parent dataset: {parent_record.get('metadata', {}).get('display_name', parent_record['source_name'])}",
        'dataset': parent_record,
    })


@app.route('/datasets/<dataset_id>/rebuild', methods=['POST'])
@login_required
def rebuild_dataset(dataset_id):
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    record = get_dataset_record(session['user'], dataset_id)
    if not record:
        return error_response('Dataset not found in your workspace.', 404)
    if not supports_pipeline_rebuild(record):
        return error_response('This dataset does not yet have a structured rebuild definition.', 400)

    try:
        rebuilt_df = build_dataset_from_record(session['user'], record)
        if rebuilt_df.empty:
            return error_response('The rebuilt pipeline produced an empty dataset.', 400)

        source_name = record.get('source_name') or record.get('metadata', {}).get('display_name') or 'dataset'
        rebuilt_path, rebuilt_filename = store_derived_dataset(rebuilt_df, source_name)
        record_metadata = record.get('metadata', {})
        rebuild_step = {
            'kind': 'system',
            'operation': 'rebuild_pipeline',
            'description': f"Rebuilt the dataset from its recorded pipeline definition on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC.",
            'created_at': datetime.utcnow().isoformat() + 'Z',
        }
        rebuilt_record = create_dataset_record(
            session['user'],
            source_name=rebuilt_filename,
            stored_path=rebuilt_path,
            source_type='rebuilt',
            row_count=len(rebuilt_df),
            column_count=len(rebuilt_df.columns),
            parent_dataset_id=record['id'],
            metadata=dataset_metadata(
                f"{record_metadata.get('display_name', source_name)} / rebuilt",
                rebuilt_df.columns.tolist(),
                {
                    'pipeline_steps': record_metadata.get('pipeline_steps', []),
                    'lineage_steps': record_metadata.get('lineage_steps', []) + [rebuild_step],
                    'rebuilt_from_dataset_id': record['id'],
                    'rebuilt_from_source_type': record.get('source_type'),
                    'join': record_metadata.get('join'),
                },
            ),
        )
        session['current_dataset_id'] = rebuilt_record['id']
        session['current_filepath'] = rebuilt_path

        processor = DataProcessor(rebuilt_path)
        return jsonify({
            'success': True,
            'message': 'Pipeline rebuilt into a new dataset version.',
            'dataset': rebuilt_record,
            'summary': processor.get_analysis_summary(),
        })
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/data_model')
@login_required
def data_model():
    datasets = [
        record
        for record in list_dataset_records(session['user'])
        if os.path.exists(record.get('stored_path', ''))
    ]
    model = suggest_relationships(datasets)
    relationships = list_relationship_records(session['user'])
    return jsonify({
        'success': True,
        'model': model,
        'relationships': relationships,
    })

@app.route('/relationships/save', methods=['POST'])
@login_required
def save_relationship():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    payload = request.get_json(silent=True) or {}
    required = ['left_dataset_id', 'left_column', 'right_dataset_id', 'right_column']
    if any(not payload.get(field) for field in required):
        return error_response('Choose both datasets and key columns before saving a relationship.', 400)

    left_record = get_dataset_record(session['user'], payload['left_dataset_id'])
    right_record = get_dataset_record(session['user'], payload['right_dataset_id'])
    if not left_record or not right_record:
        return error_response('One of the selected datasets was not found.', 404)

    record = create_relationship_record(
        session['user'],
        left_dataset_id=payload['left_dataset_id'],
        left_column=payload['left_column'],
        right_dataset_id=payload['right_dataset_id'],
        right_column=payload['right_column'],
        join_type=payload.get('join_type') or 'left',
        confidence=payload.get('confidence'),
    )
    return jsonify({'success': True, 'relationship': record})

@app.route('/relationships/join', methods=['POST'])
@login_required
def create_joined_dataset():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    payload = request.get_json(silent=True) or {}
    left_record = get_dataset_record(session['user'], payload.get('left_dataset_id', ''))
    right_record = get_dataset_record(session['user'], payload.get('right_dataset_id', ''))
    if not left_record or not right_record:
        return error_response('Choose two valid datasets to join.', 400)

    try:
        joined_df = join_datasets(
            left_record,
            right_record,
            left_key=payload.get('left_column'),
            right_key=payload.get('right_column'),
            join_type=payload.get('join_type') or 'left',
        )
        if joined_df.empty:
            return error_response('The join produced no rows. Try a different key or join type.', 400)

        left_name = left_record.get('metadata', {}).get('display_name') or left_record['source_name']
        right_name = right_record.get('metadata', {}).get('display_name') or right_record['source_name']
        left_metadata = left_record.get('metadata', {})
        join_step = {
            'kind': 'join',
            'operation': 'join',
            'description': f"Joined {left_name} to {right_name} on {payload.get('left_column')} = {payload.get('right_column')}.",
            'left_dataset_id': left_record['id'],
            'left_dataset_name': left_name,
            'left_column': payload.get('left_column'),
            'right_dataset_id': right_record['id'],
            'right_dataset_name': right_name,
            'right_column': payload.get('right_column'),
            'join_type': payload.get('join_type') or 'left',
            'confidence': payload.get('confidence'),
            'created_at': datetime.utcnow().isoformat() + 'Z',
        }
        derived_path, derived_filename = store_derived_dataset(joined_df, f'{left_name}_joined_{right_name}')
        dataset_record = create_dataset_record(
            session['user'],
            source_name=derived_filename,
            stored_path=derived_path,
            source_type='joined',
            row_count=len(joined_df),
            column_count=len(joined_df.columns),
            parent_dataset_id=left_record['id'],
            metadata=dataset_metadata(
                f'{left_name} joined with {right_name}',
                joined_df.columns.tolist(),
                {
                    'join': {
                        'left_dataset_id': left_record['id'],
                        'left_column': payload.get('left_column'),
                        'right_dataset_id': right_record['id'],
                        'right_column': payload.get('right_column'),
                        'join_type': payload.get('join_type') or 'left',
                    },
                    'lineage_steps': left_metadata.get('lineage_steps', []) + [join_step],
                    'pipeline_steps': left_metadata.get('pipeline_steps', []) + [join_step],
                },
            ),
        )

        create_relationship_record(
            session['user'],
            left_dataset_id=left_record['id'],
            left_column=payload.get('left_column'),
            right_dataset_id=right_record['id'],
            right_column=payload.get('right_column'),
            join_type=payload.get('join_type') or 'left',
            confidence=payload.get('confidence'),
        )

        session['current_dataset_id'] = dataset_record['id']
        session['current_filepath'] = derived_path
        processor = DataProcessor(derived_path)
        return jsonify({
            'success': True,
            'dataset': dataset_record,
            'summary': processor.get_analysis_summary(),
            'message': f'Created joined dataset with {len(joined_df)} rows and {len(joined_df.columns)} columns.',
        })
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/measures')
@login_required
def measures():
    records = list_measure_records(session['user'], dataset_id=session.get('current_dataset_id'))
    return jsonify({'success': True, 'measures': records})

@app.route('/measures/create', methods=['POST'])
@login_required
def create_measure():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No active dataset found. Load a dataset first.', 400)

    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    definition = payload.get('definition') or {}
    if not name:
        return error_response('Give the measure a business name.', 400)
    if not definition.get('type'):
        return error_response('Choose a measure formula type.', 400)

    try:
        df = read_data_file(filepath)
        definition['name'] = name
        result = evaluate_measure(df, definition)
        record = create_measure_record(
            session['user'],
            dataset_id=session.get('current_dataset_id'),
            name=name,
            definition=definition,
            latest_result=result,
        )
        return jsonify({'success': True, 'measure': record, 'result': result})
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/datasets/<dataset_id>/activate')
@login_required
def activate_dataset(dataset_id):
    dataset_record = get_dataset_record(session['user'], dataset_id)
    if not dataset_record:
        flash('Dataset not found in your workspace.', 'error')
        return redirect(url_for('index'))

    if not os.path.exists(dataset_record['stored_path']):
        flash('The underlying dataset file is no longer available.', 'error')
        return redirect(url_for('index'))

    session['current_dataset_id'] = dataset_record['id']
    session['current_filepath'] = dataset_record['stored_path']
    flash(f"Loaded dataset: {dataset_record.get('metadata', {}).get('display_name', dataset_record['source_name'])}", 'success')
    return redirect(url_for('analysis'))

@app.route('/dashboard')
@login_required
def dashboard():
    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        flash('Please upload a file or select a sample dataset first', 'error')
        return redirect(url_for('index'))
    current_dataset = None
    if session.get('current_dataset_id'):
        current_dataset = get_dataset_record(session['user'], session['current_dataset_id'])
    return render_template('dashboard.html', dataset_record=current_dataset)

@app.route('/generate_visualization', methods=['POST'])
@login_required
def generate_visualization():
    try:
        if not validate_csrf_token():
            return error_response('Invalid request token', 400)

        data = request.json
        # FIX #5: Never accept filepath from client — always use session
        filepath = session.get('current_filepath')
        columns = data.get('columns')
        viz_type = data.get('type')
        sample_percentage = data.get('sample_percentage', 100)
        filters = data.get('filters')
        
        if not filepath:
            return error_response('No data file loaded. Please upload a file first.', 400)
            
        if not columns:
            return error_response('No columns selected', 400)
            
        if not viz_type:
            return error_response('No visualization type selected', 400)
        
        if not os.path.exists(filepath):
            return error_response('Data file not found. Please upload again.', 400)
        
        viz_generator = VisualizationGenerator(filepath)
        visualization = viz_generator.generate_visualization(
            columns, 
            viz_type, 
            sample_percentage,
            filters
        )
        
        return jsonify({
            'success': True,
            'visualization': visualization
        })
        
    except Exception as e:
        import traceback
        print(f"Error in generate_visualization: {str(e)}")
        print(traceback.format_exc())
        return error_response(str(e), 400)

@app.route('/export', methods=['POST'])
@login_required
def export_visualization():
    try:
        if not validate_csrf_token():
            return error_response('Invalid request token', 400)

        data = request.json
        viz_type = data.get('type')
        viz_data = data.get('data')
        
        if not viz_type or not viz_data:
            return error_response('Missing required parameters', 400)
        
        viz_generator = VisualizationGenerator(None)
        export_file = viz_generator.export_visualization(
            viz_type,
            viz_data,
            app.config['UPLOAD_FOLDER'],
        )
        if not is_path_within_directory(app.config['UPLOAD_FOLDER'], export_file):
            return error_response('Unsafe export path generated.', 400)

        with open(export_file, 'rb') as handle:
            export_bytes = handle.read()
        try:
            os.remove(export_file)
        except OSError:
            pass

        return send_file(
            BytesIO(export_bytes),
            as_attachment=True,
            download_name=f'visualization.{viz_type}',
            mimetype='image/png',
        )
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/export_dashboard', methods=['POST'])
@login_required
def export_dashboard():
    try:
        if not validate_csrf_token():
            return error_response('Invalid request token', 400)

        data = request.json
        dashboard_data = data.get('dashboard_data')
        
        if not dashboard_data:
            return error_response('Missing dashboard data', 400)
        
        # Read the template
        with open(SAVING_DASHBOARD_TEMPLATE, 'r', encoding='utf-8') as f:
            template_content = f.read()
        
        # Replace the placeholder with actual dashboard data
        dashboard_json = escape_embedded_json(dashboard_data)
        html_content = template_content.replace('DASHBOARD_DATA_PLACEHOLDER', dashboard_json)
        
        # Create a temporary file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f'dashboard_export_{timestamp}.html'
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_file)
        
        # Save the file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return send_file(
            output_path,
            as_attachment=True,
            download_name=output_file,
            mimetype='text/html'
        )
        
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/clear_session', methods=['POST'])
@login_required
def clear_session():
    try:
        if not validate_csrf_token():
            return error_response('Invalid request token', 400)

        current_filepath = session.get('current_filepath')

        # Clear generated exports in the uploads directory that are older than 1 hour.
        # Tracked dataset files stay available so workspace links continue to work.
        current_time = time.time()
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                if not is_path_within_directory(app.config['UPLOAD_FOLDER'], filepath):
                    continue
                if is_any_tracked_dataset_path(filepath):
                    continue
                if os.path.getctime(filepath) < (current_time - 3600):  # 3600 seconds = 1 hour
                    os.remove(filepath)
            except Exception as e:
                print(f"Error removing old file {filename}: {str(e)}")

        if current_filepath and not is_any_tracked_dataset_path(current_filepath):
            cleanup_uploaded_file(current_filepath)

        session.pop('current_filepath', None)
        session.pop('current_dataset_id', None)
        
        return jsonify({'success': True, 'message': 'Session cleared successfully'})
    except Exception as e:
        return error_response(str(e), 500)

@app.route('/dashboard_library')
@login_required
def dashboard_library():
    dataset_id = request.args.get('dataset_id') or session.get('current_dataset_id')
    dashboards = list_dashboard_records(session['user'], dataset_id=dataset_id)
    return jsonify({
        'success': True,
        'dashboards': [
            {
                'id': dashboard['id'],
                'name': dashboard['name'],
                'dataset_id': dashboard.get('dataset_id'),
                'created_at': dashboard['created_at'],
                'updated_at': dashboard['updated_at'],
                'chart_count': len(dashboard.get('dashboard_viz', [])),
            }
            for dashboard in dashboards
        ]
    })

@app.route('/dashboards/save', methods=['POST'])
@login_required
def save_dashboard_record():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    dashboard_viz = payload.get('dashboard_viz') or []
    dashboard_state = payload.get('dashboard_state') or {}

    if not name:
        return error_response('Give this dashboard a name before saving.', 400)
    if not dashboard_viz:
        return error_response('Add at least one chart before saving the dashboard.', 400)

    record = create_dashboard_record(
        session['user'],
        name=name,
        dataset_id=session.get('current_dataset_id'),
        dashboard_viz=dashboard_viz,
        dashboard_state=dashboard_state,
    )
    return jsonify({'success': True, 'dashboard': record})

@app.route('/dashboards/<dashboard_id>')
@login_required
def get_dashboard(dashboard_id):
    record = get_dashboard_record(session['user'], dashboard_id)
    if not record:
        return error_response('Dashboard not found.', 404)
    return jsonify({'success': True, 'dashboard': record})

@app.route('/starter_dashboard', methods=['POST'])
@login_required
def starter_dashboard():
    if not validate_csrf_token():
        return error_response('Invalid request token', 400)

    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No active dataset found. Load a dataset first.', 400)

    processor = DataProcessor(filepath)
    summary = processor.get_analysis_summary()
    recommendations = summary.get('recommended_visualizations', [])[:4]
    if not recommendations:
        return error_response('No starter dashboard recommendations are available for this dataset yet.', 400)

    starter_cards = []
    positions = [
        {'x': 0, 'y': 0},
        {'x': 430, 'y': 0},
        {'x': 0, 'y': 340},
        {'x': 430, 'y': 340},
    ]
    for idx, chart in enumerate(recommendations):
        starter_cards.append({
            'id': int(time.time()) + idx,
            'title': chart['title'],
            'type': chart['type'],
            'columns': chart['columns'],
            'samplePercentage': chart.get('sample_percentage', 100),
            'position': positions[idx] if idx < len(positions) else {'x': 0, 'y': idx * 320},
            'size': {'width': 400, 'height': 300},
        })

    return jsonify({'success': True, 'dashboard_viz': starter_cards})

@app.route('/dashboard_filter_options')
@login_required
def dashboard_filter_options():
    filepath = session.get('current_filepath')
    if not filepath or not os.path.exists(filepath):
        return error_response('No active dataset found. Load a dataset first.', 400)

    try:
        processor = DataProcessor(filepath)
        summary = processor.get_analysis_summary()
        semantic_profiles = summary.get('semantic_profiles', [])
        dimension_columns = [
            profile['name']
            for profile in semantic_profiles
            if profile.get('semantic_role') == 'dimension'
            and (profile.get('unique_count', 0) <= 20 or profile.get('subtype') == 'geography')
        ]
        df = processor.df
        options = {}
        for column in dimension_columns[:8]:
            values = [str(value) for value in df[column].dropna().astype(str).value_counts().head(15).index.tolist()]
            options[column] = values

        date_columns = [
            profile['name']
            for profile in semantic_profiles
            if profile.get('semantic_role') == 'datetime'
        ]

        return jsonify({'success': True, 'options': options, 'date_columns': date_columns})
    except Exception as e:
        return error_response(str(e), 400)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # FIX #4: Validate CSRF token
        if not validate_csrf_token():
            flash('Invalid form submission. Please try again.', 'error')
            return redirect(url_for('register'))
        
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validate inputs
        if not username or not email or not password:
            flash('All fields are required', 'error')
            return redirect(url_for('register'))
        
        if password != confirm_password:
            flash('Passwords do not match', 'error')
            return redirect(url_for('register'))
        
        if len(password) < 6:
            flash('Password must be at least 6 characters', 'error')
            return redirect(url_for('register'))
        
        # FIX #3: Actually save the user with a hashed password
        users = _load_users()
        if username in users:
            flash('Username already exists', 'error')
            return redirect(url_for('register'))
        if _email_exists(users, email):
            flash('Email is already registered', 'error')
            return redirect(url_for('register'))
        
        users[username] = {
            'email': _normalize_email(email),
            'password_hash': generate_password_hash(password)
        }
        _save_users(users)
        
        flash('Registration successful. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # FIX #4: Validate CSRF token
        if not validate_csrf_token():
            flash('Invalid form submission. Please try again.', 'error')
            return redirect(url_for('login'))
        
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        normalized_login = _normalize_email(username)
        
        # FIX #2: Authenticate against stored users with hashed passwords
        users = _load_users()
        matched_username = username
        user = users.get(username)
        if user is None:
            matched = next(
                (
                    (stored_username, details)
                    for stored_username, details in users.items()
                    if _normalize_email(details.get('email')) == normalized_login
                ),
                None
            )
            if matched:
                matched_username, user = matched
        
        if user and check_password_hash(user['password_hash'], password):
            session['user'] = matched_username
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Proper logout route."""
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(debug=debug_mode, use_reloader=False)
