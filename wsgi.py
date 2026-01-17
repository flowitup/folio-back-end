"""
WSGI entrypoint for the application.

This module provides the WSGI application object for production deployment.
Usage with gunicorn: gunicorn 'wsgi:app'
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
