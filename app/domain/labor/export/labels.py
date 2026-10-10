"""Localised labels for the labour export — en, fr and vi.

English stays the default so a request without a locale renders exactly as
before. Every locale carries the same keys (a unit test checks the parity).
"""

from __future__ import annotations

from datetime import date

SUPPORTED_LOCALES = ("en", "fr", "vi")
DEFAULT_LOCALE = "en"

_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Folio · Labor Export",
        "project": "Project: {name}",
        "month": "Month: {month}",
        "range": "Range: {start} → {end}",
        "range_months_one": "Range: {start} → {end} ({n} month)",
        "range_months_other": "Range: {start} → {end} ({n} months)",
        "generated": "Generated: {at} by {email}",
        "worker_rate": "Worker: {name}    Rate: {rate}/day",
        "no_entries_range": "No labor entries in range {start} → {end}",
        "no_entries_month": "No entries this month",
        "summary_sheet": "Summary",
        "daily_detail": "Daily detail",
        "day_log": "Day log — {month}",
        "total_row": "TOTAL",
        "page": "Page {n}",
        "worker": "Worker",
        "days": "Days",
        "banked_hrs": "Banked hrs",
        "bonus_full": "Bonus full",
        "bonus_half": "Bonus half",
        "priced_cost": "Priced cost",
        "bonus_cost": "Bonus cost",
        "total_priced_bonus": "Total (priced + bonus)",
        "total_cost": "Total cost",
        "worker_days": "Worker-days",
        "bonus_days": "Bonus days",
        "banked_hours": "Banked hours",
        "date": "Date",
        "shift": "Shift",
        "supplement_hrs": "Extra hrs (unpaid)",
        "override": "Override",
        "effective_cost": "Effective cost",
        "note": "Note",
        "activity": "Activity",
        "description": "Description",
        "shift_full": "Full day",
        "shift_half": "Half day",
        "shift_overtime": "Overtime",
    },
    "fr": {
        "title": "Folio · Export main-d'œuvre",
        "project": "Projet : {name}",
        "month": "Mois : {month}",
        "range": "Période : {start} → {end}",
        "range_months_one": "Période : {start} → {end} ({n} mois)",
        "range_months_other": "Période : {start} → {end} ({n} mois)",
        "generated": "Généré le {at} par {email}",
        "worker_rate": "Ouvrier : {name}    Tarif : {rate}/jour",
        "no_entries_range": "Aucune saisie de main-d'œuvre sur {start} → {end}",
        "no_entries_month": "Aucune saisie ce mois-ci",
        "summary_sheet": "Synthèse",
        "daily_detail": "Détail par jour",
        "day_log": "Journal — {month}",
        "total_row": "TOTAL",
        "page": "Page {n}",
        "worker": "Ouvrier",
        "days": "Jours",
        "banked_hrs": "Heures en banque",
        "bonus_full": "Primes pleines",
        "bonus_half": "Primes ½",
        "priced_cost": "Coût au tarif",
        "bonus_cost": "Coût des primes",
        "total_priced_bonus": "Total (tarif + primes)",
        "total_cost": "Coût total",
        "worker_days": "Jours travaillés",
        "bonus_days": "Jours de prime",
        "banked_hours": "Heures en banque",
        "date": "Date",
        "shift": "Poste",
        "supplement_hrs": "Heures en plus (non payées)",
        "override": "Montant forcé",
        "effective_cost": "Coût retenu",
        "note": "Note",
        "activity": "Activité",
        "description": "Description",
        "shift_full": "Journée complète",
        "shift_half": "Demi-journée",
        "shift_overtime": "Heures sup. (x1,5)",
    },
    "vi": {
        "title": "Folio · Xuất nhân công",
        "project": "Dự án: {name}",
        "month": "Tháng: {month}",
        "range": "Khoảng thời gian: {start} → {end}",
        "range_months_one": "Khoảng thời gian: {start} → {end} ({n} tháng)",
        "range_months_other": "Khoảng thời gian: {start} → {end} ({n} tháng)",
        "generated": "Tạo lúc {at} bởi {email}",
        "worker_rate": "Nhân công: {name}    Đơn giá: {rate}/ngày",
        "no_entries_range": "Không có công nhật trong khoảng {start} → {end}",
        "no_entries_month": "Không có công nhật trong tháng",
        "summary_sheet": "Tổng hợp",
        "daily_detail": "Chi tiết theo ngày",
        "day_log": "Nhật ký — {month}",
        "total_row": "TỔNG",
        "page": "Trang {n}",
        "worker": "Nhân công",
        "days": "Ngày công",
        "banked_hrs": "Giờ tích lũy",
        "bonus_full": "Thưởng cả ngày",
        "bonus_half": "Thưởng nửa ngày",
        "priced_cost": "Chi phí theo đơn giá",
        "bonus_cost": "Chi phí thưởng",
        "total_priced_bonus": "Tổng (đơn giá + thưởng)",
        "total_cost": "Tổng chi phí",
        "worker_days": "Ngày công",
        "bonus_days": "Ngày thưởng",
        "banked_hours": "Giờ tích lũy",
        "date": "Ngày",
        "shift": "Ca",
        "supplement_hrs": "Giờ thêm (không tính lương)",
        "override": "Số tiền ghi đè",
        "effective_cost": "Chi phí thực tế",
        "note": "Ghi chú",
        "activity": "Hoạt động",
        "description": "Mô tả",
        "shift_full": "Cả ngày",
        "shift_half": "Nửa ngày",
        "shift_overtime": "Tăng ca",
    },
}

_MONTHS: dict[str, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
}


def normalize_locale(locale: str | None) -> str:
    """Return a supported locale, falling back to English."""
    return locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE


def t(locale: str, key: str, **kwargs: object) -> str:
    """Label `key` in `locale` (English fallback), formatted with `kwargs`."""
    text = _LABELS[normalize_locale(locale)].get(key) or _LABELS[DEFAULT_LOCALE][key]
    return text.format(**kwargs) if kwargs else text


def shift_label(locale: str, shift_type: str | None) -> str:
    """A day's shift ("full", "half", "overtime") in `locale`; blank for a supplement-only day."""
    if not shift_type:
        return ""
    key = f"shift_{shift_type}"
    return t(locale, key) if key in _LABELS[DEFAULT_LOCALE] else shift_type


def month_label(month: date, locale: str = DEFAULT_LOCALE) -> str:
    """Short month + year ("Jan 2026", "janv. 2026", "Tháng 1 năm 2026"); also safe as a sheet title."""
    locale = normalize_locale(locale)
    if locale == "vi":
        return f"Tháng {month.month} năm {month.year}"
    return f"{_MONTHS[locale][month.month - 1]} {month.year}"


def range_label(start: date, end: date, locale: str = DEFAULT_LOCALE) -> str:
    """ "Range: Jan 2026 → Mar 2026 (3 months)" in `locale`."""
    n = (end.year - start.year) * 12 + (end.month - start.month) + 1
    key = "range_months_one" if n == 1 else "range_months_other"
    return t(locale, key, start=month_label(start, locale), end=month_label(end, locale), n=n)
