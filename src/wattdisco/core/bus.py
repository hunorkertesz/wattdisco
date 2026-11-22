"""Tiny synchronous pub/sub. Sensors publish samples, everything else listens.

Handlers run inline on publish, so they must be quick and never block:
hand slow work (file writes, network) to a queue or a thread.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[Any], None]
AnyHandler = Callable[[str, Any], None]


class Bus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._any: list[AnyHandler] = []

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        self._handlers[topic].append(handler)
        return lambda: self._handlers[topic].remove(handler)

    def subscribe_all(self, handler: AnyHandler) -> Callable[[], None]:
        self._any.append(handler)
        return lambda: self._any.remove(handler)

    def publish(self, topic: str, payload: Any) -> None:
        for handler in list(self._handlers.get(topic, ())):
            try:
                handler(payload)
            except Exception:
                log.exception("handler for %r failed", topic)
        for handler in list(self._any):
            try:
                handler(topic, payload)
            except Exception:
                log.exception("catch-all handler failed on %r", topic)
