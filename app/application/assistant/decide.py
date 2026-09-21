"""S3 — decide: feature C's ONE Jev call (project / category / duplicate_of / amounts_consistent).

Every write downstream is gated on the confidences this module returns (plan hard rule:
"every write is gated on Jev confidence") — ``features/ticket.py`` is the only caller and
owns interpreting them against ``gate.py``'s thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from app.application.assistant.ports import ChoiceQuestion, DecisionPort, NoulQuestion
from app.application.assistant.state import DuplicateCandidate, WritableProject

_NONE = "none"

#: Expense category options offered to Jev — a fixed, self-contained set (KISS: this
#: only feeds `invoice_ai_imports.category`, an informational label, not a downstream
#: gate) rather than reusing the bibliotheque product category taxonomy, which is
#: shaped around materials, not site expenses in general (fuel, PPE, rentals, ...).
CATEGORY_CRITERIA: dict[str, str] = {
    "materiaux": "matériaux de construction",
    "outillage": "outillage / consommables",
    "carburant": "carburant / péage",
    "epi": "équipement de protection individuelle",
    "location": "location de matériel",
    "autre": "autre dépense de chantier",
}


@dataclass(frozen=True)
class TicketDecision:
    """S3's answer for one ticket: where it belongs, what it is, and how safe it is to write."""

    project_id: Optional[UUID]
    project_confidence: float
    category: str
    category_confidence: float
    duplicate_of: Optional[UUID]
    duplicate_confidence: float
    amounts_consistent: float


def decide_ticket(
    decisions: DecisionPort,
    state: dict[str, Any],
    projects: list[WritableProject],
    duplicates: list[DuplicateCandidate],
) -> TicketDecision:
    """The ONE ``system_one`` call for feature C's S3 (plan section 3, S3)."""
    project_criteria: dict[str, Optional[str]] = {
        str(project.id): f"{project.name} – {project.address}" if project.address else project.name
        for project in projects
    }
    duplicate_criteria: dict[str, Optional[str]] = {_NONE: "aucun doublon"}
    for candidate in duplicates:
        duplicate_criteria[str(candidate.invoice_id)] = (
            f"{candidate.project_name} – {candidate.date} – {candidate.total_ttc}€"
        )

    questions: dict[str, ChoiceQuestion | NoulQuestion] = {
        "project": ChoiceQuestion(
            instructions=(
                "Chantier auquel ce ticket/cette facture appartient, d'après l'adresse du chantier, les "
                "ouvriers présents ce jour-là et les achats récents chez ce commerçant."
            ),
            criteria=project_criteria or {_NONE: "aucun chantier disponible"},
        ),
        "category": ChoiceQuestion(
            instructions="Catégorie de dépense la plus proche pour cet achat.",
            criteria=dict(CATEGORY_CRITERIA),
        ),
        "duplicate_of": ChoiceQuestion(
            instructions=(
                "Ce ticket correspond-il à une facture déjà enregistrée pour le même commerçant et un montant "
                "proche, à quelques jours près ? Choisis-la, sinon 'none'."
            ),
            criteria=duplicate_criteria,
        ),
        "amounts_consistent": NoulQuestion(
            instructions=(
                "Les montants du ticket sont cohérents (HT + TVA = TTC, somme des lignes = total) et plausibles "
                "pour un achat de chantier."
            )
        ),
    }
    result = decisions.decide(state, questions)
    project_label, project_confidence, _ = result.choice("project")
    category_label, category_confidence, _ = result.choice("category")
    duplicate_label, duplicate_confidence, _ = result.choice("duplicate_of")
    amounts_consistent = result.noul("amounts_consistent")

    return TicketDecision(
        project_id=UUID(project_label) if project_label in project_criteria else None,
        project_confidence=project_confidence,
        category=category_label,
        category_confidence=category_confidence,
        duplicate_of=(
            UUID(duplicate_label) if duplicate_label != _NONE and duplicate_label in duplicate_criteria else None
        ),
        duplicate_confidence=duplicate_confidence,
        amounts_consistent=amounts_consistent,
    )
