"""Feature B's browser worker — the ``ai-browser`` container (plan section 4).

This package is imported by a *separate container* (``Dockerfile.browser``) whose
environment (see the parent repo's ``docker-compose.yml`` ``ai-browser`` service) has
``DATABASE_URL``, ``REDIS_URL``, ``SECRET_KEY``, ``S3_*`` and the AI/browser env vars —
but no ``JWT_SECRET_KEY`` and no ``FLASK_ENV=production``. Nothing under this package
may import or call ``app.create_app()``: it talks to Postgres through a bare
``sqlalchemy.orm.Session`` (``sessionmaker(bind=create_engine(DATABASE_URL))()``), to S3
through ``S3AttachmentStorage`` constructed directly from ``S3_*``, and to Redis/RQ
through a plain ``rq.Queue`` — the same infrastructure adapters the Flask app uses,
just wired by hand instead of through ``wiring.Container``.

``python -m app.infrastructure.browser_worker`` (``__main__.py``) is the entrypoint
``docker/browser-entrypoint.sh`` runs under ``xvfb-run -a`` in the container's default
mode.
"""
