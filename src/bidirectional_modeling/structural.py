"""Deterministic identities and isolated copies for semantic values.

Proof-producing code must never use an object's display representation as its
identity.  This module is the single strict codec used by contexts, macro
specifications, closure analysis, and residual quotients.
"""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
import math
from typing import Any, Mapping, Optional, Set, Tuple


FrozenValue = Tuple[Any, ...]


def freeze_value(
    value: Any,
    *,
    purpose: str = "deterministic structural identity",
    _active: Optional[Set[int]] = None,
) -> FrozenValue:
    """Return a deterministic, hashable identity for supported values.

    Only primitives, enums, and ordinary containers of supported values are
    accepted.  Opaque objects and cyclic containers fail explicitly instead of
    falling back to ``repr()``, object identity, or process-local hashes.
    """

    active = set() if _active is None else _active
    if value is None:
        return ("none",)
    # Enum must precede primitive checks because IntEnum and ``str, Enum``
    # subclasses would otherwise collide with their raw values.
    if isinstance(value, Enum):
        return (
            "enum",
            type(value).__module__,
            type(value).__qualname__,
            freeze_value(value.value, purpose=purpose, _active=active),
        )
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        if math.isnan(value):
            return ("float", "nan")
        return ("float", value.hex())
    if type(value) is str:
        return ("str", value)
    if type(value) is bytes:
        return ("bytes", value.hex())

    if type(value) is dict:
        marker = id(value)
        if marker in active:
            raise TypeError("%s does not support cyclic containers" % purpose)
        active.add(marker)
        try:
            items = [
                (
                    freeze_value(key, purpose=purpose, _active=active),
                    freeze_value(item, purpose=purpose, _active=active),
                )
                for key, item in value.items()
            ]
            return ("mapping", tuple(sorted(items)))
        finally:
            active.remove(marker)

    if type(value) in (list, tuple):
        marker = id(value)
        if marker in active:
            raise TypeError("%s does not support cyclic containers" % purpose)
        active.add(marker)
        try:
            return (
                type(value).__name__,
                tuple(
                    freeze_value(item, purpose=purpose, _active=active)
                    for item in value
                ),
            )
        finally:
            active.remove(marker)

    if type(value) in (set, frozenset):
        marker = id(value)
        if marker in active:
            raise TypeError("%s does not support cyclic containers" % purpose)
        active.add(marker)
        try:
            items = (
                freeze_value(item, purpose=purpose, _active=active)
                for item in value
            )
            return (type(value).__name__, tuple(sorted(items)))
        finally:
            active.remove(marker)

    raise TypeError(
        "%s requires canonical primitive/container values; got %s.%s"
        % (purpose, type(value).__module__, type(value).__qualname__)
    )


def isolated_copy(value: Any, *, purpose: str = "model state") -> Any:
    """Deep-copy a callback boundary or fail with a domain-specific message."""

    try:
        return deepcopy(value)
    except Exception as error:
        raise TypeError("%s could not be isolated: %s" % (purpose, error)) from error


def isolated_mapping(
    value: Mapping[str, Any], *, purpose: str = "model state"
) -> dict[str, Any]:
    """Copy a mapping deeply and normalize its outer container to ``dict``."""

    if not isinstance(value, Mapping):
        raise TypeError("%s must be a mapping" % purpose)
    copied = isolated_copy(value, purpose=purpose)
    if not isinstance(copied, Mapping):
        raise TypeError("%s must remain a mapping after isolation" % purpose)
    return dict(copied)
