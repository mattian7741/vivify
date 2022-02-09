from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ergo.util import uniqueid


@dataclass
class Scope:
    id: str = field(default_factory=uniqueid)
    parent: Optional[Scope] = None