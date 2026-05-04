import os

import app as app_module


def test_app_storage_paths_are_rooted_to_repo():
    assert os.path.isabs(app_module.app.config['UPLOAD_FOLDER'])
    assert os.path.isabs(app_module.app.config['SAMPLE_DATASETS'])
    assert os.path.isabs(app_module.SAVING_DASHBOARD_TEMPLATE)

    assert app_module.app.config['UPLOAD_FOLDER'].startswith(app_module.APP_ROOT)
    assert app_module.app.config['SAMPLE_DATASETS'].startswith(app_module.APP_ROOT)
    assert app_module.SAVING_DASHBOARD_TEMPLATE.startswith(app_module.APP_ROOT)
