from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=64)
    origin: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)
    weight_kg: float = Field(gt=0, le=100_000)
    due_at: datetime


class Order(OrderCreate):
    id: int
    status: Literal["pending", "planned", "completed", "cancelled"]
    created_at: datetime


class Vehicle(BaseModel):
    id: int
    code: str
    capacity_kg: float
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


class DispatchBatch(BaseModel):
    batch_id: int
    created_at: datetime
    assignments: list[DispatchAssignment]
    unassigned_order_ids: list[int]


class DispatchBatchSummary(BaseModel):
    batch_id: int
    created_at: datetime
    assignment_count: int
    unassigned_order_ids: list[int]

