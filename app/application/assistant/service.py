"""Assistant pipeline entry point and the synchronous action-submission use case.

``AssistantService.handle_message`` is the S0-and-dispatch loop (plan section 3/4):
router -> gate on intent confidence -> `find_equipment`/`move_equipment` (DB only, no
LLM) or a DeepSeek text reply (`question`/`chit_chat`) or a `FeatureHandlers` hook
(`identify_material`/`import_ticket`/`fetch_invoice` — phase 03/04 fill these in; the
default posts "not available yet"). Every branch always posts at least one reply;
`ProviderNotConfiguredError` -> the "not configured" template, any other exception ->
the "error" template, both logged with the trace id.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4

from app.application.assistant.equipment import EquipmentHit, EquipmentService, MoveResult
from app.application.assistant.exceptions import (
    AssistantAlreadyAnsweredError,
    AssistantError,
    AssistantMessageNotFoundError,
    AssistantNotAddressedError,
    ProviderNotConfiguredError,
)
from app.application.assistant.gate import intent_status, is_write_allowed
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import INTENTS, ChannelScope, RouterDecision
from app.application.assistant.ports import (
    AssistantDispatcherPort,
    CostLedgerPort,
    MessagePosterPort,
    ProjectCompanyReaderPort,
    RateLimiterPort,
    VisionLlmPort,
)
from app.application.assistant.router import Router
from app.application.assistant import reply
from app.application.chat.ports import ChatDirectoryPort
from app.application.companies.ports import UserCompanyAccessRepositoryPort
from app.application.invitations.ports import TransactionalSessionPort
from app.application.projects.ports import IProjectRepository
from app.domain.entities.chat_message import ChannelRef, ChatMessage, strip_assistant_mention

logger = logging.getLogger(__name__)

#: Assistant-authored messages never carry a `lang` payload themselves; language for a
#: reply to a choice tap is read off the *original* user message via `reply_to_id`.
_FALLBACK_LOCALE = "fr"


class FeatureHandlersPort(Protocol):
    """The three intents whose real implementation phase 03/04 own.

    Each hook is fully responsible for posting its own reply through ``messenger``
    (a card, a choice, a job_status — whatever the feature needs) using ``trace_id`` so
    the structured log line for that request stays attributable. ``AssistantService``
    never inspects what a hook posts.
    """

    def identify_material(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        project_hint: Optional[str] = None,
    ) -> str:
        """The user sent (or picked "material" for) a photo — feature A.

        ``project_hint`` (the router's S0 ``project_hint``, when the caption named a
        project) lets the caller resolve the company via ``project.company_id`` instead
        of always asking ``pick_company``. ``scope`` is the channel this dispatch came
        from — phase 03/04 use it for redaction/tool access; ignored for now. Returns the
        outcome for the structured log line (``replied``/``asked``/``created``/
        ``refused``/``error``).
        """
        ...

    def import_ticket(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        """The user sent (or picked "receipt" for) a photo — feature C. Returns the
        outcome for the structured log line."""
        ...

    def fetch_invoice(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        decision: RouterDecision,
        scope: ChannelScope,
    ) -> str:
        """The user asked to go fetch an invoice from a merchant site — feature B.
        Returns the outcome for the structured log line."""
        ...

    def handle_action(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        action: str,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> bool:
        """A choice tap not already handled by ``AssistantService`` itself (equipment,
        ``clarify_intent``): feature C/A own e.g. ``set_project``, ``confirm_duplicate``,
        ``not_duplicate``, ``pick_company``. Returns True when handled (a reply was
        posted), False when no feature recognised ``action`` — the caller then posts the
        "unknown_action" template.
        """
        ...


class DefaultFeatureHandlers:
    """Phase 02's stand-in: every hook posts "not available yet" and nothing else."""

    def identify_material(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
        project_hint: Optional[str] = None,
    ) -> str:
        messenger.post_text(
            user_id,
            reply.render("not_available_yet", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
        )
        return "replied"

    def import_ticket(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> str:
        messenger.post_text(
            user_id,
            reply.render("not_available_yet", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
        )
        return "replied"

    def fetch_invoice(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        decision: RouterDecision,
        scope: ChannelScope,
    ) -> str:
        messenger.post_text(
            user_id,
            reply.render("not_available_yet", lang),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
        )
        return "replied"

    def handle_action(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        action: str,
        payload: dict[str, Any],
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
        scope: ChannelScope,
    ) -> bool:
        return False


#: Which of the structured log line's ``feature`` buckets an intent belongs to.
_FEATURE_BY_INTENT: dict[str, str] = {
    "identify_material": "material",
    "import_ticket": "ticket",
    "fetch_invoice": "fetch",
    "find_equipment": "equipment",
    "move_equipment": "equipment",
    "question": "chat",
    "chit_chat": "chat",
}


def _feature_for_intent(intent: str) -> str:
    return _FEATURE_BY_INTENT.get(intent, "none")


def _equipment_option(hit: EquipmentHit, action: str, extra_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": f"{hit.name} ({hit.location_label})",
        "action": action,
        "payload": {"item_id": str(hit.item_id), **extra_payload},
    }


class AssistantService:
    """Called by the RQ jobs (``app.application.assistant.jobs``)."""

    def __init__(
        self,
        message_repo: MessagePosterPort,
        messenger: AssistantMessenger,
        router: Router,
        equipment: EquipmentService,
        company_access_repo: UserCompanyAccessRepositoryPort,
        project_repo: IProjectRepository,
        vision: VisionLlmPort,
        cost_ledger: CostLedgerPort,
        rate_limiter: RateLimiterPort,
        project_company_reader: ProjectCompanyReaderPort,
        feature_handlers: Optional[FeatureHandlersPort] = None,
    ) -> None:
        self._messages = message_repo
        self._messenger = messenger
        self._router = router
        self._equipment = equipment
        self._company_access = company_access_repo
        self._projects = project_repo
        self._vision = vision
        self._cost_ledger = cost_ledger
        self._rate_limiter = rate_limiter
        self._project_company_reader = project_company_reader
        self._features = feature_handlers or DefaultFeatureHandlers()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _company_ids(self, user_id: UUID) -> list[UUID]:
        return [access.company_id for access in self._company_access.list_for_user(user_id)]

    def _lang_for_original(self, original: Optional[ChatMessage]) -> str:
        if original is None:
            return _FALLBACK_LOCALE
        lang_hint = (original.payload or {}).get("lang") if original.payload else None
        return reply.detect_lang(original.body or "", lang_hint)

    def _resolve_scope(self, channel: ChannelRef, asker_id: UUID) -> ChannelScope:
        """Builds the ``ChannelScope`` a dispatch came from — a company/admin channel's
        id IS the company id; a project channel's company is looked up (a project with no
        company yet resolves to ``None``, same as everywhere else in the codebase)."""
        if channel.kind == "project":
            company_id = self._project_company_reader.project_company_id(channel.id)
            return ChannelScope(
                kind="project", company_id=company_id, project_id=channel.id, is_admin_channel=False, asker_id=asker_id
            )
        if channel.kind == "admin":
            return ChannelScope(
                kind="admin", company_id=channel.id, project_id=None, is_admin_channel=True, asker_id=asker_id
            )
        return ChannelScope(
            kind="company", company_id=channel.id, project_id=None, is_admin_channel=False, asker_id=asker_id
        )

    # ------------------------------------------------------------------
    # S0 -> dispatch
    # ------------------------------------------------------------------

    def handle_message(self, *, user_id: UUID, message_id: UUID) -> None:
        message = self._messages.find_by_id(message_id)
        if message is None or message.sender_id != user_id:
            # Defense in depth: the RQ dispatcher only ever enqueues a message it just
            # saw `user_id` themselves send (see `SendMessageUseCase`), but this is the
            # same "verify inside the use-case, don't trust the caller" discipline every
            # other handler in this file follows — never act on a message someone else
            # sent, in any channel.
            logger.warning("assistant handle_message: message %s was not sent by %s", message_id, user_id)
            return

        channel = message.channel
        scope = self._resolve_scope(channel, user_id)
        trace_id = uuid4().hex[:16]
        start = time.monotonic()
        lang = self._lang_for_original(message)
        message_text = strip_assistant_mention((message.body or "").strip())
        intent = "n/a"
        intent_confidence = 0.0
        provider_calls = 0
        feature = "none"
        outcome = "error"
        cost_before = self._cost_ledger.today_total()
        try:
            if self._cost_ledger.over_cap():
                outcome = "refused"
                self._messenger.post_text(
                    user_id,
                    reply.render("quota_exceeded", lang),
                    reply_to_id=message.id,
                    trace_id=trace_id,
                    channel=channel,
                )
                return

            if not self._rate_limiter.allow(user_id):
                outcome = "refused"
                self._messenger.post_text(
                    user_id,
                    reply.render("rate_limited", lang),
                    reply_to_id=message.id,
                    trace_id=trace_id,
                    channel=channel,
                )
                return

            has_photo = message.content_type == "photo"
            if has_photo and not message_text:
                outcome = "asked"
                self._post_photo_ask_kind(user_id, message.id, lang, trace_id, channel)
                return
            if not has_photo and not message_text:
                # A message that is only "@folio" (or a bare reply to the assistant with
                # nothing else typed) strips down to nothing to route on — answer the
                # free greeting template instead of asking the router to make sense of
                # an empty string.
                outcome = "replied"
                self._messenger.post_text(
                    user_id, reply.render("greeting", lang), reply_to_id=message.id, trace_id=trace_id, channel=channel
                )
                return

            company_ids = self._company_ids(user_id)
            projects = self._projects.list_for_user_and_companies(user_id, company_ids)
            project_names = [p.name for p in projects]
            history = [
                m.body for m in self._messages.list_recent_addressed(channel, limit=10) if m.id != message.id and m.body
            ]

            decision = self._router.route(message_text, has_photo, project_names, history)
            provider_calls += 1
            intent, intent_confidence = decision.intent, decision.intent_confidence
            feature = _feature_for_intent(intent)

            if intent_status(intent_confidence) != "confirmed":
                outcome = "asked"
                self._post_clarify_intent(user_id, message.id, decision, lang, trace_id, channel)
                return

            extra_calls, outcome = self._dispatch(
                user_id=user_id,
                message_id=message.id,
                message_text=message_text,
                decision=decision,
                lang=lang,
                trace_id=trace_id,
                company_ids=company_ids,
                channel=channel,
                scope=scope,
            )
            provider_calls += extra_calls
        except ProviderNotConfiguredError:
            outcome = "refused"
            self._messenger.post_text(
                user_id,
                reply.render("not_configured", lang),
                reply_to_id=message.id,
                trace_id=trace_id,
                channel=channel,
            )
        except Exception:
            outcome = "error"
            logger.exception("assistant.request trace=%s user=%s failed", trace_id, user_id)
            self._messenger.post_text(
                user_id, reply.render("error", lang), reply_to_id=message.id, trace_id=trace_id, channel=channel
            )
        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            cost_usd = max(self._cost_ledger.today_total() - cost_before, 0.0)
            logger.info(
                "assistant.request trace=%s user=%s intent=%s conf=%.2f feature=%s outcome=%s "
                "provider_calls=%d cost_usd=%.5f ms=%d",
                trace_id,
                user_id,
                intent,
                intent_confidence,
                feature,
                outcome,
                provider_calls,
                cost_usd,
                elapsed_ms,
            )

    def _dispatch(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        message_text: str,
        decision: RouterDecision,
        lang: str,
        trace_id: str,
        company_ids: list[UUID],
        channel: ChannelRef,
        scope: ChannelScope,
    ) -> tuple[int, str]:
        """Returns (extra provider calls made, outcome) — both feed the request log line."""
        intent = decision.intent
        if intent == "find_equipment":
            result = self._equipment.find(company_ids=company_ids, query=message_text)
            if not result.hits:
                text = reply.render("equipment_not_found", lang)
            else:
                text = reply.render_equipment_found(result.hits, lang, more=max(result.total - len(result.hits), 0))
            self._messenger.post_text(user_id, text, reply_to_id=message_id, trace_id=trace_id, channel=channel)
            return 0, "replied"
        if intent == "move_equipment":
            outcome = self._dispatch_move_search(
                user_id=user_id,
                message_id=message_id,
                query=message_text,
                project_hint=decision.project_hint,
                is_write_confirmed=is_write_allowed(decision.is_write),
                lang=lang,
                trace_id=trace_id,
                company_ids=company_ids,
                channel=channel,
            )
            return 0, outcome
        if intent in ("question", "chit_chat"):
            if reply.is_trivial_greeting(message_text):
                self._messenger.post_text(
                    user_id, reply.render("greeting", lang), reply_to_id=message_id, trace_id=trace_id, channel=channel
                )
                return 0, "replied"
            answer = reply.chit_chat_reply(self._vision, lang, message_text)
            self._messenger.post_text(user_id, answer, reply_to_id=message_id, trace_id=trace_id, channel=channel)
            return 1, "replied"
        if intent == "identify_material":
            outcome = self._features.identify_material(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=self._messenger,
                trace_id=trace_id,
                scope=scope,
                project_hint=decision.project_hint,
            )
            return 0, outcome
        if intent == "import_ticket":
            outcome = self._features.import_ticket(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=self._messenger,
                trace_id=trace_id,
                scope=scope,
            )
            return 0, outcome
        if intent == "fetch_invoice":
            outcome = self._features.fetch_invoice(
                user_id=user_id,
                message_id=message_id,
                lang=lang,
                messenger=self._messenger,
                trace_id=trace_id,
                decision=decision,
                scope=scope,
            )
            return 0, outcome
        self._messenger.post_text(
            user_id, reply.render("unknown_intent", lang), reply_to_id=message_id, trace_id=trace_id, channel=channel
        )
        return 0, "replied"

    # ------------------------------------------------------------------
    # Clarifying / choice replies
    # ------------------------------------------------------------------

    def _post_photo_ask_kind(
        self, user_id: UUID, message_id: UUID, lang: str, trace_id: str, channel: ChannelRef
    ) -> None:
        prompt = reply.render("photo_ask_kind_prompt", lang)
        options = [
            {
                "label": reply.render("photo_ask_kind_ticket", lang),
                "action": "import_ticket",
                "payload": {"message_id": str(message_id)},
            },
            {
                "label": reply.render("photo_ask_kind_material", lang),
                "action": "identify_material",
                "payload": {"message_id": str(message_id)},
            },
        ]
        self._messenger.post_choice(
            user_id, prompt, options, reply_to_id=message_id, trace_id=trace_id, channel=channel
        )

    def _post_clarify_intent(
        self, user_id: UUID, message_id: UUID, decision: RouterDecision, lang: str, trace_id: str, channel: ChannelRef
    ) -> None:
        ranked = sorted(decision.intent_probabilities.items(), key=lambda item: item[1], reverse=True)
        top_two = ranked[:2] or [(decision.intent, decision.intent_confidence)]
        options = [
            {
                "label": reply.intent_label(name, lang),
                "action": "clarify_intent",
                "payload": {"message_id": str(message_id), "intent": name},
            }
            for name, _confidence in top_two
        ]
        self._messenger.post_choice(
            user_id,
            reply.render("clarify_intent_prompt", lang),
            options,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=channel,
        )

    # ------------------------------------------------------------------
    # move_equipment choice flow
    # ------------------------------------------------------------------

    def _dispatch_move_search(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
        query: str,
        project_hint: Optional[str],
        is_write_confirmed: bool,
        lang: str,
        trace_id: str,
        company_ids: list[UUID],
        channel: ChannelRef,
    ) -> str:
        outcome = self._equipment.move(
            user_id=user_id,
            company_ids=company_ids,
            query=query,
            project_hint=project_hint,
            is_write_confirmed=is_write_confirmed,
        )
        return self._reply_move_outcome(user_id, message_id, outcome, project_hint, lang, trace_id, channel)

    def _reply_move_outcome(
        self,
        user_id: UUID,
        message_id: UUID,
        outcome: MoveResult,
        project_hint: Optional[str],
        lang: str,
        trace_id: str,
        channel: ChannelRef,
    ) -> str:
        if outcome.status == "not_found":
            self._messenger.post_text(
                user_id,
                reply.render("equipment_not_found", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "replied"
        elif outcome.status == "ambiguous_item":
            options = [
                _equipment_option(hit, "move_equipment_pick", {"project_hint": project_hint})
                for hit in outcome.candidates
            ]
            self._messenger.post_choice(
                user_id,
                reply.render("equipment_move_pick_prompt", lang),
                options,
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "asked"
        elif outcome.status == "ambiguous_project":
            item = outcome.item
            if item is None:  # pragma: no cover - equipment.py always sets item for this status
                raise AssistantError("ambiguous_project outcome missing its item.")
            options = [
                {
                    "label": name,
                    "action": "move_equipment_set_project",
                    "payload": {"item_id": str(item.item_id), "project_name": name},
                }
                for name in outcome.project_candidates
            ]
            self._messenger.post_choice(
                user_id,
                reply.render("equipment_move_pick_project_prompt", lang),
                options,
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "asked"
        elif outcome.status == "confirm":
            item = outcome.item
            if item is None:  # pragma: no cover - equipment.py always sets item for this status
                raise AssistantError("confirm outcome missing its item.")
            prompt = reply.render("equipment_move_confirm_prompt", lang, name=item.name, project=outcome.project_name)
            options = [
                {
                    "label": reply.render("equipment_move_confirm_yes", lang),
                    "action": "move_equipment_confirm",
                    "payload": {"item_id": str(item.item_id), "project_id": str(outcome.project_id)},
                },
                {
                    "label": reply.render("equipment_move_confirm_no", lang),
                    "action": "move_equipment_cancel",
                    "payload": {},
                },
            ]
            self._messenger.post_choice(
                user_id, prompt, options, reply_to_id=message_id, trace_id=trace_id, channel=channel
            )
            return "asked"
        elif outcome.status == "moved":
            item = outcome.item
            if item is None:  # pragma: no cover - equipment.py always sets item for this status
                raise AssistantError("moved outcome missing its item.")
            self._messenger.post_text(
                user_id,
                reply.render("equipment_moved", lang, name=item.name, project=outcome.project_name),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "replied"
        elif outcome.status == "denied":
            self._messenger.post_text(
                user_id,
                reply.render("equipment_move_denied", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return "refused"
        else:  # pragma: no cover - defensive, every status above is exhaustive
            raise AssistantError(f"Unhandled move outcome status: {outcome.status}")

    # ------------------------------------------------------------------
    # Action taps (POST /api/v1/assistant/actions)
    # ------------------------------------------------------------------

    def handle_action(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        choice_message = self._messages.find_by_id(message_id)
        if (
            choice_message is None
            or choice_message.content_type != "choice"
            or str((choice_message.payload or {}).get("addressed_to")) != str(user_id)
        ):
            # Defense in depth — see the identical check in handle_message(). By the
            # time this runs, `SubmitAssistantActionUseCase` has already verified the
            # caller is a member of the choice's channel AND is its addressee, but this
            # handler must never trust that on its own.
            logger.warning("assistant handle_action: message %s is not a choice addressed to %s", message_id, user_id)
            return
        channel = choice_message.channel
        scope = self._resolve_scope(channel, user_id)
        original = (
            self._messages.find_by_id(choice_message.reply_to_id) if choice_message.reply_to_id is not None else None
        )
        lang = self._lang_for_original(original)
        trace_id = uuid4().hex[:16]

        if self._cost_ledger.over_cap():
            self._messenger.post_text(
                user_id,
                reply.render("quota_exceeded", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return
        if not self._rate_limiter.allow(user_id):
            self._messenger.post_text(
                user_id,
                reply.render("rate_limited", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
            return

        try:
            if action == "clarify_intent":
                self._handle_clarify_intent(user_id, payload, lang, trace_id, channel, scope)
            elif action == "import_ticket":
                self._features.import_ticket(
                    user_id=user_id,
                    message_id=_uuid_from_payload(payload, "message_id", default=message_id),
                    lang=lang,
                    messenger=self._messenger,
                    trace_id=trace_id,
                    scope=scope,
                )
            elif action == "identify_material":
                self._features.identify_material(
                    user_id=user_id,
                    message_id=_uuid_from_payload(payload, "message_id", default=message_id),
                    lang=lang,
                    messenger=self._messenger,
                    trace_id=trace_id,
                    scope=scope,
                )
            elif action == "move_equipment_pick":
                outcome = self._equipment.move_item(
                    user_id=user_id,
                    item_id=_uuid_from_payload(payload, "item_id"),
                    project_hint=payload.get("project_hint"),
                    is_write_confirmed=True,  # an explicit tap on a named tool is itself the write confirmation
                )
                self._reply_move_outcome(
                    user_id, message_id, outcome, payload.get("project_hint"), lang, trace_id, channel
                )
            elif action == "move_equipment_set_project":
                outcome = self._equipment.move_item(
                    user_id=user_id,
                    item_id=_uuid_from_payload(payload, "item_id"),
                    project_hint=payload.get("project_name"),
                    is_write_confirmed=True,  # an explicit tap on a named project is itself the write confirmation
                )
                self._reply_move_outcome(
                    user_id, message_id, outcome, payload.get("project_name"), lang, trace_id, channel
                )
            elif action == "move_equipment_confirm":
                outcome = self._equipment.move_by_item_id(
                    user_id=user_id,
                    item_id=_uuid_from_payload(payload, "item_id"),
                    project_id=_uuid_from_payload(payload, "project_id"),
                    is_write_confirmed=True,
                )
                self._reply_move_outcome(user_id, message_id, outcome, None, lang, trace_id, channel)
            elif action == "move_equipment_cancel":
                # The tap already disabled the choice (SubmitAssistantActionUseCase marks
                # `payload.answered`); nothing more to say.
                pass
            elif not self._features.handle_action(
                user_id=user_id,
                message_id=message_id,
                action=action,
                payload=payload,
                lang=lang,
                messenger=self._messenger,
                trace_id=trace_id,
                scope=scope,
            ):
                logger.info("assistant handle_action: unrecognised action=%s message=%s", action, message_id)
                self._messenger.post_text(
                    user_id,
                    reply.render("unknown_action", lang),
                    reply_to_id=message_id,
                    trace_id=trace_id,
                    channel=channel,
                )
        except ProviderNotConfiguredError:
            self._messenger.post_text(
                user_id,
                reply.render("not_configured", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=channel,
            )
        except Exception:
            logger.exception("assistant.action trace=%s user=%s action=%s failed", trace_id, user_id, action)
            self._messenger.post_text(
                user_id, reply.render("error", lang), reply_to_id=message_id, trace_id=trace_id, channel=channel
            )

    def _handle_clarify_intent(
        self,
        user_id: UUID,
        payload: dict[str, Any],
        lang: str,
        trace_id: str,
        channel: ChannelRef,
        scope: ChannelScope,
    ) -> None:
        intent = payload.get("intent")
        if intent not in INTENTS:
            return
        original_message_id = _uuid_from_payload(payload, "message_id")
        message = self._messages.find_by_id(original_message_id)
        # Defense in depth (see handle_message's identical check) — never launder
        # another channel's message body through DeepSeek into this caller's conversation.
        if message is None or message.sender_id != user_id:
            return
        company_ids = self._company_ids(user_id)
        # An explicitly clarified intent carries no merchant/project/write signal of its
        # own — every downstream gate (is_write in particular) stays conservative.
        decision = RouterDecision(intent=intent, intent_confidence=1.0, intent_probabilities={intent: 1.0})
        self._dispatch(
            user_id=user_id,
            message_id=message.id,
            message_text=strip_assistant_mention((message.body or "").strip()),
            decision=decision,
            lang=lang,
            trace_id=trace_id,
            company_ids=company_ids,
            channel=channel,
            scope=scope,
        )


def _uuid_from_payload(payload: dict[str, Any], key: str, default: Optional[UUID] = None) -> UUID:
    value = payload.get(key)
    if value is None:
        if default is not None:
            return default
        raise AssistantError(f"Missing '{key}' in action payload.")
    return UUID(str(value))


class SubmitAssistantActionUseCase:
    """``POST /api/v1/assistant/actions``: the caller answers a choice message.

    Security-critical: ``action``/``payload`` arrive from the client, and every id a
    handler ever reads (invoice ids, S3 storage keys, chat message ids, company ids,
    Jev confidences) previously came straight from that untrusted JSON — a client could
    submit ANY payload shape for ANY action on a choice message it merely owns (obtained
    for free by sending a bare photo), which is a full cross-tenant IDOR (delete/recreate
    any invoice, read/delete any S3 object by key, OCR any chat photo, ...; see the
    review report this fixes). The fix: only ever accept an ``(action, payload)`` pair
    that is byte-for-byte one of the options the server itself wrote into
    ``message.payload["options"]`` when it posted the choice — everything downstream
    (``AssistantService.handle_action``) then only ever sees a payload this server
    authored, never one the client invented. The app always resubmits an option
    unmodified, so this is fully backward compatible.

    A choice now lives in a shared channel (company/project/admin), so being a channel
    member is not enough to answer it: only the person it was addressed to
    (``payload["addressed_to"]``, set by ``AssistantMessenger.post_choice``) may tap it —
    anyone else, member or not, gets ``AssistantNotAddressedError`` (403 NotAddressed).
    """

    def __init__(
        self,
        message_repo: MessagePosterPort,
        directory: ChatDirectoryPort,
        db_session: TransactionalSessionPort,
        dispatcher: AssistantDispatcherPort,
    ) -> None:
        self._messages = message_repo
        self._directory = directory
        self._db = db_session
        self._dispatcher = dispatcher

    def execute(self, *, actor_id: UUID, action: str, payload: dict[str, Any], reply_to_id: UUID) -> None:
        message = self._messages.find_by_id(reply_to_id)
        if message is None or message.content_type != "choice":
            raise AssistantMessageNotFoundError(f"Message {reply_to_id} is not a choice message.")
        current_payload = dict(message.payload or {})
        addressed_to = current_payload.get("addressed_to")
        if not self._directory.is_member(actor_id, message.channel) or str(actor_id) != str(addressed_to):
            raise AssistantNotAddressedError(f"Message {reply_to_id} was not addressed to {actor_id}.")
        # A stale-read "already answered" check would be redundant with (and no safer
        # than) the atomic transition below, so the only authority for that decision is
        # answer_choice_if_unanswered's return value.
        stored_payload = _find_matching_option_payload(current_payload.get("options"), action, payload)
        if stored_payload is None:
            raise AssistantMessageNotFoundError(
                f"'{action}' with this payload was never offered on message {reply_to_id}."
            )

        # Atomic transition (NEW-H1): two concurrent submissions of the same choice race
        # to flip "answered" via a single conditional UPDATE evaluated against the row's
        # current state, not a value either request read earlier — only one can win, and
        # it commits inside this call. See answer_choice_if_unanswered's docstring.
        if not self._messages.answer_choice_if_unanswered(reply_to_id, action, stored_payload):
            raise AssistantAlreadyAnsweredError(f"Message {reply_to_id} was already answered.")
        # After that commit: a dispatch failure must never be able to roll back the answer.
        # Dispatch the STORED payload, never the client's — see the class docstring.
        self._dispatcher.action_received(
            user_id=actor_id, message_id=reply_to_id, action=action, payload=stored_payload
        )


def _find_matching_option_payload(options: Any, action: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The stored option (from ``message.payload["options"]``) whose ``action`` matches
    and whose ``payload`` is exactly equal to the submitted one — or None when the
    client submitted something the server never offered."""
    if not isinstance(options, list):
        return None
    for option in options:
        if not isinstance(option, dict):
            continue
        option_payload = option.get("payload")
        if option.get("action") == action and isinstance(option_payload, dict) and option_payload == payload:
            return option_payload
    return None
