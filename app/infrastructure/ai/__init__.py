"""Provider adapters for the Folio Assistant AI pipeline (`app.application.assistant`).

Each module implements one port from `app.application.assistant.ports` against a real
SDK, plus a `NullX` fallback used when the corresponding API key is not configured —
see `app/__init__.py`'s assistant wiring for which key selects which adapter.
"""
