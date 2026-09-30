"""Read model cost and call counts from one append-only case ledger snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sawon_interfaces import RunEvent


class EventReader(Protocol):
    def read_all(self) -> list[RunEvent]: ...


@dataclass(frozen=True)
class ModelAccounting:
    known_usd: Decimal
    unknown: bool
    model_calls: int


def read_model_accounting(log: EventReader) -> ModelAccounting:
    calls = [event for event in log.read_all() if event.kind == "model_call"]
    return ModelAccounting(
        known_usd=sum(
            (Decimal(str(event.cost.usd)) for event in calls if event.cost.usd is not None),
            start=Decimal(0),
        ),
        unknown=any(event.cost.usd is None for event in calls),
        model_calls=len(calls),
    )
