"""Small in-process domain event bus.

Synchronous ``publish()`` is reserved for critical effects that must succeed or
fail atomically with the source transaction: WORM audit, password history,
session invalidation. A missing subscriber is therefore a runtime error rather
than a silent no-op. Non-critical integrations use ``publish_after_commit()``;
there an absent subscriber or a handler failure cannot roll back a commit and
is intentionally tolerated/logged.
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


class MissingDomainEventHandler(RuntimeError):
    """Critical synchronous event has no registered consumer."""


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


def require_handlers(*event_names: str) -> None:
    """Fail application startup if declared critical subscriptions are missing."""
    missing = [name for name in event_names if not _HANDLERS.get(name)]
    if missing:
        raise MissingDomainEventHandler(
            "Critical domain events have no registered handlers: " + ", ".join(missing)
        )


def _dispatch(event: DomainEvent, *, require_handler: bool) -> None:
    handlers = tuple(_HANDLERS.get(event.name, ()))
    if require_handler and not handlers:
        raise MissingDomainEventHandler(
            f"Critical domain event {event.name!r} has no registered handlers"
        )
    for handler in handlers:
        handler(event)


def publish(event_name: str, **payload: Any) -> None:
    """Dispatch a critical event inside the current transaction.

    Critical database side effects such as the WORM audit log and password
    history must succeed or fail together with the state change. Handler
    exceptions intentionally propagate, and a missing handler is itself an
    error so a broken AppConfig import cannot silently disable the side effect.
    """
    _dispatch(
        DomainEvent(name=event_name, payload=payload),
        require_handler=True,
    )


def publish_after_commit(event_name: str, **payload: Any) -> None:
    """Dispatch after a successful commit for non-critical integrations."""
    event = DomainEvent(name=event_name, payload=payload)

    def callback() -> None:
        try:
            _dispatch(event, require_handler=False)
        except Exception:
            logger.exception("Post-commit domain event handler failed: %s", event_name)

    transaction.on_commit(callback)
