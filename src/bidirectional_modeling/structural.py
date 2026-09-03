"""Deterministic identities and isolated copies for semantic values.

Proof-producing code must never use an object's display representation as its
identity.  This module is the single strict codec used by contexts, macro
specifications, closure analysis, and residual quotients.
"""

from __future__ import annotations

import hashlib
import math
from copy import deepcopy
from enum import Enum
from functools import partial
from types import CodeType, FunctionType, MethodType
from typing import Any, Callable, Mapping, Optional, Set, Tuple


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


def fingerprint_value(
    value: Any, *, purpose: str = "deterministic structural fingerprint"
) -> str:
    """Return a SHA-256 digest of one strictly canonicalized value."""

    frozen = freeze_value(value, purpose=purpose)
    return hashlib.sha256(repr(frozen).encode("utf-8")).hexdigest()


def validate_fingerprint(value: str, *, purpose: str = "fingerprint") -> None:
    """Require a lowercase hexadecimal SHA-256 digest."""

    if (
        type(value) is not str
        or len(value) != 64
        or value.lower() != value
    ):
        raise ValueError("%s must be a lowercase SHA-256 digest" % purpose)
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(
            "%s must be a lowercase SHA-256 digest" % purpose
        ) from error


def _code_constant_signature(value: Any, purpose: str) -> FrozenValue:
    if isinstance(value, CodeType):
        return _code_signature(value, purpose)
    if value is Ellipsis:
        return ("ellipsis",)
    if type(value) is complex:
        return ("complex", value.real.hex(), value.imag.hex())
    return freeze_value(value, purpose=purpose)


def _code_signature(code: CodeType, purpose: str) -> FrozenValue:
    """Canonicalize behavior-relevant code fields without file/line metadata."""

    return (
        "python-code-v1",
        code.co_argcount,
        getattr(code, "co_posonlyargcount", 0),
        code.co_kwonlyargcount,
        code.co_flags,
        code.co_code.hex(),
        getattr(code, "co_exceptiontable", b"").hex(),
        tuple(
            _code_constant_signature(item, purpose) for item in code.co_consts
        ),
        tuple(code.co_names),
        tuple(code.co_varnames),
        tuple(code.co_freevars),
        tuple(code.co_cellvars),
    )


def _code_global_names(code: CodeType) -> Set[str]:
    names = set(code.co_names)
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            names.update(_code_global_names(constant))
    return names


def _callable_binding(
    value: Any,
    semantic_id: Optional[str],
    purpose: str,
) -> FrozenValue:
    try:
        return freeze_value(value, purpose=purpose)
    except TypeError:
        if semantic_id is None:
            raise TypeError(
                "%s depends on an opaque value; provide an explicit semantic ID"
                % purpose
            )
        return (
            "declared-opaque-binding",
            type(value).__module__,
            type(value).__qualname__,
        )


def _function_global_bindings(
    value: FunctionType,
    semantic_id: Optional[str],
    purpose: str,
) -> FrozenValue:
    """Capture globals actually named by a function's bytecode.

    Builtins are absent from ``__globals__`` and need no binding. Canonical
    constants are recorded by value. Imported modules, classes, helpers, and
    other opaque dependencies require a caller-owned semantic ID, which is the
    explicit version contract for behavior outside the function itself.
    """

    return (
        "function-globals-v1",
        tuple(
            (
                name,
                _callable_binding(value.__globals__[name], semantic_id, purpose),
            )
            for name in sorted(_code_global_names(value.__code__))
            if name in value.__globals__
        ),
    )


def _bound_owner_binding(
    value: Any,
    semantic_id: Optional[str],
    purpose: str,
) -> FrozenValue:
    state = getattr(value, "__dict__", None)
    if type(state) is dict:
        return (
            "bound-owner-state-v1",
            type(value).__module__,
            type(value).__qualname__,
            _callable_binding(state, semantic_id, purpose),
        )
    return _callable_binding(value, semantic_id, purpose)


def callable_signature(
    value: Callable[..., Any],
    *,
    semantic_id: Optional[str] = None,
    purpose: str = "callable identity",
) -> FrozenValue:
    """Build a fail-closed identity for a Python callable.

    Python functions are identified by canonical code, defaults, and closure
    bindings. An explicit semantic ID can describe opaque external bindings or
    implementations, but callers remain responsible for changing that ID when
    such external behavior changes.
    """

    if not callable(value):
        raise TypeError("%s requires a callable" % purpose)
    if semantic_id is not None and (
        type(semantic_id) is not str or not semantic_id
    ):
        raise ValueError("%s semantic ID must be a non-empty string" % purpose)

    if isinstance(value, partial):
        return (
            "partial-callable-v1",
            semantic_id,
            callable_signature(
                value.func,
                semantic_id=semantic_id,
                purpose=purpose,
            ),
            _callable_binding(value.args, semantic_id, purpose),
            _callable_binding(value.keywords or {}, semantic_id, purpose),
        )

    if isinstance(value, MethodType):
        function = value.__func__
        return (
            "bound-method-v1",
            semantic_id,
            function.__module__,
            function.__qualname__,
            _code_signature(function.__code__, purpose),
            _function_global_bindings(function, semantic_id, purpose),
            _bound_owner_binding(value.__self__, semantic_id, purpose),
        )

    if isinstance(value, FunctionType):
        closure = []
        for cell in value.__closure__ or ():
            try:
                binding = cell.cell_contents
            except ValueError:
                closure.append(("empty-cell",))
            else:
                closure.append(
                    _callable_binding(binding, semantic_id, purpose)
                )
        return (
            "python-function-v1",
            semantic_id,
            value.__module__,
            value.__qualname__,
            _code_signature(value.__code__, purpose),
            _function_global_bindings(value, semantic_id, purpose),
            _callable_binding(value.__defaults__ or (), semantic_id, purpose),
            _callable_binding(value.__kwdefaults__ or {}, semantic_id, purpose),
            tuple(closure),
        )

    call_method = getattr(type(value), "__call__", None)
    code = getattr(call_method, "__code__", None)
    if isinstance(code, CodeType):
        return (
            "callable-object-v1",
            semantic_id,
            type(value).__module__,
            type(value).__qualname__,
            _code_signature(code, purpose),
            _function_global_bindings(call_method, semantic_id, purpose),
            _bound_owner_binding(value, semantic_id, purpose),
        )

    if semantic_id is None:
        raise TypeError(
            "%s cannot inspect this callable; provide an explicit semantic ID"
            % purpose
        )
    return (
        "declared-callable-v1",
        semantic_id,
        type(value).__module__,
        type(value).__qualname__,
    )


def callable_fingerprint(
    value: Callable[..., Any],
    *,
    semantic_id: Optional[str] = None,
    purpose: str = "callable fingerprint",
) -> str:
    """Return a SHA-256 digest for :func:`callable_signature`."""

    return fingerprint_value(
        callable_signature(
            value,
            semantic_id=semantic_id,
            purpose=purpose,
        ),
        purpose=purpose,
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
