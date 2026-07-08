"""
Celery application instance.

Broker:          Redis DB 1
Result backend:  Redis DB 2

Task routing:
  review_code  → queue: reviews   (heavy, LLM-bound)
  solve_problem → queue: problems  (heavy, LLM-bound)

To start the worker locally:
  cd backend
  celery -A app.workers.celery_app worker --loglevel=info -Q reviews,problems

To monitor tasks (Flower):
  celery -A app.workers.celery_app flower --port=5555
"""
from __future__ import annotations

from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_code_reviewer",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer         = "json",
    result_serializer       = "json",
    accept_content          = ["json"],
    result_expires          = 3600,        # results kept for 1 hour
    task_acks_late          = True,        # re-queue on worker crash
    task_reject_on_worker_lost = True,
    worker_prefetch_multiplier = 1,        # one task at a time per worker (LLM-bound)
    task_routes = {
        "app.workers.tasks.review_code_task":   {"queue": "reviews"},
        "app.workers.tasks.solve_problem_task": {"queue": "problems"},
    },
    task_soft_time_limit    = 120,         # 2 min soft limit → SoftTimeLimitExceeded
    task_time_limit         = 150,         # 2.5 min hard kill
)
