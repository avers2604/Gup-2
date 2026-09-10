"""Small in-process domain event bus.

Domain events decouple write-model code from audit/session/password-history
side effects. By default handlers run synchronously in the current database
transaction: this keeps critical audit/history changes atomic with the write
and makes rollback semantics deterministic. Non-critical integrations can use
``publish_after_commit`` explicitly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DomainEvent:
    name: str
    payload: dict[str, Any]


Handler = Callable[[DomainEvent], None]
_HANDLERS: dict[str, list[Handler]] = {}


def register(event_name: str) -> Callable[[Handler], Handler]:
    """Register an in-process handler for a named event."""

    def decorator(handler: Handler) -> Handler:
        handlers = _HANDLERS.setdefault(event_name, [])
        if handler not in handlers:
            handlers.append(handler)
        return handler

    return decorator


def _dispatch(event: DomainEvent) -> None:
    for handler in tuple(_HANDLERS.get(event.name, ())):
        handler(event)


def publish(event_name: str, **payload: Any) -> None:
    """Dispatch inside the current transaction.

    Critical database side effects such as the WORM audit log and password
    history must succeed or fail together with the state change, so exceptions
    intentionally propagate to the caller.
    """
    _dispatch(DomainEvent(name=event_name, payload=payload))


def publish_after_commit(event_name: str, **payload: Any) -> None:
    """Dispatch only after a successful commit for non-critical integrations."""
    event = DomainEvent(name=event_name, payload=payload)

    def callback() -> None:
        try:
            _dispatch(event)
        except Exception:
            logger.exception("Post-commit domain event handler failed: %s", event_name)

    transaction.on_commit(callback)
