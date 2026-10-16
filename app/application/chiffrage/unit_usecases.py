"""Unit-of-measure use-cases: list, create, delete.

Presets are a constant, custom units are rows; the list endpoint merges the two
so the front-end select has a single source of truth.
"""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.dtos import UnitResponse
from app.application.chiffrage.exceptions import (
    InvalidChiffrageInputError,
    UnitAlreadyExistsError,
    UnitInUseError,
    UnitNotFoundError,
)
from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import PRESET_BY_KEY, PRESET_UNITS, unit_key
from app.application.chiffrage.validation import MAX_UNIT_SYMBOL
from app.domain.entities.chiffrage_unit import ChiffrageUnit


class ListUnitsUseCase:
    """Return presets followed by the project's custom units.

    Authorization is the route decorators' job (see GetChiffrageTreeUseCase).
    """

    def __init__(self, repo: ChiffrageRepositoryPort) -> None:
        self._repo = repo

    def execute(self, *, project_id: UUID) -> list[UnitResponse]:
        units = [UnitResponse(id=None, symbol=symbol, is_preset=True) for symbol in PRESET_UNITS]
        units.extend(
            UnitResponse(id=str(u.id), symbol=u.symbol, is_preset=False) for u in self._repo.list_units(project_id)
        )
        return units


class CreateUnitUseCase:
    """Add a custom unit symbol to a project."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, symbol: str) -> ChiffrageUnit:
        """Reject blanks, over-long symbols, presets and existing custom units.

        Rejecting a preset duplicate matters as much as rejecting a custom one:
        the select merges both lists, so allowing it would render the same
        symbol twice. The comparison ignores case and reads 'm2' as 'm²', so
        'U', 'Sac' or 'M2' cannot shadow 'u', 'sac' or 'm²'.
        """
        cleaned = (symbol or "").strip()
        if not cleaned:
            raise InvalidChiffrageInputError("Unit symbol cannot be empty.")
        if len(cleaned) > MAX_UNIT_SYMBOL:
            raise InvalidChiffrageInputError(f"Unit cannot exceed {MAX_UNIT_SYMBOL} characters.")
        key = unit_key(cleaned)
        if key in PRESET_BY_KEY:
            raise UnitAlreadyExistsError(f"'{cleaned}' is already a built-in unit ('{PRESET_BY_KEY[key]}').")
        if any(unit_key(u.symbol) == key for u in self._repo.list_units(project_id)):
            raise UnitAlreadyExistsError(f"'{cleaned}' is already a unit of this project.")

        unit = ChiffrageUnit.create(project_id=project_id, symbol=cleaned)
        self._repo.add_unit(unit)
        self._db.commit()
        return unit


class DeleteUnitUseCase:
    """Remove a custom unit that no article uses.

    A unit still used by articles is refused (409): the articles would keep a
    symbol the project no longer offers, and could not be saved unchanged.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, unit_id: UUID) -> None:
        unit = self._repo.find_unit(unit_id)
        if unit is None or unit.project_id != project_id:
            raise UnitNotFoundError(f"Unit {unit_id} not found in project {project_id}.")
        in_use = self._repo.count_articles_with_unit(project_id, unit.symbol)
        if in_use:
            raise UnitInUseError(f"Unit '{unit.symbol}' is used by {in_use} article(s).")
        self._repo.delete_unit(unit_id)
        self._db.commit()
