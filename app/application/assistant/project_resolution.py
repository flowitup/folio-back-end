"""Phase 04 item 1 — resolve which project a company/admin-channel question is about.

A project channel needs no resolution: the channel itself IS the project. A company or
admin channel question ("bao nhiêu ở Arcueil ?") names (or implies) one of the asker's
projects in that company; when Jev's own confidence is high enough the answer proceeds
straight away, otherwise a ``choice`` is posted (addressed to the asker) and the caller
must wait for the tap — reused by every handler that needs "which project" answered
before it can do anything (day roster, attendance, tasks, the admin-only finance/payroll
answers).
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope
from app.application.assistant.ports import ChoiceQuestion, DecisionPort
from app.application.projects.ports import IProjectRepository

#: Jev confidence a company/admin-channel project mention needs to auto-resolve; below
#: this a `choice` is shown instead (mirrors gate.py's other project-assignment gates).
PROJECT_MATCH_CONFIDENCE = 0.85

#: The action a "which project?" choice posts — `AssistantService.handle_action` re-runs
#: the pending intent against the tapped project, carrying the original message text.
PICK_PROJECT_CTX_ACTION = "pick_project_ctx"


def resolve_project(
    *,
    scope: ChannelScope,
    text: str,
    user_id: UUID,
    message_id: UUID,
    lang: str,
    trace_id: str,
    pending_intent: str,
    project_repo: IProjectRepository,
    decisions: DecisionPort,
    messenger: AssistantMessenger,
) -> Optional[UUID]:
    """Returns the resolved project id, or ``None`` after already posting a reply
    (either "you have no project" or a "which one?" choice addressed to the asker) —
    the caller must stop and report the "asked"/"refused" outcome in that case.
    """
    if scope.kind == "project":
        return scope.project_id

    company_ids = [scope.company_id] if scope.company_id is not None else []
    projects = [p for p in project_repo.list_for_user_and_companies(user_id, company_ids)]
    if not projects:
        messenger.post_text(
            user_id,
            reply.render("resolve_project_none", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return None
    if len(projects) == 1:
        return projects[0].id

    criteria: dict[str, Optional[str]] = {
        str(project.id): f"{project.name} – {project.address or ''}".strip(" –") for project in projects
    }
    state = {"message": text, "projects": [{"id": str(p.id), "name": p.name} for p in projects]}
    result = decisions.decide(
        state,
        {"project": ChoiceQuestion(instructions="Le chantier auquel le message fait référence.", criteria=criteria)},
    )
    label, confidence, _probabilities = result.choice("project")
    if confidence >= PROJECT_MATCH_CONFIDENCE and label in criteria:
        return UUID(label)

    options = [
        {
            "label": project.name,
            "action": PICK_PROJECT_CTX_ACTION,
            "payload": {"project_id": str(project.id), "intent": pending_intent, "text": text},
        }
        for project in projects
    ]
    messenger.post_choice(
        user_id,
        reply.render("resolve_project_prompt", lang),
        options,
        reply_to_id=message_id,
        trace_id=trace_id,
        channel=scope.channel,
        addressed_to=user_id,
        scope=scope,
    )
    return None


__all__ = ["resolve_project", "PROJECT_MATCH_CONFIDENCE", "PICK_PROJECT_CTX_ACTION"]
