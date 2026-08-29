"""Core, domain-neutral types for bidirectional modeling.

The package deliberately treats purpose as contextual evidence, not as an
intrinsic property of a structure.  Domain adapters only need to expose
observable traces and a few transparent resource metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple


Snapshot = Mapping[str, Any]
State = Mapping[str, Any]


class PurposeLevel(str, Enum):
    EFFECT = "effect"
    FUNCTION = "function"
    INTENTION = "intention"


class RequirementCategory(str, Enum):
    OBJECTIVE = "objective"
    INVARIANT = "invariant"
    CONSTRAINT = "constraint"


class Aggregation(str, Enum):
    FINAL = "final"
    INITIAL = "initial"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    DELTA = "delta"
    EACH = "each"


@dataclass(frozen=True)
class Evidence:
    """A contextual observation supporting or opposing a purpose hypothesis."""

    statement: str
    hypothesis: str
    strength: float
    kind: str = "observation"
    source: str = "unspecified"

    def __post_init__(self) -> None:
        if not -1.0 <= self.strength <= 1.0:
            raise ValueError("evidence strength must be in [-1, 1]")


@dataclass(frozen=True)
class Intervention:
    name: str
    actions: Tuple[str, ...] = ()
    repeat_last: bool = True

    def action_at(self, step: int) -> str:
        if not self.actions:
            return "noop"
        if step < len(self.actions):
            return self.actions[step]
        return self.actions[-1] if self.repeat_last else "noop"


@dataclass(frozen=True)
class ResourceBudget:
    max_candidates: int = 100
    max_simulations: int = 10_000
    max_cost: float = math.inf

    def __post_init__(self) -> None:
        if self.max_candidates <= 0 or self.max_simulations <= 0:
            raise ValueError("budget limits must be positive")


@dataclass(frozen=True)
class Context:
    environment: Mapping[str, Any] = field(default_factory=dict)
    scale: str = "task"
    history: Tuple[Evidence, ...] = ()
    observer: str = "human"
    interventions: Tuple[Intervention, ...] = ()
    assumptions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelMetrics:
    cost: float
    complexity: float
    risk: float

    def __post_init__(self) -> None:
        if min(self.cost, self.complexity, self.risk) < 0:
            raise ValueError("model metrics must be non-negative")

    def as_tuple(self) -> Tuple[float, float, float]:
        return self.cost, self.complexity, self.risk


@dataclass(frozen=True)
class Trace:
    model_name: str
    initial_state: str
    intervention: str
    snapshots: Tuple[Snapshot, ...]

    def values(self, field_name: str) -> Tuple[Any, ...]:
        missing = [index for index, item in enumerate(self.snapshots) if field_name not in item]
        if missing:
            raise KeyError("observable %r missing at steps %s" % (field_name, missing))
        return tuple(item[field_name] for item in self.snapshots)


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: RequirementCategory
    passed: bool
    observed: Any
    expected: str
    robustness: float
    detail: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.robustness <= 1.0:
            raise ValueError("robustness must be in [0, 1]")


class Requirement(Protocol):
    name: str
    category: RequirementCategory

    def evaluate(
        self,
        model: "ExecutableModel",
        traces: Sequence[Trace],
        context: Context,
    ) -> CheckResult:
        ...


def _compare(observed: Any, operator: str, expected: Any, tolerance: float) -> Tuple[bool, float]:
    if operator == "eq":
        if isinstance(observed, (int, float)) and isinstance(expected, (int, float)):
            difference = abs(float(observed) - float(expected))
            passed = difference <= tolerance
            if tolerance > 0:
                robustness = max(0.0, 1.0 - difference / tolerance)
            else:
                robustness = 1.0 if passed else 0.0
            return passed, robustness
        passed = observed == expected
        return passed, 1.0 if passed else 0.0
    if operator == "ne":
        passed = observed != expected
        return passed, 1.0 if passed else 0.0
    if operator in {"le", "lt", "ge", "gt"}:
        left, right = float(observed), float(expected)
        if operator == "le":
            passed, margin = left <= right + tolerance, right + tolerance - left
        elif operator == "lt":
            passed, margin = left < right + tolerance, right + tolerance - left
        elif operator == "ge":
            passed, margin = left + tolerance >= right, left + tolerance - right
        else:
            passed, margin = left + tolerance > right, left + tolerance - right
        scale = max(abs(right), tolerance, 1.0)
        # Passing exactly at the boundary is valid but fragile (0.5), while a
        # comfortable margin approaches 1.0.  Failed checks remain zero.
        score = 0.5 + 0.5 * max(0.0, margin) / scale
        return passed, min(1.0, score) if passed else 0.0
    if operator == "in":
        passed = observed in expected
        return passed, 1.0 if passed else 0.0
    raise ValueError("unsupported operator %r" % operator)


@dataclass(frozen=True)
class FieldRequirement:
    name: str
    field_name: str
    operator: str
    expected: Any
    category: RequirementCategory = RequirementCategory.OBJECTIVE
    aggregation: Aggregation = Aggregation.FINAL
    tolerance: float = 0.0

    def _aggregate(self, values: Sequence[Any]) -> Any:
        if self.aggregation == Aggregation.FINAL:
            return values[-1]
        if self.aggregation == Aggregation.INITIAL:
            return values[0]
        if self.aggregation == Aggregation.MIN:
            return min(values)
        if self.aggregation == Aggregation.MAX:
            return max(values)
        if self.aggregation == Aggregation.MEAN:
            return mean(values)
        if self.aggregation == Aggregation.DELTA:
            return values[-1] - values[0]
        return tuple(values)

    def evaluate(
        self,
        model: "ExecutableModel",
        traces: Sequence[Trace],
        context: Context,
    ) -> CheckResult:
        outcomes = []
        robustness = []
        failures = []
        for trace in traces:
            values = trace.values(self.field_name)
            if self.aggregation == Aggregation.EACH:
                checks = [_compare(value, self.operator, self.expected, self.tolerance) for value in values]
                passed = all(item[0] for item in checks)
                score = min(item[1] for item in checks)
                observed = tuple(values)
            else:
                observed = self._aggregate(values)
                passed, score = _compare(observed, self.operator, self.expected, self.tolerance)
            outcomes.append(observed)
            robustness.append(score)
            if not passed:
                failures.append("%s/%s" % (trace.initial_state, trace.intervention))
        passed_all = bool(traces) and not failures
        detail = "failed scenarios: %s" % ", ".join(failures) if failures else "%d scenarios checked" % len(traces)
        return CheckResult(
            name=self.name,
            category=self.category,
            passed=passed_all,
            observed=tuple(outcomes),
            expected="%s %r after %s" % (self.operator, self.expected, self.aggregation.value),
            robustness=min(robustness) if robustness else 0.0,
            detail=detail,
        )


@dataclass(frozen=True)
class ModelMetricRequirement:
    name: str
    metric: str
    operator: str
    expected: float
    category: RequirementCategory = RequirementCategory.CONSTRAINT
    tolerance: float = 0.0

    def evaluate(
        self,
        model: "ExecutableModel",
        traces: Sequence[Trace],
        context: Context,
    ) -> CheckResult:
        observed = getattr(model.metrics, self.metric)
        passed, robustness = _compare(observed, self.operator, self.expected, self.tolerance)
        return CheckResult(
            name=self.name,
            category=self.category,
            passed=passed,
            observed=observed,
            expected="%s %s" % (self.operator, self.expected),
            robustness=robustness,
            detail="model metric %s" % self.metric,
        )


@dataclass(frozen=True)
class CustomRequirement:
    name: str
    category: RequirementCategory
    checker: Callable[["ExecutableModel", Sequence[Trace], Context], CheckResult]

    def evaluate(
        self,
        model: "ExecutableModel",
        traces: Sequence[Trace],
        context: Context,
    ) -> CheckResult:
        result = self.checker(model, traces, context)
        if result.name != self.name or result.category != self.category:
            raise ValueError("custom checker must preserve requirement name and category")
        return result


@dataclass(frozen=True)
class EquivalenceSpec:
    fields: Tuple[str, ...]
    tolerances: Mapping[str, float] = field(default_factory=dict)

    def equivalent(self, left: Snapshot, right: Snapshot) -> bool:
        for field_name in self.fields:
            tolerance = self.tolerances.get(field_name, 0.0)
            passed, _ = _compare(left[field_name], "eq", right[field_name], tolerance)
            if not passed:
                return False
        return True

    def signature(self, snapshot: Snapshot) -> Tuple[Any, ...]:
        result = []
        for field_name in self.fields:
            value = snapshot[field_name]
            tolerance = self.tolerances.get(field_name, 0.0)
            if tolerance and isinstance(value, (int, float)):
                value = round(float(value) / tolerance)
            result.append(value)
        return tuple(result)


@dataclass(frozen=True)
class MacroSpec:
    name: str
    observables: Tuple[str, ...]
    objectives: Tuple[Requirement, ...]
    equivalence: EquivalenceSpec
    invariants: Tuple[Requirement, ...] = ()
    constraints: Tuple[Requirement, ...] = ()
    tolerance: float = 0.0
    horizon: int = 1
    ambiguous_terms: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    assumptions: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.horizon < 1:
            raise ValueError("horizon must be at least one")
        missing = set(self.equivalence.fields) - set(self.observables)
        if missing:
            raise ValueError("equivalence fields must be declared observables: %s" % sorted(missing))

    @property
    def requirements(self) -> Tuple[Requirement, ...]:
        return self.objectives + self.invariants + self.constraints


class ExecutableModel(Protocol):
    name: str
    metrics: ModelMetrics
    assumptions: Tuple[str, ...]
    failure_boundaries: Tuple[str, ...]
    prior_reliability: float
    capabilities: Tuple[str, ...]

    def simulate(self, context: Context, horizon: int) -> Sequence[Trace]:
        ...


Transition = Callable[[State, str, Context], State]
Readout = Callable[[State, Context], Snapshot]


@dataclass
class FiniteStateModel:
    name: str
    states: Mapping[str, State]
    initial_states: Tuple[str, ...]
    actions: Tuple[str, ...]
    transition: Transition
    readout: Readout
    metrics: ModelMetrics
    assumptions: Tuple[str, ...] = ()
    failure_boundaries: Tuple[str, ...] = ()
    prior_reliability: float = 0.8
    capabilities: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = set(self.initial_states) - set(self.states)
        if unknown:
            raise ValueError("unknown initial states: %s" % sorted(unknown))
        if not 0.0 <= self.prior_reliability <= 1.0:
            raise ValueError("prior reliability must be in [0, 1]")

    def step(self, state: State, action: str, context: Context) -> State:
        return self.transition(dict(state), action, context)

    def observe(self, state: State, context: Context) -> Snapshot:
        return dict(self.readout(state, context))

    def simulate(self, context: Context, horizon: int) -> Sequence[Trace]:
        interventions = context.interventions or (Intervention("baseline"),)
        traces = []
        for initial_name in self.initial_states:
            for intervention in interventions:
                state = dict(self.states[initial_name])
                snapshots = [self.observe(state, context)]
                for step in range(horizon):
                    state = dict(self.step(state, intervention.action_at(step), context))
                    snapshots.append(self.observe(state, context))
                traces.append(
                    Trace(
                        model_name=self.name,
                        initial_state=initial_name,
                        intervention=intervention.name,
                        snapshots=tuple(snapshots),
                    )
                )
        return tuple(traces)


@dataclass(frozen=True)
class ConfidenceBreakdown:
    coverage: float
    robustness: float
    assumption_reliability: float

    def __post_init__(self) -> None:
        for value in (self.coverage, self.robustness, self.assumption_reliability):
            if not 0.0 <= value <= 1.0:
                raise ValueError("confidence components must be in [0, 1]")

    @property
    def value(self) -> float:
        product = self.coverage * self.robustness * self.assumption_reliability
        return product ** (1.0 / 3.0) if product else 0.0


@dataclass(frozen=True)
class SatisfactionCertificate:
    spec_name: str
    model_name: str
    satisfied: bool
    checks: Tuple[CheckResult, ...]
    verified_scenarios: int
    confidence: ConfidenceBreakdown
    assumptions: Tuple[str, ...]
    failure_boundaries: Tuple[str, ...]


@dataclass(frozen=True)
class Counterexample:
    kind: str
    summary: str
    witness: Mapping[str, Any]
    violated: Tuple[str, ...] = ()
    suggested_refinements: Tuple[str, ...] = ()
    blocking: bool = True


@dataclass(frozen=True)
class CandidateEvaluation:
    model: ExecutableModel
    certificate: SatisfactionCertificate
    counterexamples: Tuple[Counterexample, ...] = ()

    @property
    def confidence(self) -> float:
        return self.certificate.confidence.value


@dataclass(frozen=True)
class RealizationResult:
    spec: MacroSpec
    candidates: Tuple[CandidateEvaluation, ...]
    rejected: Tuple[CandidateEvaluation, ...]
    dominated: Tuple[CandidateEvaluation, ...]
    searched_candidates: int
    truncated: bool


@dataclass(frozen=True)
class PurposeHypothesis:
    name: str
    level: PurposeLevel
    spec: MacroSpec
    prior: float = 0.5
    explanation: str = ""
    predictions: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.prior <= 1.0:
            raise ValueError("hypothesis prior must be in [0, 1]")
        for probability in self.predictions.values():
            if not 0.0 <= probability <= 1.0:
                raise ValueError("prediction probabilities must be in [0, 1]")


@dataclass(frozen=True)
class Experiment:
    name: str
    question: str
    cost: float = 0.0


@dataclass(frozen=True)
class DiscriminatingQuery:
    experiment: Experiment
    candidate_names: Tuple[str, ...]
    predictions: Mapping[str, float]
    expected_information_gain: float


@dataclass(frozen=True)
class InterpretationCandidate:
    hypothesis: PurposeHypothesis
    certificate: SatisfactionCertificate
    fit: float
    simplicity: float
    robustness: float
    context_support: float
    confidence: float
    evidence: Tuple[Evidence, ...]
    caveats: Tuple[str, ...] = ()


@dataclass(frozen=True)
class InterpretationResult:
    model_name: str
    candidates: Tuple[InterpretationCandidate, ...]
    equivalent_explanations: Tuple[Tuple[str, ...], ...]
    discriminating_query: Optional[DiscriminatingQuery]
    non_identifiable: bool


@dataclass(frozen=True)
class ClosureReport:
    closed: bool
    checked_pairs: int
    counterexamples: Tuple[Counterexample, ...]


@dataclass(frozen=True)
class Concept:
    name: str
    definition: str
    positive_examples: Tuple[str, ...] = ()
    negative_examples: Tuple[str, ...] = ()
    invariants: Tuple[str, ...] = ()
    boundaries: Tuple[str, ...] = ()
    related_concepts: Tuple[str, ...] = ()
    candidate_definitions: Tuple[str, ...] = ()
    version: int = 1


def normalized_entropy(probabilities: Iterable[float]) -> float:
    values = [max(0.0, value) for value in probabilities]
    total = sum(values)
    if total <= 0 or len(values) <= 1:
        return 0.0
    normalized = [value / total for value in values if value > 0]
    entropy = -sum(value * math.log(value, 2) for value in normalized)
    return entropy / math.log(len(values), 2)
