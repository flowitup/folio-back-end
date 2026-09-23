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
    "equipment_found_more": {
        "vi": "…và {count} kết quả khác.",
        "fr": "… et {count} autre(s) résultat(s).",
        "en": "…and {count} more result(s).",
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
    "rate_limited": {
        "vi": "Bạn đã gửi khá nhiều yêu cầu trong giờ qua, vui lòng thử lại sau ít phút.",
        "fr": "Tu as envoyé beaucoup de demandes cette dernière heure, réessaie dans quelques minutes.",
        "en": "You've sent a lot of requests in the last hour, please try again in a few minutes.",
    },
    "error": {
        "vi": "Đã có lỗi xảy ra, vui lòng thử lại.",
        "fr": "Une erreur est survenue, réessaie s'il te plaît.",
        "en": "Something went wrong, please try again.",
    },
    "provider_unavailable": {
        "vi": "Trợ lý đang tạm thời quá tải, vui lòng thử lại sau ít phút.",
        "fr": "L'assistant est temporairement indisponible, réessaie dans quelques minutes.",
        "en": "The assistant is temporarily unavailable, please try again in a few minutes.",
    },
    "fetch_ack": {
        "vi": "Tôi đang tìm hoá đơn, sẽ báo lại sớm.",
        "fr": "Je m'en occupe, je te dis dès que j'ai la facture.",
        "en": "On it — I'll let you know as soon as I have the invoice.",
    },
    "fetch_need_merchant": {
        "vi": "Đây là hoá đơn của nhà cung cấp nào?",
        "fr": "C'est une facture de quel fournisseur ?",
        "en": "Which merchant is this invoice from?",
    },
    "fetch_need_amount": {
        "vi": "Hoá đơn này số tiền TTC là bao nhiêu?",
        "fr": "Il me manque le montant TTC de cette facture, tu peux me le donner ?",
        "en": "I'm missing the total amount (incl. tax) — could you give it to me?",
    },
    "fetch_already_running": {
        "vi": "Tôi đang tìm hoá đơn này rồi, chờ tôi một chút nhé.",
        "fr": "Je suis déjà en train de chercher cette facture, patiente un peu.",
        "en": "I'm already looking for this invoice, hang tight.",
    },
    "fetch_running": {
        "vi": "Tôi đang tìm trên trang của nhà cung cấp…",
        "fr": "Je cherche sur le site du fournisseur…",
        "en": "Looking it up on the merchant's site…",
    },
    "fetch_done": {
        "vi": "Đã lấy được hoá đơn!",
        "fr": "Facture récupérée !",
        "en": "Got the invoice!",
    },
    "fetch_not_ready": {
        "vi": "Hoá đơn chưa có trên trang nhà cung cấp, tôi sẽ thử lại sau.",
        "fr": "La facture n'est pas encore disponible côté fournisseur, je réessaierai plus tard.",
        "en": "The invoice isn't available on the merchant's site yet, I'll retry later.",
    },
    "fetch_failed": {
        "vi": "Tôi đã thử vài lần nhưng không lấy được hoá đơn này.",
        "fr": "Je n'ai pas réussi à récupérer cette facture après plusieurs tentatives.",
        "en": "I couldn't fetch this invoice after several attempts.",
    },
    "fetch_blocked": {
        "vi": "Trang web yêu cầu xác minh, bạn gửi giúp tôi ảnh chụp hoá đơn nhé.",
        "fr": "Le site demande une vérification, envoie-moi la photo du ticket à la place.",
        "en": "The site is asking for a verification step — send me a photo of the receipt instead.",
    },
    "fetch_not_found_prompt": {
        "vi": "Tôi không tìm thấy đúng hoá đơn, có phải một trong số này không?",
        "fr": "Je n'ai pas trouvé la bonne facture, est-ce l'une de celles-ci ?",
        "en": "I couldn't find the exact invoice — is it one of these?",
    },
    "fetch_none_option": {"vi": "Không phải cái nào cả", "fr": "Ce n'est aucune", "en": "None of these"},
    "fetch_none_of_these": {
        "vi": "Được, bạn gửi giúp tôi ảnh chụp hoá đơn nhé, tôi sẽ nhập từ đó.",
        "fr": "D'accord, envoie-moi la photo du ticket à la place, je l'enregistrerai à partir de là.",
        "en": "Got it — send me a photo of the receipt instead and I'll record it from that.",
    },
    "fetch_extract_failed": {
        "vi": "Tôi tải được tệp nhưng không đọc được nội dung, vui lòng kiểm tra lại thủ công.",
        "fr": "J'ai téléchargé le fichier mais je n'arrive pas à en lire le contenu, merci de vérifier manuellement.",
        "en": "I downloaded the file but could not read its content, please check it manually.",
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
    "retake_photo": {
        "vi": "Ảnh không đủ rõ để đọc, bạn chụp lại giúp tôi nhé?",
        "fr": "La photo n'est pas assez lisible, tu peux la reprendre ?",
        "en": "The photo is not clear enough to read, could you retake it?",
    },
    "photo_unreadable": {
        "vi": "Tôi không nhận diện được vật liệu trong ảnh này, bạn chụp lại rõ hơn nhé?",
        "fr": "Je n'arrive pas à identifier ce matériau sur cette photo, tu peux en reprendre une plus nette ?",
        "en": "I could not identify the material in this photo, could you retake a clearer one?",
    },
    "duplicate_refused": {
        "vi": "Có vẻ hoá đơn này đã được nhập rồi, tôi không tạo bản mới.",
        "fr": "Ce ticket ressemble à une facture déjà enregistrée, je ne crée pas de doublon.",
        "en": "This looks like an invoice already recorded, I am not creating a duplicate.",
    },
    "duplicate_check_prompt": {
        "vi": "Hoá đơn này có trùng với hoá đơn đã có không?",
        "fr": "Ce ticket est-il le même que cette facture déjà enregistrée ?",
        "en": "Is this receipt the same as this already-recorded invoice?",
    },
    "duplicate_check_confirm": {"vi": "Đúng, trùng", "fr": "Oui, c'est la même", "en": "Yes, it's the same"},
    "duplicate_check_deny": {"vi": "Không, khác", "fr": "Non, c'est différent", "en": "No, it's different"},
    "pick_project_prompt": {
        "vi": "Hoá đơn này thuộc công trình nào?",
        "fr": "Ce ticket appartient à quel chantier ?",
        "en": "Which project does this receipt belong to?",
    },
    "pick_project_none": {
        "vi": "Bạn chưa có công trình nào để ghi hoá đơn.",
        "fr": "Tu n'as aucun chantier sur lequel enregistrer ce ticket.",
        "en": "You have no project available to record this receipt on.",
    },
    "pick_company_prompt": {
        "vi": "Vật liệu này thuộc công ty nào?",
        "fr": "Ce matériau appartient à quelle entreprise ?",
        "en": "Which company does this material belong to?",
    },
    "invoice_created": {
        "vi": "Đã tạo hoá đơn {number} cho {project}.",
        "fr": "Facture {number} créée pour {project}.",
        "en": "Invoice {number} created for {project}.",
    },
    "invoice_attached": {
        "vi": "Đã đính kèm bản scan vào hoá đơn {number}.",
        "fr": "Scan ajouté à la facture {number}.",
        "en": "Scan attached to invoice {number}.",
    },
    "amounts_to_check": {
        "vi": "Lưu ý: các số tiền trên hoá đơn này cần kiểm tra lại.",
        "fr": "Attention : les montants de cette facture méritent une vérification.",
        "en": "Note: the amounts on this invoice are worth double-checking.",
    },
    "material_found": {
        "vi": "Đã thêm vào thư viện vật liệu.",
        "fr": "Ajouté à la bibliothèque de matériaux.",
        "en": "Added to the material library.",
    },
    "material_to_confirm": {
        "vi": "Tôi khá chắc đây là vật liệu này, bạn xác nhận giúp nhé.",
        "fr": "Je pense reconnaître ce matériau, confirme si c'est correct.",
        "en": "I think this is the material below, please confirm.",
    },
    "product_search_ack": {
        "vi": "Tôi đang tìm sản phẩm này ở các nhà cung cấp, sẽ báo lại sớm.",
        "fr": "Je cherche la fiche produit chez les fournisseurs, je te dis dès que j'ai quelque chose.",
        "en": "I'm looking for this product's page at the merchants, I'll let you know shortly.",
    },
    "product_search_running": {
        "vi": "Tôi đang tìm trên các trang nhà cung cấp…",
        "fr": "Je cherche sur les sites des fournisseurs…",
        "en": "Looking it up on the merchants' sites…",
    },
    "product_search_failed": {
        "vi": "Tôi không truy cập được trang nhà cung cấp, đã lưu ảnh vào thư viện để bạn hoàn thiện sau.",
        "fr": "Je n'ai pas pu accéder aux sites fournisseurs, j'ai enregistré la photo dans la bibliothèque pour "
        "que tu la complètes plus tard.",
        "en": "I could not reach the merchants' sites, I saved the photo in the library for you to complete later.",
    },
    "material_photo_only": {
        "vi": "Tôi không tìm được sản phẩm phù hợp trên mạng, đã lưu ảnh vào thư viện để bạn hoàn thiện sau.",
        "fr": "Je n'ai pas trouvé ce produit en ligne, j'ai enregistré la photo dans la bibliothèque pour que tu "
        "la complètes plus tard.",
        "en": "I could not find this product online, I saved the photo in the library for you to complete later.",
    },
    "no_permission": {
        "vi": "Bạn không có quyền thực hiện thao tác này.",
        "fr": "Tu n'as pas le droit d'effectuer cette action.",
        "en": "You do not have permission to perform this action.",
    },
    "unknown_action": {
        "vi": "Tôi không hiểu yêu cầu này.",
        "fr": "Je ne comprends pas cette action.",
        "en": "I do not understand this action.",
    },
    "scan_fallback_note": {
        "vi": "(scan bằng chế độ dự phòng)",
        "fr": "(scan généré en mode de secours)",
        "en": "(scan generated in fallback mode)",
    },
    # -----------------------------------------------------------------
    # D17 — confidential-class refusals (phase 03). Never model text.
    # -----------------------------------------------------------------
    "refuse_finance": {
        "vi": "Thông tin tài chính công ty (ngân sách, doanh thu, số tiền đã nhận) chỉ được xem trong kênh Quản trị.",
        "fr": "Les informations financières de l'entreprise (budget, revenus, montants reçus) ne sont visibles "
        "que dans le canal Administration.",
        "en": "Company financial information (budget, income, funds received) is only visible in the Admin channel.",
    },
    "refuse_salary": {
        "vi": "Thông tin lương/taux của người khác chỉ được xem trong kênh Quản trị.",
        "fr": "Le salaire ou le taux d'une autre personne n'est visible que dans le canal Administration.",
        "en": "Someone else's pay or rate is only visible in the Admin channel.",
    },
    "refuse_own_salary": {
        "vi": "Tôi không trả lời về lương ở đây, bạn xem taux/lương của mình trong tab Lương của ứng dụng nhé.",
        "fr": "Je ne réponds pas sur les salaires ici, tu peux voir ton taux et ta paie dans l'onglet Salaire "
        "de l'application.",
        "en": "I don't answer pay questions here — check your rate and pay in the app's Salary tab.",
    },
    "refuse_generic": {
        "vi": "Tôi không thể chia sẻ thông tin này ở kênh này.",
        "fr": "Je ne peux pas partager cette information dans ce canal.",
        "en": "I can't share this information in this channel.",
    },
    "admin_channel_hint": {
        "vi": "(Với vai trò quản trị, bạn có thể hỏi lại trong kênh Quản trị.)",
        "fr": "(En tant qu'administrateur, tu peux reposer la question dans le canal Administration.)",
        "en": "(As an admin, you can ask again in the Admin channel.)",
    },
    "refuse_ask_in_project_channel": {
        "vi": "Thông tin của một công trình (nhân sự, công việc) chỉ được trả lời trong kênh của chính công trình đó.",
        "fr": "Les informations d'un chantier (effectif, tâches) ne sont répondues que dans le canal de ce "
        "chantier.",
        "en": "Project details (roster, tasks) are only answered in that project's own channel.",
    },
    # -----------------------------------------------------------------
    # Phase 04 — labor handlers (day roster, bulk attendance, validation).
    # -----------------------------------------------------------------
    "roster_empty": {
        "vi": "Không có ai trên công trình này hôm đó.",
        "fr": "Personne sur ce chantier ce jour-là.",
        "en": "No one on this project that day.",
    },
    "roster_line_present": {
        "vi": "{name} — có mặt, {hours}h, {day_type}",
        "fr": "{name} — présent, {hours}h, {day_type}",
        "en": "{name} — present, {hours}h, {day_type}",
    },
    "roster_line_pending": {
        "vi": "{name} — chờ duyệt, {hours}h, {day_type}",
        "fr": "{name} — en attente de validation, {hours}h, {day_type}",
        "en": "{name} — pending validation, {hours}h, {day_type}",
    },
    "roster_line_absent": {
        "vi": "{name} — vắng mặt",
        "fr": "{name} — absent",
        "en": "{name} — absent",
    },
    "attendance_need_names": {
        "vi": "Tôi không nhận ra tên người thợ nào trong tin nhắn, bạn nói rõ tên giúp tôi nhé?",
        "fr": "Je n'ai reconnu aucun nom d'ouvrier dans le message, tu peux préciser ?",
        "en": "I did not recognise any worker's name in the message, could you clarify?",
    },
    "attendance_confirm_prompt": {
        "vi": "Ghi {names} có mặt ngày {date} ở {project}?",
        "fr": "Enregistrer {names} présent(s) le {date} sur {project} ?",
        "en": "Log {names} as present on {date} at {project}?",
    },
    "attendance_confirm_yes": {"vi": "Xác nhận", "fr": "Confirmer", "en": "Confirm"},
    "attendance_confirm_no": {"vi": "Huỷ", "fr": "Annuler", "en": "Cancel"},
    "attendance_logged": {
        "vi": "Đã ghi nhận {created} người có mặt ({skipped} đã có sẵn).",
        "fr": "{created} présence(s) enregistrée(s) ({skipped} déjà existante(s)).",
        "en": "{created} attendance(s) logged ({skipped} already existed).",
    },
    "validate_attendance_none_pending": {
        "vi": "Không có ngày công nào đang chờ duyệt.",
        "fr": "Aucune journée en attente de validation.",
        "en": "No pending day to validate.",
    },
    "validate_attendance_prompt": {
        "vi": "Duyệt ngày công nào?",
        "fr": "Valider quelle(s) journée(s) ?",
        "en": "Validate which day(s)?",
    },
    "validate_attendance_all_option": {
        "vi": "Tất cả",
        "fr": "Toutes",
        "en": "All",
    },
    "validate_attendance_done": {
        "vi": "Đã duyệt {count} ngày công.",
        "fr": "{count} journée(s) validée(s).",
        "en": "{count} day(s) validated.",
    },
    # -----------------------------------------------------------------
    # Phase 04 — tasks handlers.
    # -----------------------------------------------------------------
    "task_need_title": {
        "vi": "Tôi chưa hiểu rõ tên công việc, bạn nói lại giúp tôi nhé?",
        "fr": "Je n'ai pas bien compris le titre de la tâche, tu peux reformuler ?",
        "en": "I did not quite catch the task's title, could you rephrase?",
    },
    "task_confirm_prompt_with_due": {
        "vi": 'Tạo công việc "{title}" hạn {due} ở {project}?',
        "fr": "Créer la tâche « {title} » pour le {due} sur {project} ?",
        "en": 'Create the task "{title}" due {due} on {project}?',
    },
    "task_confirm_prompt_no_due": {
        "vi": 'Tạo công việc "{title}" ở {project}?',
        "fr": "Créer la tâche « {title} » sur {project} ?",
        "en": 'Create the task "{title}" on {project}?',
    },
    "task_confirm_yes": {"vi": "Tạo", "fr": "Créer", "en": "Create"},
    "task_confirm_no": {"vi": "Huỷ", "fr": "Annuler", "en": "Cancel"},
    "task_created": {
        "vi": 'Đã tạo công việc "{title}".',
        "fr": "Tâche « {title} » créée.",
        "en": 'Task "{title}" created.',
    },
    "tasks_none_open": {
        "vi": "Không có công việc nào mở trong tuần này.",
        "fr": "Aucune tâche ouverte cette semaine.",
        "en": "No open task this week.",
    },
    "tasks_line": {
        "vi": "{title} — hạn {due}",
        "fr": "{title} — échéance {due}",
        "en": "{title} — due {due}",
    },
    "tasks_line_no_due": {
        "vi": "{title} — chưa có hạn",
        "fr": "{title} — sans échéance",
        "en": "{title} — no due date",
    },
    # -----------------------------------------------------------------
    # Phase 04 — admin-only finance/payroll answers (templates only, no model text).
    # -----------------------------------------------------------------
    "project_income_summary": {
        "vi": "{project} — ngân sách {budget}, đã nhận {released}, đã chi {spent}, còn lại {remaining}.",
        "fr": "{project} — budget {budget}, reçu {released}, dépensé {spent}, restant {remaining}.",
        "en": "{project} — budget {budget}, received {released}, spent {spent}, remaining {remaining}.",
    },
    "salary_summary_none": {
        "vi": "Không có dữ liệu lương phù hợp.",
        "fr": "Aucune donnée de paie correspondante.",
        "en": "No matching pay data.",
    },
    "salary_summary_line": {
        "vi": "{worker} — {month}: đã trả {paid} ({count} hoá đơn)",
        "fr": "{worker} — {month} : payé {paid} ({count} facture(s))",
        "en": "{worker} — {month}: paid {paid} ({count} invoice(s))",
    },
    "unpaid_invoices_none": {
        "vi": "Không có hoá đơn khách hàng nào chưa thanh toán.",
        "fr": "Aucune facture client impayée.",
        "en": "No unpaid client invoice.",
    },
    "unpaid_invoices_line": {
        "vi": "{project} — {number} — {amount} — quá hạn {days} ngày",
        "fr": "{project} — {number} — {amount} — {days} jour(s) de retard",
        "en": "{project} — {number} — {amount} — {days} day(s) late",
    },
    "unpaid_invoices_line_not_due": {
        "vi": "{project} — {number} — {amount} — chưa đến hạn",
        "fr": "{project} — {number} — {amount} — pas encore échue",
        "en": "{project} — {number} — {amount} — not yet due",
    },
    "audit_summary_none": {
        "vi": "Tuần này chưa có ai hỏi trợ lý.",
        "fr": "Personne n'a sollicité l'assistant cette semaine.",
        "en": "No one asked the assistant this week.",
    },
    "audit_summary_line": {
        "vi": "{user} — {total} câu hỏi, {refused} bị từ chối",
        "fr": "{user} — {total} question(s), {refused} refusée(s)",
        "en": "{user} — {total} question(s), {refused} refused",
    },
    "resolve_project_prompt": {
        "vi": "Bạn muốn nói về công trình nào?",
        "fr": "De quel chantier veux-tu parler ?",
        "en": "Which project do you mean?",
    },
    "resolve_project_none": {
        "vi": "Bạn chưa có công trình nào trong công ty này.",
        "fr": "Tu n'as aucun chantier dans cette entreprise.",
        "en": "You have no project in this company.",
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
    "ask_project_income": {
        "vi": "Tài chính công trình",
        "fr": "Finances du chantier",
        "en": "Project finances",
    },
    "ask_salary": {"vi": "Lương của một người", "fr": "Salaire d'une personne", "en": "Someone's pay"},
    "ask_own_salary": {"vi": "Lương của tôi", "fr": "Mon salaire", "en": "My pay"},
    "ask_roster": {"vi": "Ai có mặt hôm nay", "fr": "Qui est présent aujourd'hui", "en": "Who's on site today"},
    "log_attendance": {"vi": "Khai công", "fr": "Déclarer une présence", "en": "Log attendance"},
    "validate_attendance": {"vi": "Duyệt công", "fr": "Valider des pointages", "en": "Validate attendance"},
    "create_task": {"vi": "Tạo công việc", "fr": "Créer une tâche", "en": "Create a task"},
    "ask_tasks": {"vi": "Công việc mở", "fr": "Tâches ouvertes", "en": "Open tasks"},
    "ask_audit": {"vi": "Ai đã hỏi gì", "fr": "Qui a demandé quoi", "en": "Who asked what"},
    "ask_unpaid_invoices": {
        "vi": "Hoá đơn khách chưa thanh toán",
        "fr": "Factures clients impayées",
        "en": "Unpaid client invoices",
    },
}
if set(INTENT_LABELS) != set(INTENTS):
    raise RuntimeError("INTENT_LABELS and INTENTS have drifted apart.")


def render(key: str, lang: str, **kwargs: object) -> str:
    """A fixed template, formatted with `kwargs` (e.g. `render("equipment_moved", "fr", name=..., project=...)`)."""
    resolved_lang = lang if lang in LANGUAGES else _DEFAULT_LANG
    template = TEMPLATES[key][resolved_lang]
    return template.format(**kwargs) if kwargs else template


def render_equipment_found(hits: list[EquipmentHit], lang: str, *, more: int = 0) -> str:
    """One line per hit via `equipment_found_line`, joined; empty list is never passed in.

    ``more`` (the count truncated off, `FindResult.total - len(hits)`) appends an
    "+N more" line instead of ever growing unbounded — `equipment.py`'s `find()` already
    caps `hits` at `MAX_CANDIDATES`, this just tells the user there was more.
    """
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
    if more > 0:
        lines.append(render("equipment_found_more", lang, count=more))
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
