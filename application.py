"""
Oryx / Azure App Service entry point.
Exposes the FastAPI app as "application:app" for default gunicorn detection.
"""
from aldar_middleware import app

