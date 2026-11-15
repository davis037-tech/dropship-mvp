"""WSGI entry point:  gunicorn backend.wsgi:app"""
from backend.app import create_app

app = create_app()
