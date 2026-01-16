"""
Background tasks for Journiv.
"""

# Ensure Celery registers task modules on worker startup.
from app.tasks import import_tasks  # noqa: F401
from app.tasks import export_tasks  # noqa: F401
from app.tasks import version_check  # noqa: F401
from app.tasks import license_refresh  # noqa: F401
from app.tasks import immich_import_tasks  # noqa: F401
from app.tasks import media_processing_tasks  # noqa: F401
