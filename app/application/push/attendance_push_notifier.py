"""Push notifications around attendance: workers ↔ the managers who validate them.

Delivery happens on a daemon thread so a slow provider never delays the API response;
recipients and tokens are resolved synchronously (cheap queries) before handing off.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Dict, List, Protocol
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.application.ports.push_sender import PushMessage, PushSenderPort

logger = logging.getLogger(__name__)


class PushDeviceRepositoryPort(Protocol):
    def tokens_for_users(self, user_ids: List[UUID]) -> Dict[UUID, List[str]]: ...
    def validator_user_ids(self, project_id: UUID) -> List[UUID]: ...
    def delete_token(self, token: str) -> None: ...


class ProjectNameReader(Protocol):
    def find_by_id(self, project_id: UUID): ...


# Short, action-first copy. Keys: (event, locale).
_TEXT: Dict[str, Dict[str, tuple]] = {
    "submitted": {
        "vi": ("Chấm công chờ duyệt", "{worker} chấm công ngày {date} · {project}"),
        "fr": ("Pointage à valider", "{worker} a pointé le {date} · {project}"),
        "en": ("Attendance to validate", "{worker} logged {date} · {project}"),
    },
    "change_requested": {
        "vi": ("Yêu cầu sửa chấm công", "{worker} xin sửa ngày {date} · {project}"),
        "fr": ("Demande de modification", "{worker} demande à modifier le {date} · {project}"),
        "en": ("Attendance change request", "{worker} asks to change {date} · {project}"),
    },
    "validated": {
        "vi": ("Chấm công đã duyệt", "Ngày {date} của bạn đã được duyệt · {project}"),
        "fr": ("Pointage validé", "Votre journée du {date} est validée · {project}"),
        "en": ("Attendance validated", "Your day {date} was validated · {project}"),
    },
    "rejected": {
        "vi": ("Chấm công bị từ chối", "Ngày {date} của bạn bị từ chối · {project}"),
        "fr": ("Pointage refusé", "Votre journée du {date} a été refusée · {project}"),
        "en": ("Attendance rejected", "Your day {date} was rejected · {project}"),
    },
    "change_applied": {
        "vi": ("Yêu cầu sửa đã áp dụng", "Ngày {date} đã được sửa như bạn yêu cầu · {project}"),
        "fr": ("Modification appliquée", "Votre journée du {date} a été modifiée · {project}"),
        "en": ("Change applied", "Your day {date} was changed as requested · {project}"),
    },
    "change_refused": {
        "vi": ("Yêu cầu sửa bị từ chối", "Ngày {date} giữ nguyên như đã duyệt · {project}"),
        "fr": ("Modification refusée", "Votre journée du {date} reste inchangée · {project}"),
        "en": ("Change refused", "Your day {date} stays as validated · {project}"),
    },
}


class AttendancePushNotifier:
    def __init__(
        self,
        devices: PushDeviceRepositoryPort,
        sender: PushSenderPort,
        worker_repo: IWorkerRepository,
        project_repo: ProjectNameReader,
        locale: str = "vi",
        run_async: bool = True,
    ) -> None:
        self._devices = devices
        self._sender = sender
        self._workers = worker_repo
        self._projects = project_repo
        self._locale = locale if locale in ("vi", "fr", "en") else "vi"
        self._run_async = run_async

    # -- events -----------------------------------------------------------------

    def worker_submitted(self, worker_id: UUID, day: date, entry_id: UUID, *, change: bool = False) -> None:
        """A worker logged a day (or asked to change one): tell the project's validators."""
        worker = self._workers.find_by_id(worker_id)
        if worker is None:
            return
        recipients = [u for u in self._devices.validator_user_ids(worker.project_id) if u != worker.user_id]
        self._dispatch(
            "change_requested" if change else "submitted",
            recipients,
            worker_name=worker.person_name or worker.name,
            day=day,
            project_id=worker.project_id,
            entry_id=entry_id,
        )

    def decision(self, worker_id: UUID, day: date, entry_id: UUID, event: str) -> None:
        """A manager settled a day: tell the worker (validated / rejected / change_applied / change_refused)."""
        worker = self._workers.find_by_id(worker_id)
        if worker is None or worker.user_id is None:
            return
        self._dispatch(
            event,
            [worker.user_id],
            worker_name=worker.person_name or worker.name,
            day=day,
            project_id=worker.project_id,
            entry_id=entry_id,
        )

    # -- internals ----------------------------------------------------------------

    def _dispatch(
        self, event: str, recipients: List[UUID], *, worker_name: str, day: date, project_id: UUID, entry_id: UUID
    ) -> None:
        if not recipients:
            return
        tokens = self._devices.tokens_for_users(recipients)
        if not tokens:
            return
        project = self._projects.find_by_id(project_id)
        project_name = project.name if project is not None else ""
        title, body = _TEXT[event][self._locale]
        text = body.format(worker=worker_name, date=day.strftime("%d/%m"), project=project_name)
        data = {"kind": event, "project_id": str(project_id), "entry_id": str(entry_id)}
        messages = [
            PushMessage(token=t, title=title, body=text, data=data)
            for user_tokens in tokens.values()
            for t in user_tokens
        ]
        if self._run_async:
            threading.Thread(target=self._send, args=(messages,), daemon=True).start()
        else:
            self._send(messages)

    def _send(self, messages: List[PushMessage]) -> None:
        try:
            self._sender.send(messages, on_invalid_token=self._forget_token)
        except Exception:  # never let a push failure surface
            logger.exception("push.send failed count=%s", len(messages))

    def _forget_token(self, token: str) -> None:
        try:
            self._devices.delete_token(token)
        except Exception:
            logger.exception("push.forget_token failed")
