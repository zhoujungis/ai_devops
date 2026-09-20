"""Configuration package.

Importing the Celery app here guarantees ``@shared_task`` binds to it as soon as
Django starts, which is what Celery requires.
"""

from config.celery import app as celery_app

__all__ = ("celery_app",)
