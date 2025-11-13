"""
Celery application configuration for async import/export tasks.
"""
from celery import Celery
from app.core.config import settings

# Create Celery app instance
celery_app = Celery("journiv")

# Configure Celery from settings
celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer=settings.celery_task_serializer,
    result_serializer=settings.celery_result_serializer,
    accept_content=settings.celery_accept_content,
    timezone=settings.celery_timezone,
    enable_utc=settings.celery_enable_utc,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour hard limit for tasks
    task_soft_time_limit=3300,  # 55 minutes soft limit
    worker_prefetch_multiplier=1,  # One task at a time
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
    task_acks_late=True,  # Acknowledge tasks after completion
    task_reject_on_worker_lost=True,  # Requeue tasks if worker dies
    broker_connection_retry_on_startup=True,  # Retry broker connection on startup
)

# Auto-discover tasks from app.tasks module
celery_app.autodiscover_tasks(["app.tasks"])


def get_celery_app() -> Celery:
    """Get Celery app instance."""
    return celery_app
