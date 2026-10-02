"""Deterministic provenance bindings shared by proof-producing modules.

Fingerprints in this module are consistency and replay guards, not signatures.
They bind a result to canonical declarations and to the bounded observations
that were actually evaluated; they do not prove behavior outside that domain.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

from .core import Context, EquivalenceSpec, ExecutableModel, FiniteStateModel, MacroSpec, Trace
from .structural import fingerprint_value, freeze_value, ordered_tuple


def _safe_model_name(model: ExecutableModel) -> str:
    try:
        return str(getattr(model, "name"))
    except Exception:
        return type(model).__name__


def context_fingerprint(context: Context) -> str:
    """Return a stable digest for a declared context and scenario domain."""

    return fingerprint_value(
        ("context-v2", context.semantic_signature()),
        purpose="context fingerprint deterministic structural identity",
    )


def macro_spec_fingerprint(spec: MacroSpec) -> str:
    """Bind a certificate to one named macro specification."""

    for requirement in spec.requirements:
        if not callable(getattr(requirement, "semantic_signature", None)):
            raise TypeError(
                "macro specification fingerprint requires every requirement "
                "to declare a semantic_signature"
            )
    return fingerprint_value(
        ("macro-spec-v1", spec.name, spec.semantic_signature()),
        purpose="macro specification deterministic structural fingerprint",
    )


def equivalence_fingerprint(equivalence: EquivalenceSpec) -> str:
    """Bind a quotient report to one observation-equivalence declaration."""

    return fingerprint_value(
        ("equivalence-v1", equivalence.semantic_signature()),
        purpose="equivalence deterministic structural fingerprint",
    )


def _trace_signatures(traces):
    signatures = []
    for index, trace in enumerate(traces):
        if isinstance(trace, Trace):
            value = (trace.model_name, trace.initial_state, trace.intervention,
                     tuple(dict(snapshot) for snapshot in trace.snapshots))
        else:
            value = ("invalid-trace", index, type(trace).__module__, type(trace).__qualname__)
        signatures.append(freeze_value(value, purpose="ordered trace evidence"))
    return tuple(signatures)


def observed_model_fingerprint(
    model: ExecutableModel,
    traces: Iterable[Any],
    horizon: int,
) -> str:
    """Fingerprint bounded evidence in the exact order seen by requirements."""
    metrics = getattr(model, "metrics", None)
    resource_signature = (None if metrics is None else
                          tuple(getattr(metrics, key) for key in ("cost", "complexity", "risk")))
    return fingerprint_value(
        ("observed-model-v3", type(model).__module__, type(model).__qualname__,
         _safe_model_name(model), resource_signature, horizon, _trace_signatures(traces)),
        purpose="model evidence fingerprint",
    )


def _model_input_snapshot(model):
    """Local drift guard; callback references never enter persistent fingerprints.

    Opaque implementations retain the observed metadata contract. This does not
    certify hidden state or arbitrary callback code without bounded execution.
    """
    digest, error = safe_observed_model_fingerprint(model, (), 1)
    if error is not None:
        return None
    try:
        metadata = tuple(ordered_tuple(getattr(model, name, ()))
                         for name in ('assumptions', 'failure_boundaries', 'capabilities'))
        metadata = freeze_value(metadata)
    except Exception:
        return None
    if isinstance(model, FiniteStateModel):
        try:
            configuration = freeze_value((metadata, model.states, ordered_tuple(model.initial_states),
                                          ordered_tuple(model.actions)))
            callbacks = (model.transition, model.readout, model.applicable,
                         vars(model).get('simulate'))
        except Exception:
            return None
    else:
        configuration, callbacks = metadata, ()
    return digest, configuration, callbacks


def _model_input_unchanged(model, snapshot):
    current = _model_input_snapshot(model)
    return (snapshot is not None and current is not None and snapshot[:2] == current[:2]
            and len(snapshot[2]) == len(current[2])
            and all(old is new for old, new in zip(snapshot[2], current[2])))


def _safe_fingerprint(
    operation: Any,
    fallback: Tuple[Any, ...],
    purpose: str,
) -> Tuple[str, Optional[str]]:
    try:
        return operation(), None
    except Exception as error:
        digest = fingerprint_value(
            fallback,
            purpose="uncertifiable %s fingerprint" % purpose,
        )
        return digest, "%s: %s" % (type(error).__name__, error)


def safe_context_fingerprint(context: Context) -> Tuple[str, Optional[str]]:
    """Return a valid digest plus an error when the context is uncertifiable."""

    return _safe_fingerprint(
        lambda: context_fingerprint(context),
        ("uncertifiable-context-v1", type(context).__module__, type(context).__qualname__),
        "context",
    )


def safe_macro_spec_fingerprint(spec: MacroSpec) -> Tuple[str, Optional[str]]:
    """Return a valid digest plus an error when the spec is uncertifiable."""

    try:
        name = str(getattr(spec, "name"))
    except Exception:
        name = type(spec).__name__
    return _safe_fingerprint(
        lambda: macro_spec_fingerprint(spec),
        ("uncertifiable-macro-spec-v1", type(spec).__module__, type(spec).__qualname__, name),
        "macro specification",
    )


def safe_equivalence_fingerprint(
    equivalence: EquivalenceSpec,
) -> Tuple[str, Optional[str]]:
    """Return a valid digest plus an error when equivalence is uncertifiable."""

    return _safe_fingerprint(
        lambda: equivalence_fingerprint(equivalence),
        (
            "uncertifiable-equivalence-v1",
            type(equivalence).__module__,
            type(equivalence).__qualname__,
        ),
        "equivalence",
    )


def safe_observed_model_fingerprint(
    model: ExecutableModel,
    traces: Iterable[Any],
    horizon: int,
) -> Tuple[str, Optional[str]]:
    """Return a valid digest plus an error when trace evidence is uncertifiable."""

    return _safe_fingerprint(
        lambda: observed_model_fingerprint(model, traces, horizon),
        (
            "uncertifiable-observed-model-v1",
            type(model).__module__,
            type(model).__qualname__,
            _safe_model_name(model),
            horizon,
        ),
        "model evidence",
    )


def trace_batch_protocol_fingerprint(
    model_digest: str,
    context_digest: str,
    horizon: int,
    simulation_limit: int,
    coverage_authority: str,
    complete: bool,
    coverage: float,
    diagnostic_codes: Tuple[str, ...],
    traces: Tuple[Trace, ...],
) -> str:
    """Fingerprint both the trace-collection protocol and certified outcome."""

    evidence_digest, _ = _safe_fingerprint(
        lambda: fingerprint_value(('ordered-traces-v1', _trace_signatures(traces))),
        ('uncertifiable-traces-v1',), 'trace evidence')
    return fingerprint_value(
        (
            "trace-batch-v3",
            model_digest,
            context_digest,
            horizon,
            simulation_limit,
            coverage_authority,
            complete,
            coverage,
            tuple(sorted(set(diagnostic_codes))),
            evidence_digest,
        ),
        purpose="trace batch protocol fingerprint",
    )


def satisfaction_protocol_fingerprint(
    spec_digest: str,
    model_digest: str,
    context_digest: str,
    trace_batch_digest: str,
    max_cost: float,
) -> str:
    """Fingerprint the inputs and resource constraint of one satisfaction run."""

    return fingerprint_value(
        (
            "satisfaction-evaluator-v2",
            spec_digest,
            model_digest,
            context_digest,
            trace_batch_digest,
            max_cost,
        ),
        purpose="satisfaction evaluation protocol fingerprint",
    )
