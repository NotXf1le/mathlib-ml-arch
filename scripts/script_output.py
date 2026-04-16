from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable


def append_unique(items: list[str], message: str | None) -> None:
    if message and message not in items:
        items.append(message)


@dataclass(frozen=True)
class PayloadEmitter:
    json_enabled: bool
    human_printer: Callable[[dict[str, object]], None]

    def emit(self, payload: dict[str, object]) -> None:
        if self.json_enabled:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        self.human_printer(payload)
