"""WSGI entry point for production servers (PythonAnywhere, gunicorn, etc.)."""
from app import app as application  # noqa: F401
