"""Request bodies for the push device routes."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RegisterPushDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=10, max_length=255)
    platform: Literal["ios", "android"]


class UnregisterPushDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=10, max_length=255)
