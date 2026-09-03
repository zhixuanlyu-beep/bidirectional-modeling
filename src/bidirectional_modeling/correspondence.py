"""Explicit, verifiable correspondences between adjacent modeling scales.

A correspondence is a caller-owned claim that projecting a lower-scale trace
produces the same task-relevant history as an upper-scale executable model.
The validator checks that claim over independently certified scenario domains.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple, Union

from .core import (
    Context,
    EquivalenceSpec,
    ExecutableModel,
    NonDeterministicModelError,
    ResourceBudget,
    ScenarioKey,
    Snapshot,
    Trace,
)
from .evaluation import SatisfactionEvaluator, TraceBatch
from .provenance import (
    context_fingerprint,
    observed_model_fingerprint as _model_evidence_fingerprint,
    safe_observed_model_fingerprint as _safe_model_evidence_fingerprint,
)
from .structural import (
    callable_fingerprint,
    fingerprint_value,
    freeze_value,
    isolated_copy,
    isolated_mapping,
    validate_fingerprint,
)


SnapshotProjection = Callable[[Snapshot, Context], Mapping[str, Any]]
ScenarioProjection = Callable[[ScenarioKey], ScenarioKey]


def _identity_scenario(key: ScenarioKey) -> ScenarioKey:
    return key


@dataclass(frozen=True)
class Scale:
    """Observable interface and equivalence relation at one modeling scale."""

    name: str
    observables: Tuple[str, ...]
    equivalence: EquivalenceSpec

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("scale name must be a non-empty string")
        if not self.observables:
            raise ValueError("a scale must declare at least one observable")
        if any(
            not isinstance(item, str) or not item for item in self.observables
        ):
            raise ValueError("scale observable names must be non-empty strings")
        if len(self.observables) != len(set(self.observables)):
            raise ValueError("scale observables must be unique")
        missing = set(self.equivalence.fields) - set(self.observables)
        if missing:
            raise ValueError(
                "scale equivalence fields must be declared observables: %s"
                % sorted(missing)
            )

    def semantic_signature(self) -> Tuple[Any, ...]:
        return (
            "scale-v1",
            self.name,
            tuple(sorted(self.observables)),
            self.equivalence.semantic_signature(),
        )

    def fingerprint(self) -> str:
        return fingerprint_value(
            self.semantic_signature(),
            purpose="scale deterministic structural fingerprint",
        )


@dataclass(frozen=True)
class Correspondence:
    """A directed coarse-graining claim from a lower to an upper scale."""

    name: str
    lower_scale: Scale
    upper_scale: Scale
    projection: SnapshotProjection
    scenario_projection: ScenarioProjection = _identity_scenario
    assumptions: Tuple[str, ...] = ()
    projection_id: Optional[str] = None
    scenario_projection_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("correspondence name must be non-empty")
        if self.lower_scale.name == self.upper_scale.name:
            raise ValueError("correspondence endpoints must be distinct scales")
        if not callable(self.projection):
            raise TypeError("correspondence projection must be callable")
        if not callable(self.scenario_projection):
            raise TypeError("scenario projection must be callable")
        for label, identity in (
            ("projection_id", self.projection_id),
            ("scenario_projection_id", self.scenario_projection_id),
        ):
            if identity is not None and (
                type(identity) is not str or not identity
            ):
                raise ValueError("%s must be a non-empty string" % label)

    def semantic_signature(self) -> Tuple[Any, ...]:
        return (
            "correspondence-v1",
            self.name,
            self.lower_scale.semantic_signature(),
            self.upper_scale.semantic_signature(),
            callable_fingerprint(
                self.projection,
                semantic_id=self.projection_id,
                purpose="snapshot projection fingerprint",
            ),
            callable_fingerprint(
                self.scenario_projection,
                semantic_id=self.scenario_projection_id,
                purpose="scenario projection fingerprint",
            ),
            tuple(sorted(self.assumptions)),
        )

    def fingerprint(self) -> str:
        return fingerprint_value(
            self.semantic_signature(),
            purpose="correspondence deterministic structural fingerprint",
        )


def correspondence_fingerprint(correspondence: Correspondence) -> str:
    """Bind a certificate to one concrete scale and projection claim."""

    return correspondence.fingerprint()


def _correspondence_binding_error(
    correspondence: Correspondence,
    expected: str,
) -> Optional[str]:
    try:
        observed = correspondence_fingerprint(correspondence)
    except Exception as error:
        return "correspondence identity could not be fingerprinted: %s: %s" % (
            type(error).__name__,
            error,
        )
    if observed != expected:
        return "correspondence identity changed after the certificate was bound"
    return None


def _audited_scenario_projection(
    correspondence: Correspondence,
    key: ScenarioKey,
) -> ScenarioKey:
    results = tuple(
        correspondence.scenario_projection(
            isolated_copy(key, purpose="scenario projection input")
        )
        for _ in range(2)
    )
    if any(not isinstance(item, ScenarioKey) for item in results):
        raise TypeError("scenario projection did not return ScenarioKey")
    if results[0] != results[1]:
        raise NonDeterministicModelError(
            "non-deterministic scenario projection in correspondence %s"
            % correspondence.name
        )
    return results[0]


def _audited_snapshot_projection(
    correspondence: Correspondence,
    snapshot: Snapshot,
    context: Context,
) -> Mapping[str, Any]:
    results = []
    for _ in range(2):
        projected = correspondence.projection(
            isolated_mapping(snapshot, purpose="snapshot projection input"),
            isolated_copy(context, purpose="snapshot projection context"),
        )
        results.append(
            isolated_mapping(projected, purpose="snapshot projection result")
        )
    identities = tuple(
        freeze_value(
            item,
            purpose="snapshot projection deterministic structural identity",
        )
        for item in results
    )
    if identities[0] != identities[1]:
        raise NonDeterministicModelError(
            "non-deterministic snapshot projection in correspondence %s"
            % correspondence.name
        )
    return results[0]


def _protocol_fingerprint(
    correspondence_digest: str,
    lower_model_digest: str,
    upper_model_digest: str,
    lower_context_digest: str,
    upper_context_digest: str,
    horizon: int,
    lower_coverage_authority: str,
    upper_coverage_authority: str,
    budget: ResourceBudget,
    simulation_limit: int,
) -> str:
    return fingerprint_value(
        (
            "correspondence-validator-v1",
            correspondence_digest,
            lower_model_digest,
            upper_model_digest,
            lower_context_digest,
            upper_context_digest,
            horizon,
            lower_coverage_authority,
            upper_coverage_authority,
            budget.max_candidates,
            simulation_limit,
            budget.max_cost,
        ),
        purpose="correspondence validation protocol fingerprint",
    )


@dataclass(frozen=True)
class CorrespondenceCounterexample:
    """One explicit failure of coverage, projection, or dynamic commutation."""

    kind: str
    detail: str
    lower_scenario: Optional[ScenarioKey] = None
    upper_scenario: Optional[ScenarioKey] = None
    step: Optional[int] = None
    lower_snapshot: Optional[Mapping[str, Any]] = None
    projected_snapshot: Optional[Mapping[str, Any]] = None
    upper_snapshot: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class CorrespondenceCertificate:
    """Evidence for one concrete lower-model -> upper-model correspondence."""

    correspondence_name: str
    lower_scale: str
    upper_scale: str
    lower_model_name: str
    upper_model_name: str
    horizon: int
    complete: bool
    commutes: bool
    lower_scenarios: int
    upper_scenarios: int
    paired_scenarios: int
    covered_upper_scenarios: int
    correspondence_fingerprint: str
    lower_model_fingerprint: str
    upper_model_fingerprint: str
    protocol_fingerprint: str
    lower_context_fingerprint: str
    upper_context_fingerprint: str
    counterexamples: Tuple[CorrespondenceCounterexample, ...] = ()
    assumptions: Tuple[str, ...] = ()
    boundaries: Tuple[str, ...] = ()
    lower_coverage_authority: str = "none"
    upper_coverage_authority: str = "none"
    simulations_used: int = 0

    def __post_init__(self) -> None:
        if any(
            not value
            for value in (
                self.correspondence_name,
                self.lower_scale,
                self.upper_scale,
                self.lower_model_name,
                self.upper_model_name,
            )
        ):
            raise ValueError("correspondence certificate identities must be non-empty")
        if self.horizon < 1:
            raise ValueError("correspondence horizon must be at least one")
        counts = (
            self.lower_scenarios,
            self.upper_scenarios,
            self.paired_scenarios,
            self.covered_upper_scenarios,
            self.simulations_used,
        )
        if min(counts) < 0:
            raise ValueError("correspondence counts must be non-negative")
        for label, fingerprint in (
            ("correspondence fingerprint", self.correspondence_fingerprint),
            ("lower model fingerprint", self.lower_model_fingerprint),
            ("upper model fingerprint", self.upper_model_fingerprint),
            ("protocol fingerprint", self.protocol_fingerprint),
            ("lower context fingerprint", self.lower_context_fingerprint),
            ("upper context fingerprint", self.upper_context_fingerprint),
        ):
            validate_fingerprint(fingerprint, purpose=label)

    @property
    def passed(self) -> bool:
        return self.complete and self.commutes

    def binds_correspondence(self, correspondence: Correspondence) -> bool:
        return (
            _correspondence_binding_error(
                correspondence,
                self.correspondence_fingerprint,
            )
            is None
        )


class CorrespondenceCaseRole(str, Enum):
    CALIBRATION = "calibration"
    HOLDOUT = "holdout"


@dataclass(frozen=True)
class CorrespondenceValidationCase:
    """One declared domain in a multi-context correspondence test suite."""

    name: str
    lower_model: ExecutableModel
    upper_model: ExecutableModel
    lower_context: Context
    upper_context: Optional[Context] = None
    horizon: int = 1
    role: CorrespondenceCaseRole = CorrespondenceCaseRole.CALIBRATION
    independent: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("correspondence validation case name must be non-empty")
        if self.horizon < 1:
            raise ValueError("correspondence validation horizon must be at least one")
        if self.independent and self.role != CorrespondenceCaseRole.HOLDOUT:
            raise ValueError("only a holdout case may claim independent provenance")

    @property
    def resolved_upper_context(self) -> Context:
        return self.upper_context or self.lower_context


@dataclass(frozen=True)
class CorrespondenceCaseResult:
    case_name: str
    role: CorrespondenceCaseRole
    independent: bool
    certificate: CorrespondenceCertificate

    def __post_init__(self) -> None:
        if not self.case_name:
            raise ValueError("correspondence case result name must be non-empty")
        if self.independent and self.role != CorrespondenceCaseRole.HOLDOUT:
            raise ValueError("only a holdout result may claim independent provenance")


@dataclass(frozen=True)
class CorrespondenceSuiteCertificate:
    """Cross-context evidence, with independent holdout status kept explicit."""

    correspondence_name: str
    lower_scale: str
    upper_scale: str
    cases: Tuple[CorrespondenceCaseResult, ...]
    simulations_used: int
    correspondence_fingerprint: str
    protocol_fingerprint: str
    truncated: bool = False
    boundaries: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.correspondence_name or not self.lower_scale or not self.upper_scale:
            raise ValueError("suite certificate identities must be non-empty")
        if self.simulations_used < 0:
            raise ValueError("suite simulations_used must be non-negative")
        validate_fingerprint(
            self.correspondence_fingerprint,
            purpose="suite correspondence fingerprint",
        )
        validate_fingerprint(
            self.protocol_fingerprint,
            purpose="suite protocol fingerprint",
        )
        names = [item.case_name for item in self.cases]
        if len(names) != len(set(names)):
            raise ValueError("suite certificate case names must be unique")
        for item in self.cases:
            metadata = (
                item.certificate.correspondence_name,
                item.certificate.lower_scale,
                item.certificate.upper_scale,
            )
            if metadata != (
                self.correspondence_name,
                self.lower_scale,
                self.upper_scale,
            ):
                raise ValueError("case certificate metadata does not match its suite")
            if (
                item.certificate.correspondence_fingerprint
                != self.correspondence_fingerprint
            ):
                raise ValueError(
                    "case certificate fingerprint does not match its suite"
                )
        if sum(item.certificate.simulations_used for item in self.cases) != (
            self.simulations_used
        ):
            raise ValueError("suite simulation count must equal its case certificates")

    @property
    def complete(self) -> bool:
        return bool(self.cases) and all(
            item.certificate.complete for item in self.cases
        )

    @property
    def commutes(self) -> bool:
        return bool(self.cases) and all(
            item.certificate.commutes for item in self.cases
        )

    @property
    def compatibility_passed(self) -> bool:
        return (
            not self.truncated
            and bool(self.cases)
            and all(item.certificate.passed for item in self.cases)
        )

    @property
    def has_independent_holdout(self) -> bool:
        return any(
            item.role == CorrespondenceCaseRole.HOLDOUT and item.independent
            for item in self.cases
        )

    @property
    def passed(self) -> bool:
        return self.has_independent_holdout and self.compatibility_passed

    def binds_correspondence(self, correspondence: Correspondence) -> bool:
        return (
            _correspondence_binding_error(
                correspondence,
                self.correspondence_fingerprint,
            )
            is None
        )


CorrespondenceEvidence = Union[
    CorrespondenceCertificate,
    CorrespondenceSuiteCertificate,
]


class CorrespondenceValidator:
    """Verify that projection commutes with lower- and upper-scale dynamics."""

    _COVERAGE_FAILURES = {
        "empty-lower-domain",
        "empty-upper-domain",
        "invalid-lower-trace",
        "invalid-upper-trace",
        "scenario-projection-failed",
        "missing-upper-scenario",
        "ambiguous-upper-scenario",
        "unmapped-upper-scenario",
    }

    def __init__(self, evaluator: Optional[SatisfactionEvaluator] = None) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()

    def validate(
        self,
        correspondence: Correspondence,
        lower_model: ExecutableModel,
        upper_model: ExecutableModel,
        lower_context: Context,
        upper_context: Optional[Context] = None,
        horizon: int = 1,
        budget: Optional[ResourceBudget] = None,
    ) -> CorrespondenceCertificate:
        """Check an entire commuting diagram under one shared simulation budget."""

        if horizon < 1:
            raise ValueError("correspondence horizon must be at least one")
        upper_context = upper_context or lower_context
        budget = budget or ResourceBudget()
        correspondence_digest = correspondence_fingerprint(correspondence)
        lower_context_digest = context_fingerprint(lower_context)
        upper_context_digest = context_fingerprint(upper_context)

        lower_batch = self.evaluator.collect(
            lower_model,
            lower_context,
            horizon,
            budget,
        )
        simulations_used = lower_batch.simulations_used
        remaining = max(0, budget.max_simulations - simulations_used)
        if remaining:
            upper_batch = self.evaluator.collect(
                upper_model,
                upper_context,
                horizon,
                replace(budget, max_simulations=remaining),
            )
            simulations_used += upper_batch.simulations_used
        else:
            upper_batch = self.evaluator.unstarted_batch(
                upper_model,
                upper_context,
                horizon,
                "upper-scale simulation was not started because the shared "
                "simulation budget was exhausted",
            )

        boundaries = list(
            "lower: %s" % item for item in lower_batch.boundaries
        ) + list("upper: %s" % item for item in upper_batch.boundaries)
        counterexamples = []

        lower_traces = []
        for index, trace in enumerate(lower_batch.traces):
            if isinstance(trace, Trace):
                lower_traces.append(trace)
            else:
                counterexamples.append(
                    CorrespondenceCounterexample(
                        "invalid-lower-trace",
                        "lower trace %d is not a Trace instance" % index,
                    )
                )

        upper_traces = []
        upper_by_key: Dict[ScenarioKey, list[Trace]] = {}
        for index, trace in enumerate(upper_batch.traces):
            if not isinstance(trace, Trace):
                counterexamples.append(
                    CorrespondenceCounterexample(
                        "invalid-upper-trace",
                        "upper trace %d is not a Trace instance" % index,
                    )
                )
                continue
            upper_traces.append(trace)
            upper_by_key.setdefault(trace.scenario_key, []).append(trace)

        if not lower_traces:
            counterexamples.append(
                CorrespondenceCounterexample(
                    "empty-lower-domain",
                    "a correspondence cannot be certified over an empty lower domain",
                )
            )
        if not upper_traces:
            counterexamples.append(
                CorrespondenceCounterexample(
                    "empty-upper-domain",
                    "a correspondence cannot be certified over an empty upper domain",
                )
            )

        paired_scenarios = 0
        mapped_upper = set()
        for lower_trace in lower_traces:
            lower_key = lower_trace.scenario_key
            try:
                upper_key = _audited_scenario_projection(
                    correspondence, lower_key
                )
            except Exception as error:
                counterexamples.append(
                    CorrespondenceCounterexample(
                        "scenario-projection-failed",
                        str(error),
                        lower_scenario=lower_key,
                    )
                )
                continue

            candidates = upper_by_key.get(upper_key, [])
            if not candidates:
                counterexamples.append(
                    CorrespondenceCounterexample(
                        "missing-upper-scenario",
                        "projected scenario is absent from the upper trace batch",
                        lower_scenario=lower_key,
                        upper_scenario=upper_key,
                    )
                )
                continue
            if len(candidates) != 1:
                counterexamples.append(
                    CorrespondenceCounterexample(
                        "ambiguous-upper-scenario",
                        "projected scenario has %d upper traces" % len(candidates),
                        lower_scenario=lower_key,
                        upper_scenario=upper_key,
                    )
                )
                continue

            upper_trace = candidates[0]
            paired_scenarios += 1
            mapped_upper.add(upper_key)
            for step, (lower_snapshot, upper_snapshot) in enumerate(
                zip(lower_trace.snapshots, upper_trace.snapshots)
            ):
                missing_lower = set(correspondence.lower_scale.observables) - set(
                    lower_snapshot
                )
                missing_upper = set(correspondence.upper_scale.observables) - set(
                    upper_snapshot
                )
                if missing_lower:
                    counterexamples.append(
                        CorrespondenceCounterexample(
                            "lower-interface-mismatch",
                            "lower snapshot is missing observables: %s"
                            % sorted(missing_lower),
                            lower_key,
                            upper_key,
                            step,
                            lower_snapshot=dict(lower_snapshot),
                            upper_snapshot=dict(upper_snapshot),
                        )
                    )
                    continue
                if missing_upper:
                    counterexamples.append(
                        CorrespondenceCounterexample(
                            "upper-interface-mismatch",
                            "upper snapshot is missing observables: %s"
                            % sorted(missing_upper),
                            lower_key,
                            upper_key,
                            step,
                            lower_snapshot=dict(lower_snapshot),
                            upper_snapshot=dict(upper_snapshot),
                        )
                    )
                    continue

                try:
                    projected = _audited_snapshot_projection(
                        correspondence,
                        lower_snapshot,
                        lower_context,
                    )
                    missing_projected = set(
                        correspondence.upper_scale.observables
                    ) - set(projected)
                    if missing_projected:
                        raise KeyError(
                            "projection is missing upper observables: %s"
                            % sorted(missing_projected)
                        )
                    equivalent = correspondence.upper_scale.equivalence.equivalent(
                        projected,
                        upper_snapshot,
                    )
                except Exception as error:
                    counterexamples.append(
                        CorrespondenceCounterexample(
                            "projection-failed",
                            str(error),
                            lower_key,
                            upper_key,
                            step,
                            lower_snapshot=dict(lower_snapshot),
                            upper_snapshot=dict(upper_snapshot),
                        )
                    )
                    continue

                if not equivalent:
                    counterexamples.append(
                        CorrespondenceCounterexample(
                            "non-commuting-step",
                            "projected lower state is not upper-scale equivalent",
                            lower_key,
                            upper_key,
                            step,
                            lower_snapshot=dict(lower_snapshot),
                            projected_snapshot=projected,
                            upper_snapshot=dict(upper_snapshot),
                        )
                    )

        for upper_key in sorted(set(upper_by_key) - mapped_upper):
            counterexamples.append(
                CorrespondenceCounterexample(
                    "unmapped-upper-scenario",
                    "upper scenario has no lower-scale preimage",
                    upper_scenario=upper_key,
                )
            )

        lower_model_digest, lower_fingerprint_error = (
            _safe_model_evidence_fingerprint(
                lower_model, lower_batch.traces, horizon
            )
        )
        upper_model_digest, upper_fingerprint_error = (
            _safe_model_evidence_fingerprint(
                upper_model, upper_batch.traces, horizon
            )
        )
        binding_complete = True
        for label, error in (
            ("lower", lower_fingerprint_error),
            ("upper", upper_fingerprint_error),
        ):
            if error is None:
                continue
            binding_complete = False
            boundaries.append(
                "%s model evidence could not be fingerprinted: %s"
                % (label, error)
            )

        binding_error = _correspondence_binding_error(
            correspondence,
            correspondence_digest,
        )
        if binding_error is not None:
            binding_complete = False
            counterexamples.append(
                CorrespondenceCounterexample(
                    "correspondence-identity-changed",
                    binding_error,
                )
            )

        protocol_digest = _protocol_fingerprint(
            correspondence_digest,
            lower_model_digest,
            upper_model_digest,
            lower_context_digest,
            upper_context_digest,
            horizon,
            lower_batch.coverage_authority,
            upper_batch.coverage_authority,
            budget,
            budget.max_simulations,
        )

        coverage_complete = not any(
            item.kind in self._COVERAGE_FAILURES for item in counterexamples
        )
        commutes = not any(
            item.kind not in self._COVERAGE_FAILURES for item in counterexamples
        )
        complete = (
            lower_batch.complete
            and upper_batch.complete
            and coverage_complete
            and binding_complete
        )
        return CorrespondenceCertificate(
            correspondence_name=correspondence.name,
            lower_scale=correspondence.lower_scale.name,
            upper_scale=correspondence.upper_scale.name,
            lower_model_name=str(getattr(lower_model, "name", "")),
            upper_model_name=str(getattr(upper_model, "name", "")),
            horizon=horizon,
            complete=complete,
            commutes=commutes,
            lower_scenarios=len(lower_traces),
            upper_scenarios=len(upper_traces),
            paired_scenarios=paired_scenarios,
            covered_upper_scenarios=len(set(upper_by_key).intersection(mapped_upper)),
            correspondence_fingerprint=correspondence_digest,
            lower_model_fingerprint=lower_model_digest,
            upper_model_fingerprint=upper_model_digest,
            protocol_fingerprint=protocol_digest,
            lower_context_fingerprint=lower_context_digest,
            upper_context_fingerprint=upper_context_digest,
            counterexamples=tuple(counterexamples),
            assumptions=correspondence.assumptions,
            boundaries=tuple(boundaries),
            lower_coverage_authority=lower_batch.coverage_authority,
            upper_coverage_authority=upper_batch.coverage_authority,
            simulations_used=simulations_used,
        )

    def validate_suite(
        self,
        correspondence: Correspondence,
        cases: Iterable[CorrespondenceValidationCase],
        budget: Optional[ResourceBudget] = None,
    ) -> CorrespondenceSuiteCertificate:
        """Validate calibration and holdout cases under one shared budget."""

        cases = tuple(cases)
        if not cases:
            raise ValueError("a correspondence validation suite cannot be empty")
        names = [item.name for item in cases]
        if len(names) != len(set(names)):
            raise ValueError("correspondence validation case names must be unique")

        budget = budget or ResourceBudget()
        correspondence_digest = correspondence_fingerprint(correspondence)
        remaining = budget.max_simulations
        simulations_used = 0
        truncated = False
        suite_boundaries = []
        results = []
        for case in cases:
            binding_error = _correspondence_binding_error(
                correspondence,
                correspondence_digest,
            )
            if binding_error is not None:
                truncated = True
                suite_boundaries.append(binding_error)
                break
            upper_context = case.resolved_upper_context
            if remaining <= 0:
                lower_context_digest = context_fingerprint(case.lower_context)
                upper_context_digest = context_fingerprint(upper_context)
                lower_model_digest, lower_error = (
                    _safe_model_evidence_fingerprint(
                        case.lower_model, (), case.horizon
                    )
                )
                upper_model_digest, upper_error = (
                    _safe_model_evidence_fingerprint(
                        case.upper_model, (), case.horizon
                    )
                )
                exhausted_boundaries = [
                    "validation case was not started because the suite simulation "
                    "budget was exhausted"
                ]
                for label, error in (
                    ("lower", lower_error),
                    ("upper", upper_error),
                ):
                    if error is not None:
                        exhausted_boundaries.append(
                            "%s model evidence could not be fingerprinted: %s"
                            % (label, error)
                        )
                protocol_digest = _protocol_fingerprint(
                    correspondence_digest,
                    lower_model_digest,
                    upper_model_digest,
                    lower_context_digest,
                    upper_context_digest,
                    case.horizon,
                    "none",
                    "none",
                    budget,
                    0,
                )
                certificate = CorrespondenceCertificate(
                    correspondence_name=correspondence.name,
                    lower_scale=correspondence.lower_scale.name,
                    upper_scale=correspondence.upper_scale.name,
                    lower_model_name=str(getattr(case.lower_model, "name", "")),
                    upper_model_name=str(getattr(case.upper_model, "name", "")),
                    horizon=case.horizon,
                    complete=False,
                    commutes=True,
                    lower_scenarios=0,
                    upper_scenarios=0,
                    paired_scenarios=0,
                    covered_upper_scenarios=0,
                    correspondence_fingerprint=correspondence_digest,
                    lower_model_fingerprint=lower_model_digest,
                    upper_model_fingerprint=upper_model_digest,
                    protocol_fingerprint=protocol_digest,
                    lower_context_fingerprint=lower_context_digest,
                    upper_context_fingerprint=upper_context_digest,
                    assumptions=correspondence.assumptions,
                    boundaries=tuple(exhausted_boundaries),
                )
                truncated = True
            else:
                certificate = self.validate(
                    correspondence,
                    case.lower_model,
                    case.upper_model,
                    case.lower_context,
                    upper_context,
                    case.horizon,
                    replace(budget, max_simulations=remaining),
                )
                remaining -= certificate.simulations_used
                if not certificate.complete and any(
                    "budget" in boundary.lower()
                    for boundary in certificate.boundaries
                ):
                    truncated = True
            if certificate.correspondence_fingerprint != correspondence_digest:
                truncated = True
                suite_boundaries.append(
                    "correspondence identity changed before a case could be bound "
                    "to the suite"
                )
                break
            simulations_used += certificate.simulations_used
            results.append(
                CorrespondenceCaseResult(
                    case.name,
                    case.role,
                    case.independent,
                    certificate,
                )
            )

        binding_error = _correspondence_binding_error(
            correspondence,
            correspondence_digest,
        )
        if binding_error is not None:
            truncated = True
            if binding_error not in suite_boundaries:
                suite_boundaries.append(binding_error)

        suite_protocol_digest = fingerprint_value(
            (
                "correspondence-suite-validator-v1",
                correspondence_digest,
                tuple(
                    (
                        item.case_name,
                        item.role.value,
                        item.independent,
                        item.certificate.protocol_fingerprint,
                    )
                    for item in results
                ),
                budget.max_candidates,
                budget.max_simulations,
                budget.max_cost,
                truncated,
            ),
            purpose="correspondence suite protocol fingerprint",
        )

        return CorrespondenceSuiteCertificate(
            correspondence_name=correspondence.name,
            lower_scale=correspondence.lower_scale.name,
            upper_scale=correspondence.upper_scale.name,
            cases=tuple(results),
            simulations_used=simulations_used,
            correspondence_fingerprint=correspondence_digest,
            protocol_fingerprint=suite_protocol_digest,
            truncated=truncated,
            boundaries=tuple(suite_boundaries),
        )


@dataclass(frozen=True)
class ScalePath:
    """A path of individually certified edges, not an inferred composite proof."""

    scales: Tuple[str, ...]
    correspondences: Tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.scales) != len(self.correspondences) + 1:
            raise ValueError("a scale path needs one more node than edge")

    @property
    def edgewise_certified(self) -> bool:
        return True

    @property
    def end_to_end_certified(self) -> bool:
        # Only a directly validated edge is an end-to-end certificate.  Local
        # edge proofs are not silently composed across changed domains/models.
        return len(self.correspondences) == 1


class ScaleGraph:
    """Registry containing only directly validated correspondence edges."""

    def __init__(self) -> None:
        self._scales: Dict[str, Scale] = {}
        self._correspondences: Dict[str, Correspondence] = {}
        self._certificates: Dict[str, CorrespondenceEvidence] = {}

    @property
    def scales(self) -> Tuple[Scale, ...]:
        return tuple(self._scales.values())

    @property
    def correspondences(self) -> Tuple[Correspondence, ...]:
        return tuple(self._correspondences.values())

    def add_verified(
        self,
        correspondence: Correspondence,
        certificate: CorrespondenceEvidence,
    ) -> None:
        if not certificate.passed:
            raise ValueError("only passed correspondence certificates may enter the graph")
        metadata = (
            certificate.correspondence_name,
            certificate.lower_scale,
            certificate.upper_scale,
        )
        expected = (
            correspondence.name,
            correspondence.lower_scale.name,
            correspondence.upper_scale.name,
        )
        if metadata != expected:
            raise ValueError("certificate metadata does not match the correspondence")
        if not certificate.binds_correspondence(correspondence):
            raise ValueError(
                "certificate fingerprint does not match the correspondence"
            )

        for scale in (correspondence.lower_scale, correspondence.upper_scale):
            existing = self._scales.get(scale.name)
            if existing is not None and existing != scale:
                raise ValueError("scale %r has conflicting definitions" % scale.name)
            self._scales[scale.name] = scale

        existing_correspondence = self._correspondences.get(correspondence.name)
        existing_certificate = self._certificates.get(correspondence.name)
        if existing_correspondence is not None and existing_correspondence != correspondence:
            raise ValueError(
                "correspondence %r already has a different certified edge"
                % correspondence.name
            )
        if existing_certificate is not None and existing_certificate != certificate:
            if isinstance(
                existing_certificate,
                CorrespondenceSuiteCertificate,
            ) and isinstance(certificate, CorrespondenceCertificate):
                # Never replace stronger cross-context evidence with one case.
                return
            if not (
                isinstance(existing_certificate, CorrespondenceCertificate)
                and isinstance(certificate, CorrespondenceSuiteCertificate)
            ):
                raise ValueError(
                    "correspondence %r already has different certified evidence"
                    % correspondence.name
                )
        self._correspondences[correspondence.name] = correspondence
        self._certificates[correspondence.name] = certificate

    def certificate(self, correspondence_name: str) -> CorrespondenceEvidence:
        return self._certificates[correspondence_name]

    def has_certified_direct(self, lower_scale: str, upper_scale: str) -> bool:
        return any(
            item.lower_scale.name == lower_scale
            and item.upper_scale.name == upper_scale
            for item in self._correspondences.values()
        )

    def find_paths(
        self,
        lower_scale: str,
        upper_scale: str,
        max_hops: int = 8,
    ) -> Tuple[ScalePath, ...]:
        if max_hops < 1:
            raise ValueError("max_hops must be positive")
        if lower_scale not in self._scales:
            raise KeyError(lower_scale)
        if upper_scale not in self._scales:
            raise KeyError(upper_scale)

        outgoing: Dict[str, list[Correspondence]] = {}
        for item in self._correspondences.values():
            outgoing.setdefault(item.lower_scale.name, []).append(item)
        for items in outgoing.values():
            items.sort(key=lambda item: item.name)

        paths = []

        def visit(
            current: str,
            scale_names: Tuple[str, ...],
            edge_names: Tuple[str, ...],
        ) -> None:
            if current == upper_scale and edge_names:
                paths.append(ScalePath(scale_names, edge_names))
                return
            if len(edge_names) >= max_hops:
                return
            for edge in outgoing.get(current, []):
                next_scale = edge.upper_scale.name
                if next_scale in scale_names:
                    continue
                visit(
                    next_scale,
                    scale_names + (next_scale,),
                    edge_names + (edge.name,),
                )

        visit(lower_scale, (lower_scale,), ())
        return tuple(
            sorted(paths, key=lambda item: (len(item.correspondences), item.correspondences))
        )
