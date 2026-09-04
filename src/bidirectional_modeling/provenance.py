"""Deterministic provenance bindings shared by proof-producing modules.

Fingerprints in this module are consistency and replay guards, not signatures.
They bind a result to canonical declarations and to the bounded observations
that were actually evaluated; they do not prove behavior outside that domain.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Tuple

from .core import (
    CheckResult,
    ConfidenceBreakdown,
    Context,
    EquivalenceSpec,
    ExecutableModel,
    MacroSpec,
    Trace,
)
from .structural import fingerprint_value, freeze_value


def _safe_model_name(model: ExecutableModel) -> str:
    try:
        return str(getattr(model, "name"))
    except Exception:
        return type(model).__name__


def context_fingerprint(context: Context) -> str:
    """Return a stable digest for a declared context and scenario domain."""

    return fingerprint_value(
        ("context-v1", context.semantic_signature()),
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


def observed_model_fingerprint(
    model: ExecutableModel,
    traces: Iterable[Any],
    horizon: int,
) -> str:
    """Fingerprint a model's identity and its observed, bounded trace evidence."""

    trace_signatures = []
    for index, trace in enumerate(traces):
        if not isinstance(trace, Trace):
            trace_signatures.append(
                freeze_value(
                    (
                        "invalid-trace",
                        index,
                        type(trace).__module__,
                        type(trace).__qualname__,
                    ),
                    purpose="model evidence fingerprint",
                )
            )
            continue
        trace_signatures.append(
            freeze_value(
                (
                    trace.model_name,
                    trace.initial_state,
                    trace.intervention,
                    tuple(dict(snapshot) for snapshot in trace.snapshots),
                ),
                purpose="model evidence fingerprint",
            )
        )
    return fingerprint_value(
        (
            "observed-model-v1",
            type(model).__module__,
            type(model).__qualname__,
            _safe_model_name(model),
            horizon,
            tuple(sorted(trace_signatures)),
        ),
        purpose="model evidence fingerprint",
    )


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
    boundaries: Tuple[str, ...],
) -> str:
    """Fingerprint both the trace-collection protocol and certified outcome."""

    return fingerprint_value(
        (
            "trace-batch-v1",
            model_digest,
            context_digest,
            horizon,
            simulation_limit,
            coverage_authority,
            complete,
            coverage,
            boundaries,
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
            "satisfaction-evaluator-v1",
            spec_digest,
            model_digest,
            context_digest,
            trace_batch_digest,
            max_cost,
        ),
        purpose="satisfaction evaluation protocol fingerprint",
    )


def satisfaction_claim_fingerprint(
    protocol_digest: str,
    spec_name: str,
    model_name: str,
    satisfied: bool,
    checks: Tuple[CheckResult, ...],
    verified_scenarios: int,
    confidence: ConfidenceBreakdown,
    assumptions: Tuple[str, ...],
    failure_boundaries: Tuple[str, ...],
    horizon: int,
    complete: bool,
    requirements_passed: bool,
    coverage_authority: str,
) -> str:
    """Fingerprint the complete semantic outcome of a satisfaction run.

    ``protocol_digest`` identifies what was evaluated.  This second digest
    identifies what the certificate claims happened, so result fields cannot
    be replaced while retaining the original protocol identity.
    """

    check_signatures = tuple(
        (
            "check-result-v1",
            item.name,
            item.category.value,
            item.passed,
            item.observed,
            item.expected,
            item.robustness,
            item.detail,
        )
        for item in checks
    )
    return fingerprint_value(
        (
            "satisfaction-claim-v1",
            protocol_digest,
            spec_name,
            model_name,
            satisfied,
            check_signatures,
            verified_scenarios,
            (
                confidence.coverage,
                confidence.robustness,
                confidence.assumption_reliability,
            ),
            assumptions,
            failure_boundaries,
            horizon,
            complete,
            requirements_passed,
            coverage_authority,
        ),
        purpose="satisfaction claim fingerprint",
    )
