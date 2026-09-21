"""Assistant bounded context: the pinned per-user AI conversation inside team chat.

Messages ride the existing chat transport (``assistant:<user_id>`` channel, see
``app.domain.entities.chat_message``); this package holds the pipeline seam
(``jobs``/``service``), the reply-posting helper (``messages``) and the ports the two
need (``ports``).
"""
