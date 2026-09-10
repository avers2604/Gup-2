"""Lightweight domain event bus.

The goal is to keep write-model code focused on state changes while moving
side effects (audit logs, session invalidation, notifications, etc.) to
post-commit handlers.
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
    """Register a handler for a named event."""

    def decorator(handler: Handler) -> Handler:
        _HANDLERS.setdefault(event_name, []).append(handler)
        return handler

    return decorator


def publish(event_name: str, **payload: Any) -> None:
    """Schedule event dispatch after the current DB transaction commits."""
    event = DomainEvent(name=event_name, payload=payload)

    def _dispatch() -> None:
        for handler in _HANDLERS.get(event_name, []):
            try:
                handler(event)
            except Exception:
                logger.exception("Domain event handler failed: %s", event_name)

    transaction.on_commit(_dispatch)
