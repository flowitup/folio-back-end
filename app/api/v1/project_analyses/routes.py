"""Project analyses API routes — list, upload, metadata, content, edit, delete.

Authorization note:
    Two tiers, and they are not the same rule.

    * Read (list, get, content) and create: project membership is enough. Each
      use-case calls ``is_member()`` and raises ``NotProjectMemberError``,
      which the route maps to 403.
    * Mutate (PATCH, DELETE): membership AND uploader-or-project-owner-or-admin,
      enforced by ``authorize_analysis_mutation``. Membership alone would let
      any project member rewrite or delete a colleague's report. This mirrors
      ``DeleteProjectDocumentUseCase``.

    Authorization is single-layer, same convention as notes/routes.py: the
    checks live in the use-cases, not in route decorators — KISS. If a future
    use-case forgets its check there is no second net, so the pattern must be
    followed consistently.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from flask import Response, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError
from slugify import slugify

from app.api._helpers.pydantic_errors import format_validation_error
from app.api._helpers.rate_limit_keys import jwt_user_key
from app.api.openapi import openapi_doc
from app.api.v1.project_analyses import project_analyses_bp
from app.api.v1.project_analyses.schemas import AnalysisUpdateBody, ListQueryParams
from app.api.v1.projects.decorators import has_permission
from app.application.project_analyses.dtos import (
    AnalysisOutput,
    CreateAnalysisInput,
    ListAnalysesInput,
    UpdateAnalysisInput,
)
from app.application.project_analyses.exceptions import (
    AnalysisNotFoundError,
    AnalysisTooLargeError,
    InvalidAnalysisFileError,
    NotProjectMemberError,
    PermissionDeniedError,
)
from app.domain.entities.project_analysis import _UNSET, _Unset
from app.infrastructure.rate_limiter import limiter
from wiring import get_container

logger = logging.getLogger(__name__)


def _serialize(dto: AnalysisOutput) -> dict[str, Any]:
    """Serialize an AnalysisOutput to the API response shape.

    ``content_url`` is a **relative** path (no scheme/host) — clients must
    prefix it with the BE base URL, same convention as project_documents'
    ``download_url``.
    """
    return {
        "id": str(dto.id),
        "project_id": str(dto.project_id),
        "uploader_id": str(dto.uploader_user_id),
        "title": dto.title,
        "summary": dto.summary,
        "source_url": dto.source_url,
        "size_bytes": dto.size_bytes,
        "tags": list(dto.tags),
        "created_at": dto.created_at.isoformat(),
        "updated_at": dto.updated_at.isoformat(),
        "content_url": f"/api/v1/projects/{dto.project_id}/analyses/{dto.id}/content",
    }


def _err(code: int, error: str, message: str) -> tuple[Response, int]:
    return jsonify({"error": error, "message": message}), code


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/analyses
# ---------------------------------------------------------------------------


@project_analyses_bp.get("/projects/<uuid:project_id>/analyses")
@openapi_doc(summary="List analysis reports for a project", query=ListQueryParams, tags=["project_analyses"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_analyses(project_id: UUID) -> Any:
    """List analysis reports for a project. Search + tag filter + pagination."""
    tag_values = request.args.getlist("tag")
    raw = {k: v for k, v in request.args.items() if k != "tag"}
    if tag_values:
        raw["tag"] = tag_values  # type: ignore[assignment]

    try:
        params = ListQueryParams.model_validate(raw)
    except ValidationError as exc:
        return format_validation_error(exc)

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_project_analyses_usecase is None:
        raise RuntimeError("list_project_analyses_usecase not wired in container")

    try:
        page = container.list_project_analyses_usecase.execute(
            actor_id=actor_id,
            filters=ListAnalysesInput(
                project_id=project_id,
                q=params.q,
                tags=tuple(params.tag),
                sort=params.sort,
                order=params.order,
                page=params.page,
                per_page=params.per_page,
            ),
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("list_analyses unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return (
        jsonify(
            {
                "items": [_serialize(a) for a in page.items],
                "total": page.total,
                "page": page.page,
                "per_page": page.per_page,
            }
        ),
        200,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/analyses/tags
# ---------------------------------------------------------------------------


@project_analyses_bp.get("/projects/<uuid:project_id>/analyses/tags")
@openapi_doc(summary="List the distinct tags used by a project's analyses", tags=["project_analyses"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def list_analysis_tags(project_id: UUID) -> Any:
    """Return the project's whole analysis tag vocabulary, for the filter UI.

    Declared before the ``/<uuid:analysis_id>`` route for readability; the
    ``uuid`` converter would not match the literal "tags" segment anyway, so
    the two cannot collide.
    """
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.list_project_analysis_tags_usecase is None:
        raise RuntimeError("list_project_analysis_tags_usecase not wired in container")

    try:
        tags = container.list_project_analysis_tags_usecase.execute(actor_id=actor_id, project_id=project_id)
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("list_analysis_tags unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify({"tags": tags}), 200


# ---------------------------------------------------------------------------
# POST /api/v1/projects/<uuid:project_id>/analyses
# ---------------------------------------------------------------------------


@project_analyses_bp.post("/projects/<uuid:project_id>/analyses")
@openapi_doc(
    summary="Upload an HTML analysis report to a project (multipart/form-data)",
    tags=["project_analyses"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("20 per minute", key_func=jwt_user_key)
def create_analysis(project_id: UUID) -> Any:
    """Upload a self-contained HTML report. Agent-callable via the same endpoint."""
    if "file" not in request.files:
        return _err(400, "MissingFile", "No file part in request (expected field 'file')")

    file = request.files["file"]
    filename = file.filename if file else None
    if not file or not filename:
        return _err(400, "MissingFile", "Empty filename")

    # Determine size — Flask wraps the multipart stream; seek to end then back.
    file.stream.seek(0, 2)
    size_bytes = file.stream.tell()
    file.stream.seek(0)

    title = (request.form.get("title") or "").strip()
    if not title:
        return _err(400, "MissingTitle", "title is required")
    summary = (request.form.get("summary") or "").strip() or None
    source_url = (request.form.get("source_url") or "").strip() or None
    tags = tuple(t for t in request.form.getlist("tags") if t.strip())

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.create_project_analysis_usecase is None:
        raise RuntimeError("create_project_analysis_usecase not wired in container")

    try:
        out = container.create_project_analysis_usecase.execute(
            CreateAnalysisInput(
                project_id=project_id,
                uploader_user_id=actor_id,
                title=title,
                summary=summary,
                source_url=source_url,
                tags=tags,
                filename=filename,
                content_type=file.mimetype or "application/octet-stream",
                size_bytes=size_bytes,
                fileobj=file.stream,
            )
        )
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except AnalysisTooLargeError as exc:
        return _err(413, "FileTooLarge", str(exc))
    except InvalidAnalysisFileError as exc:
        return _err(400, "InvalidFile", str(exc))
    except ValueError as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("create_analysis unexpected error project_id=%s", project_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_serialize(out)), 201


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/analyses/<uuid:analysis_id>
# ---------------------------------------------------------------------------


@project_analyses_bp.get("/projects/<uuid:project_id>/analyses/<uuid:analysis_id>")
@openapi_doc(summary="Get an analysis report's metadata", tags=["project_analyses"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_analysis(project_id: UUID, analysis_id: UUID) -> Any:
    """Return an analysis report's metadata."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.get_project_analysis_usecase is None:
        raise RuntimeError("get_project_analysis_usecase not wired in container")

    try:
        out = container.get_project_analysis_usecase.execute(
            actor_id=actor_id, analysis_id=analysis_id, expected_project_id=project_id
        )
    except AnalysisNotFoundError:
        return _err(404, "NotFound", "Analysis not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("get_analysis unexpected error analysis_id=%s", analysis_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_serialize(out)), 200


# ---------------------------------------------------------------------------
# GET /api/v1/projects/<uuid:project_id>/analyses/<uuid:analysis_id>/content
# ---------------------------------------------------------------------------


@project_analyses_bp.get("/projects/<uuid:project_id>/analyses/<uuid:analysis_id>/content")
@openapi_doc(summary="Get the raw HTML body of an analysis report", tags=["project_analyses"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("60 per minute", key_func=jwt_user_key)
def get_analysis_content(project_id: UUID, analysis_id: UUID) -> Any:
    """Serve the stored HTML report body inline, with a locked-down response.

    SECURITY (read before touching this route): the CSP below carries
    ``style-src 'unsafe-inline'`` and ``script-src 'unsafe-inline'`` because
    LearnFlow-style reports inline their own CSS and a scroll-reveal
    ``<script>``. Inline script execution would normally be a stored-XSS
    vector on direct navigation to this URL — it is acceptable **only**
    because the frontend never navigates the top-level document here. It
    fetches this response with an authenticated request and renders the body
    inside ``<iframe sandbox="allow-scripts">`` **without**
    ``allow-same-origin`` (see the FE analysis-viewer component). That
    combination gives the report document an opaque origin: inline scripts
    run, but they cannot read Folio's cookies, localStorage, or parent DOM.
    The CSP here is defense-in-depth for the (should-never-happen) case of a
    direct navigation; the FE sandbox attribute is the actual security
    control. Do not relax either half without re-reviewing the other — they
    are one security decision split across two phases (BE phase 04 / FE
    phase 06).
    """
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.get_project_analysis_content_usecase is None:
        raise RuntimeError("get_project_analysis_content_usecase not wired in container")

    try:
        meta, body, _length = container.get_project_analysis_content_usecase.execute(
            actor_id=actor_id, analysis_id=analysis_id, expected_project_id=project_id
        )
    except AnalysisNotFoundError:
        return _err(404, "NotFound", "Analysis not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except Exception:
        logger.exception("get_analysis_content unexpected error analysis_id=%s", analysis_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    filename_slug = slugify(meta.title, max_length=80) or str(meta.id)
    response = Response(body, mimetype="text/html")
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Content-Disposition"] = f'inline; filename="{filename_slug}.html"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data: https:; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'unsafe-inline'; frame-ancestors 'self'"
    )
    return response


# ---------------------------------------------------------------------------
# PATCH /api/v1/projects/<uuid:project_id>/analyses/<uuid:analysis_id>
# ---------------------------------------------------------------------------


@project_analyses_bp.patch("/projects/<uuid:project_id>/analyses/<uuid:analysis_id>")
@openapi_doc(
    summary="Edit an analysis report's title/summary/source_url/tags",
    request=AnalysisUpdateBody,
    tags=["project_analyses"],
)
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def update_analysis(project_id: UUID, analysis_id: UUID) -> Any:
    """Update title/summary/source_url/tags. Omitted fields are left unchanged."""
    raw = request.get_json(silent=True) or {}
    try:
        body = AnalysisUpdateBody.model_validate(raw)
    except ValidationError as exc:
        return format_validation_error(exc)

    # Only forward fields the client actually sent — see UpdateAnalysisInput
    # docstring for the "PATCH summary must not null title/source_url/tags"
    # landmine this sentinel dance avoids.
    summary_arg: str | None | _Unset = body.summary if "summary" in raw else _UNSET
    source_url_arg: str | None | _Unset = body.source_url if "source_url" in raw else _UNSET
    tags_arg: tuple[str, ...] | _Unset
    if "tags" in raw:
        tags_arg = tuple(body.tags) if body.tags is not None else ()
    else:
        tags_arg = _UNSET

    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.update_project_analysis_usecase is None:
        raise RuntimeError("update_project_analysis_usecase not wired in container")

    # Editing is restricted to the uploader, the project owner, or an admin —
    # membership alone only grants read access. Re-load the project because the
    # use-case needs its owner_id to make that call.
    if container.project_repository is None:
        raise RuntimeError("project_repository not wired in container")
    project = container.project_repository.find_by_id(project_id)
    if project is None:
        return _err(404, "NotFound", "Project not found")

    try:
        out = container.update_project_analysis_usecase.execute(
            actor_id=actor_id,
            expected_project_id=project_id,
            data=UpdateAnalysisInput(
                analysis_id=analysis_id,
                title=body.title,
                summary=summary_arg,
                source_url=source_url_arg,
                tags=tags_arg,
            ),
            project_owner_id=project.owner_id,
            is_admin=has_permission("*:*"),
        )
    except AnalysisNotFoundError:
        return _err(404, "NotFound", "Analysis not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except PermissionDeniedError:
        return _err(403, "Forbidden", "You are not permitted to edit this analysis")
    except ValueError as exc:
        return _err(400, "BadRequest", str(exc))
    except Exception:
        logger.exception("update_analysis unexpected error analysis_id=%s", analysis_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return jsonify(_serialize(out)), 200


# ---------------------------------------------------------------------------
# DELETE /api/v1/projects/<uuid:project_id>/analyses/<uuid:analysis_id>
# ---------------------------------------------------------------------------


@project_analyses_bp.delete("/projects/<uuid:project_id>/analyses/<uuid:analysis_id>")
@openapi_doc(summary="Soft-delete an analysis report", tags=["project_analyses"])
@jwt_required()  # type: ignore[untyped-decorator]
@limiter.limit("30 per minute", key_func=jwt_user_key)
def delete_analysis(project_id: UUID, analysis_id: UUID) -> Any:
    """Soft-delete an analysis report. Actor must be a project member."""
    actor_id = UUID(get_jwt_identity())
    container = get_container()
    if container.delete_project_analysis_usecase is None:
        raise RuntimeError("delete_project_analysis_usecase not wired in container")

    # Same restriction as PATCH: uploader, project owner, or admin only.
    if container.project_repository is None:
        raise RuntimeError("project_repository not wired in container")
    project = container.project_repository.find_by_id(project_id)
    if project is None:
        return _err(404, "NotFound", "Project not found")

    try:
        container.delete_project_analysis_usecase.execute(
            actor_id=actor_id,
            analysis_id=analysis_id,
            expected_project_id=project_id,
            project_owner_id=project.owner_id,
            is_admin=has_permission("*:*"),
        )
    except AnalysisNotFoundError:
        return _err(404, "NotFound", "Analysis not found")
    except NotProjectMemberError:
        return _err(403, "Forbidden", "Not a project member")
    except PermissionDeniedError:
        return _err(403, "Forbidden", "You are not permitted to delete this analysis")
    except Exception:
        logger.exception("delete_analysis unexpected error analysis_id=%s", analysis_id)
        return _err(500, "InternalError", "An unexpected error occurred.")

    return "", 204
