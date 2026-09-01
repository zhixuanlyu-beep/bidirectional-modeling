"""Explicit, verifiable correspondences between adjacent modeling scales.

A correspondence is a caller-owned claim that projecting a lower-scale trace
produces the same task-relevant history as an upper-scale executable model.
The validator checks that claim over independently certified scenario domains.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple, Union

from .core import (
    Context,
    EquivalenceSpec,
    ExecutableModel,
    ResourceBudget,
    ScenarioKey,
    Snapshot,
    Trace,
)
from .evaluation import SatisfactionEvaluator, TraceBatch


SnapshotProjection = Callable[[Snapshot, Context], Mapping[str, Any]]
ScenarioProjection = Callable[[ScenarioKey], ScenarioKey]


def _identity_scenario(key: ScenarioKey) -> ScenarioKey:
    return key


def context_fingerprint(context: Context) -> str:
    """Stable digest for the declared context and scenario domain."""

    payload = repr(context.semantic_signature()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class Scale:
    """Observable interface and equivalence relation at one modeling scale."""

    name: str
    observables: Tuple[str, ...]
    equivalence: EquivalenceSpec

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("scale name must be non-empty")
        if not self.observables:
            raise ValueError("a scale must declare at least one observable")
        if any(not item for item in self.observables):
            raise ValueError("scale observable names must be non-empty")
        if len(self.observables) != len(set(self.observables)):
            raise ValueError("scale observables must be unique")
        missing = set(self.equivalence.fields) - set(self.observables)
        if missing:
            raise ValueError(
                "scale equivalence fields must be declared observables: %s"
                % sorted(missing)
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

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("correspondence name must be non-empty")
        if self.lower_scale.name == self.upper_scale.name:
            raise ValueError("correspondence endpoints must be distinct scales")
        if not callable(self.projection):
            raise TypeError("correspondence projection must be callable")
        if not callable(self.scenario_projection):
            raise TypeError("scenario projection must be callable")


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
    counterexamples: Tuple[CorrespondenceCounterexample, ...] = ()
    assumptions: Tuple[str, ...] = ()
    boundaries: Tuple[str, ...] = ()
    lower_coverage_authority: str = "none"
    upper_coverage_authority: str = "none"
    lower_context_fingerprint: str = ""
    upper_context_fingerprint: str = ""
    simulations_used: int = 0

    def __post_init__(self) -> None:
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
        for fingerprint in (
            self.lower_context_fingerprint,
            self.upper_context_fingerprint,
        ):
            if not fingerprint:
                continue
            if len(fingerprint) != 64:
                raise ValueError("context fingerprints must be SHA-256 digests")
            try:
                int(fingerprint, 16)
            except ValueError as error:
                raise ValueError(
                    "context fingerprints must be hexadecimal"
                ) from error

    @property
    def passed(self) -> bool:
        return self.complete and self.commutes


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
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.correspondence_name or not self.lower_scale or not self.upper_scale:
            raise ValueError("suite certificate identities must be non-empty")
        if self.simulations_used < 0:
            raise ValueError("suite simulations_used must be non-negative")
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
            upper_batch = TraceBatch(
                (),
                False,
                0.0,
                (
                    "upper-scale simulation was not started because the shared "
                    "simulation budget was exhausted",
                ),
                "none",
            )

        boundaries = tuple(
            "lower: %s" % item for item in lower_batch.boundaries
        ) + tuple("upper: %s" % item for item in upper_batch.boundaries)
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
                upper_key = correspondence.scenario_projection(lower_key)
                if not isinstance(upper_key, ScenarioKey):
                    raise TypeError("scenario projection did not return ScenarioKey")
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
                    projected = correspondence.projection(
                        lower_snapshot,
                        lower_context,
                    )
                    if not isinstance(projected, Mapping):
                        raise TypeError("snapshot projection did not return a mapping")
                    projected = dict(projected)
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
            counterexamples=tuple(counterexamples),
            assumptions=correspondence.assumptions,
            boundaries=boundaries,
            lower_coverage_authority=lower_batch.coverage_authority,
            upper_coverage_authority=upper_batch.coverage_authority,
            lower_context_fingerprint=context_fingerprint(lower_context),
            upper_context_fingerprint=context_fingerprint(upper_context),
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
        remaining = budget.max_simulations
        simulations_used = 0
        truncated = False
        results = []
        for case in cases:
            upper_context = case.resolved_upper_context
            if remaining <= 0:
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
                    assumptions=correspondence.assumptions,
                    boundaries=(
                        "validation case was not started because the suite simulation "
                        "budget was exhausted",
                    ),
                    lower_context_fingerprint=context_fingerprint(case.lower_context),
                    upper_context_fingerprint=context_fingerprint(upper_context),
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
                simulations_used += certificate.simulations_used
                remaining -= certificate.simulations_used
                if not certificate.complete and any(
                    "budget" in boundary.lower()
                    for boundary in certificate.boundaries
                ):
                    truncated = True
            results.append(
                CorrespondenceCaseResult(
                    case.name,
                    case.role,
                    case.independent,
                    certificate,
                )
            )

        return CorrespondenceSuiteCertificate(
            correspondence_name=correspondence.name,
            lower_scale=correspondence.lower_scale.name,
            upper_scale=correspondence.upper_scale.name,
            cases=tuple(results),
            simulations_used=simulations_used,
            truncated=truncated,
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
