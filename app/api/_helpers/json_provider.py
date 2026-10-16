"""JSON provider that never emits the non-standard tokens Infinity / NaN.

Python's json module writes float('inf') as ``Infinity`` by default, which is
not JSON: clients (the mobile app's JSON.parse) then fail on the whole
response, so one out-of-range amount blanked every screen that lists it.
Here a non-finite float is written as ``null`` instead and logged, so the
rest of the payload still loads.

Dates are written as ISO 8601, not Flask's default RFC 1123 text
(``Thu, 22 Oct 2026 22:09:11 GMT``): a ``datetime`` with its UTC offset (a
naive one is UTC, as Flask already assumed), a ``date`` as ``YYYY-MM-DD``.
Routes that jsonify ``model_dump()`` used to answer RFC 1123 while the others
answered ISO, so clients needed several parsers.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, timezone
from typing import Any

from flask.json.provider import DefaultJSONProvider

log = logging.getLogger(__name__)


def _finite(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _iso_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return (obj if obj.tzinfo is not None else obj.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return DefaultJSONProvider.default(obj)


class FiniteJSONProvider(DefaultJSONProvider):
    default = staticmethod(_iso_default)  # type: ignore[assignment]

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)
        try:
            return json.dumps(obj, allow_nan=False, **kwargs)
        except ValueError:
            # Rare path: only a payload holding inf/NaN gets the extra walk.
            log.warning("Non-finite number in a JSON response; written as null")
            return json.dumps(_finite(obj), allow_nan=False, **kwargs)
