from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class OrderCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=64)
    origin: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)
    weight_kg: float = Field(gt=0, le=100_000)
    due_at: datetime
    # Omitted or null means the order needs no special driver credential.
    required_license: str | None = Field(default=None, min_length=1, max_length=32)


class Order(OrderCreate):
    id: int
    status: Literal["pending", "planned", "completed", "cancelled"]
    created_at: datetime


class VehicleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    capacity_kg: float = Field(gt=0, le=100_000)
    driver_name: str = Field(min_length=1, max_length=120)
    licenses: list[str] = Field(default_factory=list)

    @field_validator("driver_name")
    @classmethod
    def _driver_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("driver name must not be empty")
        return value

    @field_validator("licenses")
    @classmethod
    def _dedupe_licenses(cls, value: list[str]) -> list[str]:
        # Every code must be a non-empty 1-32 char credential; keep first-seen order.
        seen: set[str] = set()
        deduped: list[str] = []
        for license_code in value:
            if not license_code or len(license_code) > 32:
                raise ValueError("license codes must be 1-32 characters")
            if license_code not in seen:
                seen.add(license_code)
                deduped.append(license_code)
        return deduped


class Vehicle(BaseModel):
    id: int
    code: str
    capacity_kg: float
    driver_name: str
    licenses: list[str]
    status: Literal["available", "assigned", "maintenance"]


class DispatchAssignment(BaseModel):
    order_id: int
    vehicle_id: int
    vehicle_code: str
    reason: str


class DispatchPlan(BaseModel):
    generated_at: datetime
    assignments: list[DispatchAssignment]
    unassigned_order_ids: list[int]
    unassigned_reasons: dict[int, str]


class DispatchBatchSummary(BaseModel):
    batch_id: int
    created_at: datetime
    assignment_count: int
    unassigned_count: int


class DispatchBatchResult(BaseModel):
    batch_id: int
    created_at: datetime
    assignments: list[DispatchAssignment]
    unassigned_order_ids: list[int]
    unassigned_reasons: dict[int, str]
