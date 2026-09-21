"""S5 — reply: language detection + vi/fr/en templates + the DeepSeek chit-chat fallback.

Every assistant-authored message in the pipeline goes through either `render()` (a fixed
template) or `chit_chat_reply()` (DeepSeek text, fixed system prompt per language — plan
hard rule #4, byte-identical prompts). `detect_lang()` picks the language: the app's
`lang` payload field wins when present (see `SendMessageUseCase`'s `lang` — phase 01),
otherwise a simple heuristic (Vietnamese-only diacritics, then French stopwords, else
English).
"""

from __future__ import annotations

import re

from app.application.assistant.equipment import EquipmentHit
from app.application.assistant.models import INTENTS
from app.application.assistant.ports import VisionLlmPort

LANGUAGES = ("vi", "fr", "en")
_DEFAULT_LANG = "fr"

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

# Vietnamese letters/diacritics with no equivalent in French orthography — a hit here is
# an unambiguous vi signal, unlike a bare "é"/"à" which both languages use.
_VI_UNIQUE_CHARS = set(
    "đơưăâêôĐƠƯĂÂÊÔ" "ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ" "ẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ"
)

_FR_STOPWORDS = frozenset(
    {
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "un",
        "une",
        "et",
        "est",
        "je",
        "tu",
        "il",
        "elle",
        "nous",
        "vous",
        "ils",
        "elles",
        "où",
        "ou",
        "bonjour",
        "salut",
        "merci",
        "chantier",
        "facture",
        "matériel",
        "matériau",
        "outil",
        "cherche",
        "chercher",
        "photo",
        "déplace",
        "s'il",
        "plaît",
        "pour",
        "avec",
        "sur",
        "dans",
    }
)

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _has_vi_chars(text: str) -> bool:
    return any(ch in _VI_UNIQUE_CHARS for ch in text)


def _looks_french(text: str) -> bool:
    words = {w.lower() for w in _WORD_RE.findall(text)}
    return bool(words & _FR_STOPWORDS)


def detect_lang(text: str, hint: str | None = None) -> str:
    """The app's `lang` payload field wins; otherwise vi diacritics > fr stopwords > en."""
    if hint in LANGUAGES:
        return hint
    if _has_vi_chars(text):
        return "vi"
    if _looks_french(text):
        return "fr"
    return "en"


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, dict[str, str]] = {
    "greeting": {
        "vi": "Xin chào! Gửi cho tôi ảnh hoá đơn hoặc vật liệu, hoặc hỏi tôi một dụng cụ đang ở đâu.",
        "fr": "Bonjour ! Envoie-moi la photo d'un ticket ou d'un matériau, ou demande-moi où se trouve un outil.",
        "en": "Hi! Send me a photo of a receipt or a material, or ask me where a tool is.",
    },
    "photo_ask_kind_prompt": {
        "vi": "Đây là hoá đơn hay vật liệu?",
        "fr": "Ticket de caisse ou matériau ?",
        "en": "Receipt or material?",
    },
    "photo_ask_kind_ticket": {"vi": "Hoá đơn", "fr": "Ticket de caisse", "en": "Receipt"},
    "photo_ask_kind_material": {"vi": "Vật liệu", "fr": "Matériau", "en": "Material"},
    "equipment_not_found": {
        "vi": "Tôi không tìm thấy dụng cụ này trong kho.",
        "fr": "Je ne trouve pas cet outil dans l'inventaire.",
        "en": "I could not find that tool in the inventory.",
    },
    "equipment_found_line": {
        "vi": "{name} — {quantity} cái, tình trạng {condition}, tại {location}",
        "fr": "{name} — {quantity} pce(s), état {condition}, chez {location}",
        "en": "{name} — {quantity} pc(s), {condition} condition, at {location}",
    },
    "equipment_moved": {
        "vi": "Đã chuyển {name} đến {project}.",
        "fr": "{name} déplacé vers {project}.",
        "en": "{name} moved to {project}.",
    },
    "equipment_move_confirm_prompt": {
        "vi": "Chuyển {name} đến {project}?",
        "fr": "Déplacer {name} vers {project} ?",
        "en": "Move {name} to {project}?",
    },
    "equipment_move_confirm_yes": {"vi": "Xác nhận", "fr": "Confirmer", "en": "Confirm"},
    "equipment_move_confirm_no": {"vi": "Huỷ", "fr": "Annuler", "en": "Cancel"},
    "equipment_move_pick_prompt": {
        "vi": "Bạn muốn di chuyển dụng cụ nào?",
        "fr": "Quel outil déplacer ?",
        "en": "Which tool do you want to move?",
    },
    "equipment_move_pick_project_prompt": {
        "vi": "Chuyển đến công trình nào?",
        "fr": "Vers quel chantier ?",
        "en": "To which project?",
    },
    "equipment_move_denied": {
        "vi": "Bạn không có quyền di chuyển dụng cụ trong công ty này.",
        "fr": "Tu n'as pas le droit de déplacer du matériel dans cette entreprise.",
        "en": "You do not have permission to move equipment in this company.",
    },
    "not_configured": {
        "vi": "Trợ lý chưa được cấu hình đầy đủ trên máy chủ này.",
        "fr": "L'assistant n'est pas encore entièrement configuré sur ce serveur.",
        "en": "The assistant is not fully configured on this server yet.",
    },
    "quota_exceeded": {
        "vi": "Đã đạt giới hạn sử dụng hôm nay, thử lại vào ngày mai nhé.",
        "fr": "Le quota du jour est atteint, réessaie demain.",
        "en": "Today's usage quota is reached, please try again tomorrow.",
    },
    "error": {
        "vi": "Đã có lỗi xảy ra, vui lòng thử lại.",
        "fr": "Une erreur est survenue, réessaie s'il te plaît.",
        "en": "Something went wrong, please try again.",
    },
    "fetch_ack": {
        "vi": "Tôi đang tìm hoá đơn, sẽ báo lại sớm.",
        "fr": "Je m'en occupe, je te dis dès que j'ai la facture.",
        "en": "On it — I'll let you know as soon as I have the invoice.",
    },
    "unknown_intent": {
        "vi": "Tôi chưa hiểu rõ yêu cầu, bạn có thể nói rõ hơn không?",
        "fr": "Je n'ai pas bien compris, tu peux préciser ?",
        "en": "I did not quite understand, could you rephrase?",
    },
    "not_available_yet": {
        "vi": "Tính năng này sắp có, hiện chưa dùng được.",
        "fr": "Cette fonctionnalité arrive bientôt, pas encore disponible.",
        "en": "This feature is coming soon, not available yet.",
    },
    "clarify_intent_prompt": {
        "vi": "Bạn muốn làm gì?",
        "fr": "Tu veux faire quoi ?",
        "en": "What would you like to do?",
    },
}

#: Human-readable label per intent, per language — used by the "clarify the top-2
#: intents" choice when S0's intent confidence is below `gate.INTENT_AUTO`.
INTENT_LABELS: dict[str, dict[str, str]] = {
    "identify_material": {"vi": "Nhận diện vật liệu", "fr": "Identifier un matériau", "en": "Identify a material"},
    "import_ticket": {"vi": "Nhập hoá đơn", "fr": "Enregistrer un ticket", "en": "Import a receipt"},
    "fetch_invoice": {"vi": "Tìm hoá đơn nhà cung cấp", "fr": "Aller chercher une facture", "en": "Fetch an invoice"},
    "find_equipment": {"vi": "Tìm dụng cụ", "fr": "Trouver un outil", "en": "Find a tool"},
    "move_equipment": {"vi": "Di chuyển dụng cụ", "fr": "Déplacer un outil", "en": "Move a tool"},
    "question": {"vi": "Câu hỏi khác", "fr": "Une question", "en": "A question"},
    "chit_chat": {"vi": "Trò chuyện", "fr": "Discuter", "en": "Just chat"},
}
assert set(INTENT_LABELS) == set(INTENTS)


def render(key: str, lang: str, **kwargs: object) -> str:
    """A fixed template, formatted with `kwargs` (e.g. `render("equipment_moved", "fr", name=..., project=...)`)."""
    resolved_lang = lang if lang in LANGUAGES else _DEFAULT_LANG
    template = TEMPLATES[key][resolved_lang]
    return template.format(**kwargs) if kwargs else template


def render_equipment_found(hits: list[EquipmentHit], lang: str) -> str:
    """One line per hit via `equipment_found_line`, joined; empty list is never passed in."""
    lines = [
        render(
            "equipment_found_line",
            lang,
            name=hit.name,
            quantity=hit.quantity,
            condition=hit.condition,
            location=hit.location_label,
        )
        for hit in hits
    ]
    return "\n".join(lines)


def intent_label(intent: str, lang: str) -> str:
    labels = INTENT_LABELS.get(intent)
    if labels is None:
        return intent
    return labels.get(lang if lang in LANGUAGES else _DEFAULT_LANG, intent)


# ---------------------------------------------------------------------------
# DeepSeek chit-chat / question fallback — byte-identical system prompt per language.
# ---------------------------------------------------------------------------

_CHAT_SYSTEM_FR = (
    "Tu es l'assistant Folio, un assistant de chantier BTP. Réponds brièvement et poliment en français, "
    "ton amical et professionnel. Tu ne fabriques jamais de chiffres ou de faits sur un chantier précis : "
    "si tu ne sais pas, dis-le simplement."
)
_CHAT_SYSTEM_VI = (
    "Bạn là trợ lý Folio cho công trường xây dựng. Trả lời ngắn gọn, lịch sự bằng tiếng Việt, giọng thân "
    "thiện và chuyên nghiệp. Không bịa số liệu hay thông tin cụ thể về công trường: nếu không biết, hãy nói "
    "thẳng là không biết."
)
_CHAT_SYSTEM_EN = (
    "You are the Folio assistant for a construction site chat. Reply briefly and politely in English, "
    "friendly and professional tone. Never invent numbers or facts about a specific project: say so plainly "
    "when you do not know."
)
_CHAT_SYSTEM_BY_LANG = {"fr": _CHAT_SYSTEM_FR, "vi": _CHAT_SYSTEM_VI, "en": _CHAT_SYSTEM_EN}

#: Trivial greetings are answered from `TEMPLATES["greeting"]` — no DeepSeek call, keeps
#: the cheapest, most common chit_chat message at zero marginal cost.
_GREETING_WORDS = frozenset({"bonjour", "salut", "hello", "hi", "hey", "yo", "coucou", "xin chào", "chào", "alo"})


def is_trivial_greeting(text: str) -> bool:
    normalized = text.strip().lower().rstrip("!.? ")
    return normalized in _GREETING_WORDS


def chit_chat_reply(vision: VisionLlmPort, lang: str, user_text: str) -> str:
    system = _CHAT_SYSTEM_BY_LANG.get(lang, _CHAT_SYSTEM_FR)
    return vision.chat_text(system=system, user_text=user_text)
