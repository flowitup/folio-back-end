"""Drop placement shared by the poste, article and room reorder use-cases.

A moved row takes a free integer between its new neighbours, so a drag
normally rewrites one row. When there is no free integer left (the gap was
halved down to nothing, or two rows already share a position) the siblings are
renumbered once, in display order, which gives every gap its full step back.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol, Sequence, TypeVar
from uuid import UUID

from app.application.chiffrage.units import POSITION_STEP


class _Positioned(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def position(self) -> int: ...


T = TypeVar("T", bound=_Positioned)


def free_slot(
    before: Optional[_Positioned], after: Optional[_Positioned], last_position: Callable[[], int]
) -> Optional[int]:
    """A position strictly between the neighbours, or None when no integer is free.

    With no neighbour at all the row goes to the end, after *last_position()*.

    Positions may go below zero: clamping a drop at the head to 0 tied it with a
    head row already at 0, and the tie then put the moved row second.
    """
    if before is not None and after is not None:
        if after.position - before.position < 2:
            return None
        return (before.position + after.position) // 2
    if before is not None:
        return before.position + POSITION_STEP
    if after is not None:
        return after.position - POSITION_STEP
    return last_position() + POSITION_STEP


def renumber_around(
    siblings: Sequence[T],
    moving: T,
    before_id: Optional[UUID],
    after_id: Optional[UUID],
    move: Callable[[T, int], None],
) -> int:
    """Renumber *siblings* (in display order) with *moving* in its new slot.

    Calls *move* for every other sibling whose position changes and returns the
    moved row's new position; the caller saves the moved row itself.
    """
    order = [s for s in siblings if s.id != moving.id]
    ids = [s.id for s in order]
    if before_id is not None and before_id in ids:
        index = ids.index(before_id) + 1
    elif after_id is not None and after_id in ids:
        index = ids.index(after_id)
    else:
        index = len(order)
    order.insert(index, moving)
    for i, sibling in enumerate(order):
        position = (i + 1) * POSITION_STEP
        if sibling.id != moving.id and sibling.position != position:
            move(sibling, position)
    return (index + 1) * POSITION_STEP
