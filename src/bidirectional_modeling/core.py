"""Core, domain-neutral types for bidirectional modeling.

The package deliberately treats purpose as contextual evidence, not as an
intrinsic property of a structure.  Domain adapters only need to expose
observable traces and a few transparent resource metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum
from statistics import mean
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Protocol, Sequence, Tuple


Snapshot = Mapping[str, Any]
State = Mapping[str, Any]


class UndefinedTransition(LookupError):
    """A declared action has no result on the current semantic support."""


def _freeze_context_value(value: Any) -> Any:
    """Canonicalize context data or reject values with unstable identities."""

    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value.hex())
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value.hex())
    if isinstance(value, Enum):
        return (
            "enum",
            type(value).__module__,
            type(value).__qualname__,
            _freeze_context_value(value.value),
        )
    if isinstance(value, Mapping):
        items = (
            (_freeze_context_value(key), _freeze_context_value(item))
            for key, item in value.items()
        )
        return ("mapping", tuple(sorted(items, key=repr)))
    if isinstance(value, (list, tuple)):
        return (
            type(value).__name__,
            tuple(_freeze_context_value(item) for item in value),
        )
    if isinstance(value, (set, frozenset)):
        items = (_freeze_context_value(item) for item in value)
        return (type(value).__name__, tuple(sorted(items, key=repr)))
    raise TypeError(
        "context fingerprints require canonical primitive/container values; got %s.%s"
        % (type(value).__module__, type(value).__qualname__)
    )


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


@dataclass(frozen=True, order=True)
class ScenarioKey:
    """Caller-visible identity for one initial-state/intervention scenario."""

    initial_state: str
    intervention: str

    def __post_init__(self) -> None:
        if not self.initial_state or not self.intervention:
            raise ValueError("scenario identities must be non-empty")


@dataclass(frozen=True)
class ResourceBudget:
    max_candidates: int = 100
    max_simulations: int = 10_000
    max_cost: float = math.inf

    def __post_init__(self) -> None:
        if self.max_candidates <= 0 or self.max_simulations <= 0:
            raise ValueError("budget limits must be positive")
        if math.isnan(float(self.max_cost)) or self.max_cost < 0:
            raise ValueError("max_cost must be non-negative")


@dataclass(frozen=True)
class Context:
    environment: Mapping[str, Any] = field(default_factory=dict)
    scale: str = "task"
    history: Tuple[Evidence, ...] = ()
    observer: str = "human"
    interventions: Tuple[Intervention, ...] = ()
    assumptions: Tuple[str, ...] = ()
    include_baseline: bool = True
    scenario_manifest: Tuple[ScenarioKey, ...] = ()

    def __post_init__(self) -> None:
        names = [intervention.name for intervention in self.interventions]
        if len(names) != len(set(names)):
            raise ValueError("intervention names must be unique")
        if any(not isinstance(item, ScenarioKey) for item in self.scenario_manifest):
            raise TypeError("scenario manifest entries must be ScenarioKey instances")
        if len(self.scenario_manifest) != len(set(self.scenario_manifest)):
            raise ValueError("scenario manifest entries must be unique")

    def semantic_signature(self) -> Tuple[Any, ...]:
        """Canonical identity for the assumptions and domain of one evaluation."""

        history = tuple(
            sorted(
                (
                    item.statement,
                    item.hypothesis,
                    item.strength,
                    item.kind,
                    item.source,
                )
                for item in self.history
            )
        )
        interventions = tuple(
            sorted(
                (
                    item.name,
                    tuple(item.actions),
                    item.repeat_last,
                )
                for item in self.interventions
            )
        )
        scenarios = tuple(
            sorted(
                (item.initial_state, item.intervention)
                for item in self.scenario_manifest
            )
        )
        return (
            _freeze_context_value(self.environment),
            self.scale,
            history,
            self.observer,
            interventions,
            tuple(sorted(self.assumptions)),
            self.include_baseline,
            scenarios,
        )


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

    def __post_init__(self) -> None:
        if not self.model_name:
            raise ValueError("trace model name must be non-empty")
        ScenarioKey(self.initial_state, self.intervention)

    @property
    def scenario_key(self) -> ScenarioKey:
        return ScenarioKey(self.initial_state, self.intervention)

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

    def __post_init__(self) -> None:
        if math.isnan(float(self.tolerance)) or self.tolerance < 0:
            raise ValueError("requirement tolerance must be non-negative")

    def semantic_signature(self) -> Tuple[Any, ...]:
        return (
            "field",
            self.category.value,
            self.field_name,
            self.operator,
            _freeze_value(self.expected),
            self.aggregation.value,
            self.tolerance,
        )

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

    def __post_init__(self) -> None:
        if math.isnan(float(self.tolerance)) or self.tolerance < 0:
            raise ValueError("requirement tolerance must be non-negative")

    def semantic_signature(self) -> Tuple[Any, ...]:
        return (
            "model-metric",
            self.category.value,
            self.metric,
            self.operator,
            self.expected,
            self.tolerance,
        )

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
    semantic_id: Optional[str] = None

    def semantic_signature(self) -> Tuple[Any, ...]:
        # A caller-supplied stable ID allows independently constructed custom
        # requirements to participate in strict semantic round trips.  Without
        # one, equality is deliberately limited to the same callable object.
        identity: Any = self.semantic_id
        if identity is None:
            identity = (
                getattr(self.checker, "__module__", ""),
                getattr(self.checker, "__qualname__", ""),
                id(self.checker),
            )
        return ("custom", self.category.value, identity)

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

    def __post_init__(self) -> None:
        unknown = set(self.tolerances) - set(self.fields)
        if unknown:
            raise ValueError("tolerances reference undeclared fields: %s" % sorted(unknown))
        invalid = {key: value for key, value in self.tolerances.items() if value <= 0}
        if invalid:
            raise ValueError("equivalence resolutions must be positive: %s" % invalid)

    def equivalent(self, left: Snapshot, right: Snapshot) -> bool:
        # Equivalence must be transitive.  Numeric resolutions therefore form
        # deterministic buckets instead of using pairwise |a-b| <= epsilon,
        # which is not an equivalence relation.
        return self.signature(left) == self.signature(right)

    def signature(self, snapshot: Snapshot) -> Tuple[Any, ...]:
        result = []
        for field_name in self.fields:
            value = snapshot[field_name]
            tolerance = self.tolerances.get(field_name, 0.0)
            if tolerance and isinstance(value, (int, float)):
                value = math.floor(float(value) / tolerance + 0.5)
            result.append(value)
        return tuple(result)

    def semantic_signature(self) -> Tuple[Any, ...]:
        return tuple(sorted(self.fields)), tuple(sorted(self.tolerances.items()))


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (
                    (_freeze_value(key), _freeze_value(item))
                    for key, item in value.items()
                ),
                key=repr,
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_value(item) for item in value), key=repr))
    if isinstance(value, Enum):
        return value.value
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def requirement_semantic_signature(requirement: Requirement) -> Tuple[Any, ...]:
    method = getattr(requirement, "semantic_signature", None)
    if method is not None:
        return tuple(method())
    # Unknown third-party requirements fail closed: only the same object is
    # considered semantically identical during this process.
    return (
        "opaque",
        type(requirement).__module__,
        type(requirement).__qualname__,
        id(requirement),
    )


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
        if math.isnan(float(self.tolerance)) or self.tolerance < 0:
            raise ValueError("macro tolerance must be non-negative")
        missing = set(self.equivalence.fields) - set(self.observables)
        if missing:
            raise ValueError("equivalence fields must be declared observables: %s" % sorted(missing))

    @property
    def requirements(self) -> Tuple[Requirement, ...]:
        requirements = self.objectives + self.invariants + self.constraints
        if self.tolerance == 0:
            return requirements
        # Macro tolerance is the default numeric error bound.  A non-zero
        # requirement-local tolerance remains an explicit override.
        return tuple(
            replace(requirement, tolerance=self.tolerance)
            if isinstance(requirement, (FieldRequirement, ModelMetricRequirement))
            and requirement.tolerance == 0
            else requirement
            for requirement in requirements
        )

    def semantic_signature(self) -> Tuple[Any, ...]:
        def canonical(requirements: Tuple[Requirement, ...]) -> Tuple[Any, ...]:
            return tuple(
                sorted(
                    (requirement_semantic_signature(item) for item in requirements),
                    key=repr,
                )
            )

        return (
            tuple(sorted(self.observables)),
            canonical(self.objectives),
            self.equivalence.semantic_signature(),
            canonical(self.invariants),
            canonical(self.constraints),
            self.tolerance,
            self.horizon,
            _freeze_value(self.ambiguous_terms),
            tuple(sorted(self.assumptions)),
            tuple(sorted(self.tags)),
        )

    def semantically_equivalent(self, other: "MacroSpec") -> bool:
        return self.semantic_signature() == other.semantic_signature()

    def promote_observable(self, field_name: str, resolution: Optional[float] = None) -> "MacroSpec":
        observables = self.observables
        fields = self.equivalence.fields
        tolerances = dict(self.equivalence.tolerances)
        if field_name not in observables:
            observables += (field_name,)
        if field_name not in fields:
            fields += (field_name,)
        if resolution is not None:
            if resolution <= 0:
                raise ValueError("resolution must be positive")
            tolerances[field_name] = resolution
        return replace(
            self,
            observables=observables,
            equivalence=EquivalenceSpec(fields, tolerances),
        )


class ExecutableModel(Protocol):
    name: str
    metrics: ModelMetrics
    assumptions: Tuple[str, ...]
    failure_boundaries: Tuple[str, ...]
    prior_reliability: float
    capabilities: Tuple[str, ...]

    def simulate(self, context: Context, horizon: int) -> Iterable[Trace]:
        ...


Transition = Callable[[State, str, Context], State]
Readout = Callable[[State, Context], Snapshot]
Applicability = Callable[[State, str, Context], bool]


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
    applicable: Optional[Applicability] = None

    def __post_init__(self) -> None:
        unknown = set(self.initial_states) - set(self.states)
        if unknown:
            raise ValueError("unknown initial states: %s" % sorted(unknown))
        if len(self.initial_states) != len(set(self.initial_states)):
            raise ValueError("initial state names must be unique")
        if not 0.0 <= self.prior_reliability <= 1.0:
            raise ValueError("prior reliability must be in [0, 1]")
        if self.applicable is not None and not callable(self.applicable):
            raise TypeError("applicable must be callable")

    def supports(self, state: State, action: str, context: Context) -> bool:
        """Whether an otherwise declared action is defined on one state."""

        if action != "noop" and action not in self.actions:
            return False
        if self.applicable is None:
            return True
        return bool(self.applicable(dict(state), action, context))

    def step(self, state: State, action: str, context: Context) -> State:
        if action != "noop" and action not in self.actions:
            raise ValueError("unsupported action %r for model %s" % (action, self.name))
        if not self.supports(state, action, context):
            raise UndefinedTransition(
                "action %r is undefined on the current support for model %s"
                % (action, self.name)
            )
        return self.transition(dict(state), action, context)

    def observe(self, state: State, context: Context) -> Snapshot:
        return dict(self.readout(state, context))

    def _interventions(self, context: Context) -> Tuple[Intervention, ...]:
        interventions = context.interventions
        if context.include_baseline and not any(item.name == "baseline" for item in interventions):
            interventions = (Intervention("baseline"),) + interventions
        if not interventions:
            interventions = (Intervention("baseline"),)
        return interventions

    def scenario_count(self, context: Context) -> int:
        return len(self.scenario_manifest(context))

    def scenario_manifest(self, context: Context) -> Tuple[ScenarioKey, ...]:
        interventions = FiniteStateModel._interventions(self, context)
        return tuple(
            ScenarioKey(initial_name, intervention.name)
            for initial_name in self.initial_states
            for intervention in interventions
        )

    def simulate(self, context: Context, horizon: int) -> Iterable[Trace]:
        interventions = self._interventions(context)
        for initial_name in self.initial_states:
            for intervention in interventions:
                state = dict(self.states[initial_name])
                snapshots = [self.observe(state, context)]
                for step in range(horizon):
                    state = dict(self.step(state, intervention.action_at(step), context))
                    snapshots.append(self.observe(state, context))
                yield Trace(
                    model_name=self.name,
                    initial_state=initial_name,
                    intervention=intervention.name,
                    snapshots=tuple(snapshots),
                )

    def with_promoted_observables(self, fields: Iterable[str]) -> "FiniteStateModel":
        promoted = tuple(dict.fromkeys(fields))
        missing = [
            field_name
            for field_name in promoted
            if any(field_name not in state for state in self.states.values())
        ]
        if missing:
            raise ValueError("cannot promote unavailable state fields: %s" % sorted(set(missing)))
        original_readout = self.readout

        def promoted_readout(state: State, context: Context) -> Snapshot:
            observed = dict(original_readout(state, context))
            for field_name in promoted:
                observed[field_name] = state[field_name]
            return observed

        suffix = "+" + "+".join(promoted) if promoted else ""
        return replace(self, name=self.name + suffix, readout=promoted_readout)


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
    def verification_score(self) -> float:
        product = self.coverage * self.robustness * self.assumption_reliability
        return product ** (1.0 / 3.0) if product else 0.0

    @property
    def value(self) -> float:
        """Backward-compatible alias; this is a score, not a calibrated probability."""

        return self.verification_score


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
    complete: bool = True
    requirements_passed: bool = True
    coverage_authority: str = "candidate-enumeration"

    @property
    def verification_score(self) -> float:
        return self.confidence.verification_score


@dataclass(frozen=True)
class Counterexample:
    kind: str
    summary: str
    witness: Mapping[str, Any]
    violated: Tuple[str, ...] = ()
    suggested_refinements: Tuple[str, ...] = ()
    blocking: bool = True


@dataclass(frozen=True)
class ProbeOutcome:
    counterexample: Optional[Counterexample]
    certificate: Optional[SatisfactionCertificate] = None

    @property
    def simulations_used(self) -> int:
        return self.certificate.verified_scenarios if self.certificate else 0


@dataclass(frozen=True)
class CandidateEvaluation:
    model: ExecutableModel
    certificate: SatisfactionCertificate
    counterexamples: Tuple[Counterexample, ...] = ()
    probe_certificates: Tuple[SatisfactionCertificate, ...] = ()

    @property
    def verification_score(self) -> float:
        certificates = (self.certificate,) + self.probe_certificates
        return min(item.verification_score for item in certificates)

    @property
    def confidence(self) -> float:
        """Backward-compatible alias for the aggregate verification score."""

        return self.verification_score


@dataclass(frozen=True)
class RealizationResult:
    spec: MacroSpec
    candidates: Tuple[CandidateEvaluation, ...]
    rejected: Tuple[CandidateEvaluation, ...]
    dominated: Tuple[CandidateEvaluation, ...]
    searched_candidates: int
    truncated: bool
    simulations_used: int = 0


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

    def __post_init__(self) -> None:
        if math.isnan(float(self.cost)) or self.cost < 0:
            raise ValueError("experiment cost must be non-negative")


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
    ranking_score: float
    evidence: Tuple[Evidence, ...]
    caveats: Tuple[str, ...] = ()

    @property
    def confidence(self) -> float:
        """Backward-compatible alias for the uncalibrated ranking score."""

        return self.ranking_score


@dataclass(frozen=True)
class InterpretationResult:
    model_name: str
    candidates: Tuple[InterpretationCandidate, ...]
    equivalent_explanations: Tuple[Tuple[str, ...], ...]
    discriminating_query: Optional[DiscriminatingQuery]
    non_identifiable: bool
    score_semantics: str = "uncalibrated ranking score; not a probability"
    simulations_used: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class ClosureReport:
    closed: bool
    checked_pairs: int
    counterexamples: Tuple[Counterexample, ...]
    suggested_features: Tuple[str, ...] = ()
    complete: bool = True
    explored_states: int = 0


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
