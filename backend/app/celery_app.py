import os
from celery import Celery

# Load Redis connection url from environment or use default localhost
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Initialize Celery app
celery_app = Celery("fieldops_tasks", broker=redis_url, backend=redis_url)

# Configuration overrides
celery_app.conf.update(
    task_always_eager=False,  # Set to True in tests dynamically if needed
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "send-dispatcher-digest-every-5-minutes": {
            "task": "app.tasks.send_dispatcher_digest",
            "schedule": 300.0,
        },
        "broadcast-sla-countdown-every-30-seconds": {
            "task": "app.tasks.broadcast_sla_countdown",
            "schedule": 30.0,
        },
    }
)

# Automatically register tasks
celery_app.autodiscover_tasks(["app"])
