"""Phase 04 item 1 — resolve which project a company/admin-channel question is about.

A project channel needs no resolution: the channel itself IS the project. A company or
admin channel question ("bao nhiêu ở Arcueil ?") names (or implies) one of the asker's
projects in that company; when Jev's own confidence is high enough the answer proceeds
straight away, otherwise a ``choice`` is posted (addressed to the asker) and the caller
must wait for the tap — reused by every handler that needs "which project" answered
before it can do anything (day roster, attendance, tasks, the admin-only finance/payroll
answers).

Security-critical (review finding C1): the candidate list must never offer a project the
asker may not actually read. ``accessible_projects`` below mirrors ``GET /projects``'s
own visibility rule (``ListProjectsUseCase``/``list_for_user_and_companies`` — a UNION of
"every project of a company the caller administers" with "the caller's own
assignments", which is the intended, reviewed semantics for that endpoint), narrows it to
the channel's own company (a company/admin channel never offers another company's
projects), and then applies the resolver's ``project:read`` as the final authority —
exactly the permission the route decorators (``app.api.v1.projects.decorators``) check
for the same caller on ``GET /projects/<id>``. A project the union over-includes by
mistake, or that the caller cannot read for any other reason, is filtered out here before
it is ever offered as a choice or auto-picked.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope
from app.application.assistant.ports import ChoiceQuestion, DecisionPort
from app.application.authz.ports import AuthzReaderPort
from app.application.projects.ports import IProjectRepository
from app.domain.authz.resolver import has_permission
from app.domain.entities.project import Project

#: Jev confidence a company/admin-channel project mention needs to auto-resolve; below
#: this a `choice` is shown instead (mirrors gate.py's other project-assignment gates).
PROJECT_MATCH_CONFIDENCE = 0.85

#: The action a "which project?" choice posts — `AssistantService.handle_action` re-runs
#: the pending intent against the tapped project, carrying the original message text.
PICK_PROJECT_CTX_ACTION = "pick_project_ctx"


def accessible_projects(
    *,
    project_repo: IProjectRepository,
    authz_reader: AuthzReaderPort,
    user_id: UUID,
    company_id: Optional[UUID],
) -> list[Project]:
    """Every project ``user_id`` may actually read, scoped to ``company_id`` when given.

    Same visibility rule as ``GET /projects`` (owner-of-an-admin-company OR assigned OR
    platform-ops-sees-everything), narrowed to one company (never advertise a project of
    a company the asker merely administers elsewhere), and — the defense-in-depth layer
    the review asked for — filtered to the resolver's own ``project:read`` grant, so a
    future widening of the union above can never outrun what the caller may actually open
    in the app.
    """
    is_platform_admin = authz_reader.is_platform_ops(user_id)
    if is_platform_admin:
        candidates = project_repo.list_all()
    else:
        admin_company_ids = authz_reader.admin_company_ids(user_id)
        candidates = project_repo.list_for_user_and_companies(user_id, admin_company_ids)

    if company_id is not None:
        candidates = [p for p in candidates if authz_reader.project_company_id(p.id) == company_id]

    # NEW-M1: prime the per-request cache in three queries total instead of the
    # ~3-per-candidate the `has_permission` loop below would otherwise issue against
    # the authz reader — same `getattr` opt-in `GET /projects` already uses, since not
    # every `AuthzReaderPort` implementation (e.g. a test fake) offers this method.
    preload = getattr(authz_reader, "preload_for_projects", None)
    if preload is not None:
        preload(user_id, [p.id for p in candidates])

    return [
        p
        for p in candidates
        if has_permission(authz_reader, user_id, "project:read", project_id=p.id, is_platform_admin=is_platform_admin)
    ]


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
    authz_reader: AuthzReaderPort,
) -> Optional[UUID]:
    """Returns the resolved project id, or ``None`` after already posting a reply
    (either "you have no project" or a "which one?" choice addressed to the asker) —
    the caller must stop and report the "asked"/"refused" outcome in that case.
    """
    if scope.kind == "project":
        return scope.project_id

    projects = accessible_projects(
        project_repo=project_repo, authz_reader=authz_reader, user_id=user_id, company_id=scope.company_id
    )
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


__all__ = ["resolve_project", "accessible_projects", "PROJECT_MATCH_CONFIDENCE", "PICK_PROJECT_CTX_ACTION"]
