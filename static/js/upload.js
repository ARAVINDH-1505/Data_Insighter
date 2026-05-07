document.addEventListener('DOMContentLoaded', function() {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const loadingIndicator = document.getElementById('loadingIndicator');
    const dataPreview = document.getElementById('dataPreview');
    const columnSearch = document.getElementById('columnSearch');
    const selectAllBtn = document.getElementById('selectAll');
    const deselectAllBtn = document.getElementById('deselectAll');
    const proceedButton = document.getElementById('proceedButton');
    const csrfToken = window.getCsrfToken ? window.getCsrfToken() : '';

    // Handle drag and drop events
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, unhighlight, false);
    });

    function highlight(e) {
        dropZone.classList.add('dragover');
    }

    function unhighlight(e) {
        dropZone.classList.remove('dragover');
    }

    // Handle file drop
    dropZone.addEventListener('drop', handleDrop, false);
    fileInput.addEventListener('change', handleFiles, false);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles({ target: { files: files } });
    }

    function handleFiles(e) {
        const files = e.target.files;
        if (files.length > 0) {
            const file = files[0];
            if (!file.name.match(/\.(csv|tsv|json|jsonl|ndjson|xlsx|xls|parquet|db|sqlite|sqlite3)$/i)) {
                showError('Please upload a CSV, TSV, JSON, Excel, Parquet, or SQLite file');
                return;
            }
            if (file.size > 16 * 1024 * 1024) {
                showError('File size must be less than 16MB');
                return;
            }
            uploadFile(file);
        }
    }

    function uploadFile(file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('_csrf_token', csrfToken);

        loadingIndicator.style.display = 'block';
        dataPreview.style.display = 'none';

        fetch('/upload', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            loadingIndicator.style.display = 'none';
            if (data.success) {
                showSuccess('File uploaded successfully');
                displayDataPreview(data.data);
            } else {
                showError(data.error || 'Error uploading file');
            }
        })
        .catch(error => {
            loadingIndicator.style.display = 'none';
            showError('Error uploading file: ' + error.message);
        });
    }

    function displayDataPreview(data) {
        dataPreview.style.display = 'block';
        
        // Display summary info
        const summaryInfo = document.getElementById('summaryInfo');
        summaryInfo.innerHTML = `
            <div class="summary-item">
                <span class="summary-label">File Name:</span>
                <span class="summary-value">${data.filename}</span>
            </div>
            <div class="summary-item">
                <span class="summary-label">Total Rows:</span>
                <span class="summary-value">${data.total_rows}</span>
            </div>
            <div class="summary-item">
                <span class="summary-label">Total Columns:</span>
                <span class="summary-value">${data.total_columns}</span>
            </div>
            ${data.source_table ? `
            <div class="summary-item">
                <span class="summary-label">Source Table:</span>
                <span class="summary-value">${data.source_table}</span>
            </div>` : ''}
        `;

        // Display preview table
        const previewTable = document.getElementById('previewTable');
        if (data.preview_data && data.preview_data.length > 0) {
            const table = document.createElement('table');
            table.className = 'preview-table';
            
            // Create header
            const thead = document.createElement('thead');
            const headerRow = document.createElement('tr');
            Object.keys(data.preview_data[0]).forEach(key => {
                const th = document.createElement('th');
                th.textContent = key;
                headerRow.appendChild(th);
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            // Create body
            const tbody = document.createElement('tbody');
            data.preview_data.forEach(row => {
                const tr = document.createElement('tr');
                Object.values(row).forEach(value => {
                    const td = document.createElement('td');
                    td.textContent = value;
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            previewTable.innerHTML = '';
            previewTable.appendChild(table);
        }

        // Display column list
        const columnList = document.getElementById('columnList');
        columnList.innerHTML = '';
        data.columns.forEach(column => {
            const div = document.createElement('div');
            div.className = 'column-item';
            div.innerHTML = `
                <input type="checkbox" id="col_${column.name}" value="${column.name}" checked>
                <label for="col_${column.name}">
                    ${column.name} (${column.type})
                    <span class="column-stats">
                        Non-null: ${column.non_null_count} | 
                        Null: ${column.null_count} | 
                        Unique: ${column.unique_count}
                    </span>
                </label>
            `;
            columnList.appendChild(div);
        });

        setupColumnSelection();
        updateProceedButton();
    }

    function setupColumnSelection() {
        // Individual checkbox change events
        document.querySelectorAll('.column-item input[type="checkbox"]').forEach(checkbox => {
            checkbox.addEventListener('change', updateProceedButton);
        });
    }

    function renderWorkspaceCatalog(datasets) {
        const container = document.getElementById('workspaceCatalog');
        if (!container) {
            return;
        }
        if (!datasets.length) {
            container.innerHTML = `
                <div class="sample-dataset-card">
                    <h3>No saved datasets yet</h3>
                    <p>Upload or activate a sample dataset to start building a reusable workspace catalog.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = datasets.map((dataset) => `
            <div class="sample-dataset-card">
                <h3>${dataset.display_name}</h3>
                <span class="catalog-stat">${dataset.freshness?.status || 'current'}</span>
                <p>${dataset.access_role || 'owner'} access / owner ${dataset.owner || 'workspace'}</p>
                <p>${dataset.row_count} rows / ${dataset.column_count} columns / ${dataset.pipeline_step_count} pipeline steps</p>
                <p>${dataset.shared_count || 0} shared collaborator(s) / ${dataset.row_policy_count || 0} row policy rule(s)</p>
                <p>${dataset.freshness?.reason || 'No freshness issues detected.'}</p>
                <button class="btn btn-primary open-workspace-dataset" data-id="${dataset.id}">
                    Open Dataset
                </button>
            </div>
        `).join('');

        document.querySelectorAll('.open-workspace-dataset').forEach((button) => {
            button.addEventListener('click', () => {
                window.location.href = `/datasets/${button.dataset.id}/activate`;
            });
        });
    }

    async function loadWorkspaceCatalog() {
        const container = document.getElementById('workspaceCatalog');
        if (!container) {
            return;
        }
        try {
            const response = await fetch('/workspace_catalog');
            const data = await response.json();
            if (!data.success) {
                throw new Error(data.error || 'Could not load the workspace catalog');
            }
            renderWorkspaceCatalog(data.datasets || []);
        } catch (error) {
            container.innerHTML = `
                <div class="sample-dataset-card">
                    <h3>Workspace catalog unavailable</h3>
                    <p>${error.message}</p>
                </div>
            `;
        }
    }

    function updateProceedButton() {
        const selectedColumns = document.querySelectorAll('.column-item input[type="checkbox"]:checked');
        proceedButton.disabled = selectedColumns.length === 0;
    }

    // Add proceed button click handler
    proceedButton.addEventListener('click', () => {
        const selectedColumns = Array.from(document.querySelectorAll('.column-item input[type="checkbox"]:checked'))
            .map(checkbox => checkbox.value);
        if (selectedColumns.length > 0) {
            // FIX #5: No filepath in URL — navigate to analysis, server uses session
            window.location.href = '/analysis';
        }
    });

    // Handle sample dataset clicks
    document.querySelectorAll('.sample-link').forEach(link => {
        link.addEventListener('click', async (e) => {
            e.preventDefault();
            const filename = e.currentTarget.dataset.filename;
            try {
                const response = await fetch(`/sample/${encodeURIComponent(filename)}`);
                const data = await response.json();
                if (data.success) {
                    showSuccess('Sample dataset loaded successfully');
                    // FIX #5: No filepath in URL
                    window.location.href = '/analysis';
                } else {
                    showError('Error loading sample dataset');
                }
            } catch (error) {
                console.error('Error:', error);
                showError('Error loading sample dataset');
            }
        });
    });

    columnSearch.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();
        document.querySelectorAll('.column-item').forEach(item => {
            const text = item.textContent.toLowerCase();
            item.style.display = text.includes(searchTerm) ? 'block' : 'none';
        });
    });

    selectAllBtn.addEventListener('click', () => {
        document.querySelectorAll('.column-item input[type="checkbox"]').forEach(checkbox => {
            checkbox.checked = true;
        });
        updateProceedButton();
    });

    deselectAllBtn.addEventListener('click', () => {
        document.querySelectorAll('.column-item input[type="checkbox"]').forEach(checkbox => {
            checkbox.checked = false;
        });
        updateProceedButton();
    });

    loadWorkspaceCatalog();
}); 
