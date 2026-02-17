"""Aldar Middleware package."""

from aldar_middleware.application import get_app

# Create the app instance for WSGI servers like gunicorn
app = get_app()