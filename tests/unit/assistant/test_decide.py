"""Unit tests for `app.application.assistant.decide` (S3 — feature C's one Jev call)."""

from __future__ import annotations

from uuid import uuid4

from app.application.assistant.decide import decide_ticket
from app.application.assistant.ports import Decision
from app.application.assistant.state import DuplicateCandidate, WritableProject
from tests.fakes.ai import ScriptedDecision


class TestDecideTicket:
    def test_maps_choice_and_noul_answers(self) -> None:
        project = WritableProject(id=uuid4(), name="Villa Arcueil", address="12 rue X")
        duplicate = DuplicateCandidate(
            invoice_id=uuid4(), project_id=project.id, project_name=project.name, date="2026-09-08", total_ttc=79.5
        )
        fixed = Decision(
            choices={
                "project": (str(project.id), 0.95, {str(project.id): 0.95}),
                "category": ("materiaux", 0.8, {"materiaux": 0.8}),
                "duplicate_of": (str(duplicate.invoice_id), 0.7, {str(duplicate.invoice_id): 0.7}),
            },
            nouls={"amounts_consistent": 0.9},
        )
        decisions = ScriptedDecision(fixed=fixed)

        result = decide_ticket(decisions, {"invoice": {}}, [project], [duplicate])

        assert result.project_id == project.id
        assert result.project_confidence == 0.95
        assert result.category == "materiaux"
        assert result.duplicate_of == duplicate.invoice_id
        assert result.duplicate_confidence == 0.7
        assert result.amounts_consistent == 0.9

    def test_none_labels_map_to_none(self) -> None:
        project = WritableProject(id=uuid4(), name="Villa Arcueil", address=None)
        fixed = Decision(
            choices={
                "project": ("none", 0.2, {}),
                "category": ("autre", 0.5, {}),
                "duplicate_of": ("none", 0.99, {}),
            },
            nouls={"amounts_consistent": 0.6},
        )
        decisions = ScriptedDecision(fixed=fixed)

        result = decide_ticket(decisions, {}, [project], [])

        assert result.project_id is None
        assert result.duplicate_of is None

    def test_project_without_address_still_resolves(self) -> None:
        project = WritableProject(id=uuid4(), name="Villa Arcueil", address=None)
        fixed = Decision(
            choices={
                "project": (str(project.id), 0.95, {}),
                "category": ("materiaux", 0.9, {}),
                "duplicate_of": ("none", 0.99, {}),
            },
            nouls={"amounts_consistent": 0.9},
        )
        decisions = ScriptedDecision(fixed=fixed)

        result = decide_ticket(decisions, {}, [project], [])

        assert result.project_id == project.id

    def test_no_projects_still_asks_a_valid_question(self) -> None:
        fixed = Decision(
            choices={"project": ("none", 0.5, {}), "category": ("autre", 0.5, {}), "duplicate_of": ("none", 0.99, {})},
            nouls={"amounts_consistent": 0.5},
        )
        decisions = ScriptedDecision(fixed=fixed)
        result = decide_ticket(decisions, {}, [], [])
        assert result.project_id is None
