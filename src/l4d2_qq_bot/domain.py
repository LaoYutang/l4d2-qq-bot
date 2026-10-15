"""Platform-independent query and card models."""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Player:
    name: str
    score: int = 0
    duration_seconds: float = 0


@dataclass(frozen=True, slots=True)
class ServerStatus:
    alias: str
    address: str
    online: bool
    server_name: str = ""
    map_code: str = ""
    map_name: str = ""
    player_count: int = 0
    max_players: int = 0
    ping_ms: int = 0
    players: tuple[Player, ...] | None = None
    error: str = ""
    queried_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CardSection:
    heading: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Card:
    title: str
    subtitle: str = ""
    sections: tuple[CardSection, ...] = ()
    accent: str = "#b5f36d"
    footer: str = "L4D2 · SERVER INTELLIGENCE"
