"""The interface strategies implement (SPEC 6.1)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from .pit import PITView


@dataclass
class EntryRule:
    kind: Literal["market", "limit"] = "market"
    price: float | None = None  # limit price
    expiry: timedelta = timedelta(hours=1)  # limit orders not filled by then are cancelled


@dataclass
class Signal:
    coin: str
    side: Literal["long", "short"]
    entry: EntryRule
    stop: float
    target: float
    time_limit: timedelta
    features: dict[str, float] = field(default_factory=dict)  # snapshot for the meta-model


class Strategy(Protocol):
    def __init__(self, params: dict): ...

    def on_bar(self, t: datetime, view: PITView) -> list[Signal]:
        """view only returns rows with available_time <= t."""
