from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

LicenseCode = Annotated[str, StringConstraints(min_length=1, max_length=32)]


class OrderCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=64)
    origin: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)
    weight_kg: float = Field(gt=0, le=100_000)
    due_at: datetime
    required_license: LicenseCode | None = None


class Order(OrderCreate):
    id: int
    status: Literal["pending", "planned", "completed", "cancelled"]
    created_at: datetime


class VehicleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    capacity_kg: float = Field(gt=0, le=100_000)
    driver_name: str = Field(min_length=1, max_length=120)
    licenses: list[LicenseCode] = Field(default_factory=list)

    @field_validator("licenses")
    @classmethod
    def _dedupe_licenses(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class Vehicle(BaseModel):
    id: int
    code: str
    capacity_kg: float
    status: Literal["available", "assigned", "maintenance"]
    driver_name: str
    licenses: list[str]


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

