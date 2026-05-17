"""Labor role request/response schemas."""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, field_validator

ROLE_COLOR_PALETTE = [
    "#E11D48",
    "#7C3AED",
    "#0EA5E9",
    "#10B981",
    "#F59E0B",
    "#EC4899",
    "#3B82F6",
    "#84CC16",
    "#F97316",
    "#06B6D4",
    "#A855F7",
    "#22C55E",
]


class CreateLaborRoleRequest(BaseModel):
    name: str
    color: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Name must not be empty")
        if len(v) > 100:
            raise ValueError("Name must not exceed 100 characters")
        return v.strip()

    @field_validator("color")
    @classmethod
    def color_valid_hex(cls, v: str) -> str:
        if not re.match(r"^#[0-9a-fA-F]{6}$", v):
            raise ValueError("Color must be a valid hex (#RRGGBB)")
        return v


class UpdateLaborRoleRequest(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            if not v.strip():
                raise ValueError("Name must not be empty")
            if len(v) > 100:
                raise ValueError("Name must not exceed 100 characters")
            return v.strip()
        return v

    @field_validator("color")
    @classmethod
    def color_valid_hex(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match(r"^#[0-9a-fA-F]{6}$", v):
            raise ValueError("Color must be a valid hex (#RRGGBB)")
        return v


class LaborRoleResponse(BaseModel):
    id: str
    name: str
    color: str
    created_at: str


class LaborRoleListResponse(BaseModel):
    roles: List[LaborRoleResponse]
    palette: List[str]
