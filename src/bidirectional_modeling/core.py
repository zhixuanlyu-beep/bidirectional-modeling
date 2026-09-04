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

from .structural import (
    callable_fingerprint,
    freeze_value,
    isolated_copy,
    isolated_mapping,
    validate_fingerprint,
)


Snapshot = Mapping[str, Any]
State = Mapping[str, Any]


class UndefinedTransition(LookupError):
    """A declared action has no result on the current semantic support."""


class NonDeterministicModelError(RuntimeError):
    """The same model callback input produced different semantic results."""


def _freeze_context_value(value: Any) -> Any:
    """Canonicalize context data or reject values with unstable identities."""

    return freeze_value(value, purpose="context fingerprints")


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
        if (
            not isinstance(self.max_candidates, int)
            or isinstance(self.max_candidates, bool)
            or not isinstance(self.max_simulations, int)
            or isinstance(self.max_simulations, bool)
        ):
            raise TypeError("candidate and simulation budget limits must be integers")
        if self.max_candidates <= 0 or self.max_simulations <= 0:
            raise ValueError("budget limits must be positive")
        max_cost = float(self.max_cost)
        if math.isnan(max_cost) or max_cost < 0:
            raise ValueError("max_cost must be non-negative")
        object.__setattr__(self, "max_cost", max_cost)


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
        object.__setattr__(
            self,
            "environment",
            isolated_mapping(
                self.environment,
                purpose="context environment",
            ),
        )

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
        values = tuple(float(item) for item in (self.cost, self.complexity, self.risk))
        if any(not math.isfinite(item) or item < 0 for item in values):
            raise ValueError("model metrics must be finite and non-negative")
        object.__setattr__(self, "cost", values[0])
        object.__setattr__(self, "complexity", values[1])
        object.__setattr__(self, "risk", values[2])

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
        tolerance = float(self.tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("requirement tolerance must be finite and non-negative")
        object.__setattr__(self, "tolerance", tolerance)

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
        tolerance = float(self.tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("requirement tolerance must be finite and non-negative")
        object.__setattr__(self, "tolerance", tolerance)

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
        return (
            "custom-v2",
            self.category.value,
            callable_fingerprint(
                self.checker,
                semantic_id=self.semantic_id,
                purpose="custom requirement fingerprint",
            ),
        )

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
        if any(not isinstance(item, str) or not item for item in self.fields):
            raise ValueError("equivalence field names must be non-empty strings")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("equivalence fields must be unique")
        unknown = set(self.tolerances) - set(self.fields)
        if unknown:
            raise ValueError("tolerances reference undeclared fields: %s" % sorted(unknown))
        normalized = {key: float(value) for key, value in self.tolerances.items()}
        invalid = {
            key: value
            for key, value in normalized.items()
            if not math.isfinite(value) or value <= 0
        }
        if invalid:
            raise ValueError(
                "equivalence resolutions must be finite and positive: %s" % invalid
            )
        object.__setattr__(self, "tolerances", normalized)

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
                if not math.isfinite(float(value)):
                    raise ValueError(
                        "equivalence field %r must be finite" % field_name
                    )
                value = math.floor(float(value) / tolerance + 0.5)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("equivalence field %r must be finite" % field_name)
            result.append(value)
        return tuple(result)

    def semantic_signature(self) -> Tuple[Any, ...]:
        return tuple(sorted(self.fields)), tuple(sorted(self.tolerances.items()))


def _freeze_value(value: Any) -> Any:
    return freeze_value(value, purpose="deterministic structural identity")


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
        if not isinstance(self.horizon, int) or isinstance(self.horizon, bool):
            raise TypeError("horizon must be an integer")
        if self.horizon < 1:
            raise ValueError("horizon must be at least one")
        tolerance = float(self.tolerance)
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("macro tolerance must be finite and non-negative")
        object.__setattr__(self, "tolerance", tolerance)
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
            signatures = (
                freeze_value(
                    requirement_semantic_signature(item),
                    purpose="macro requirement deterministic structural identity",
                )
                for item in requirements
            )
            return tuple(sorted(signatures))

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
            resolution = float(resolution)
            if not math.isfinite(resolution) or resolution <= 0:
                raise ValueError("resolution must be finite and positive")
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
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("model name must be a non-empty string")
        if not isinstance(self.states, Mapping):
            raise TypeError("model states must be a mapping")
        if any(not isinstance(name, str) or not name for name in self.states):
            raise ValueError("model state names must be non-empty strings")
        if any(
            not isinstance(name, str) or not name for name in self.initial_states
        ):
            raise ValueError("initial state names must be non-empty strings")
        if any(not isinstance(action, str) or not action for action in self.actions):
            raise ValueError("model actions must be non-empty strings")
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("model actions must be unique")
        unknown = set(self.initial_states) - set(self.states)
        if unknown:
            raise ValueError("unknown initial states: %s" % sorted(unknown))
        if len(self.initial_states) != len(set(self.initial_states)):
            raise ValueError("initial state names must be unique")
        if not 0.0 <= self.prior_reliability <= 1.0:
            raise ValueError("prior reliability must be in [0, 1]")
        if not callable(self.transition):
            raise TypeError("transition must be callable")
        if not callable(self.readout):
            raise TypeError("readout must be callable")
        if self.applicable is not None and not callable(self.applicable):
            raise TypeError("applicable must be callable")
        # A model owns its initial-state data.  Candidate construction must not
        # retain aliases into an experiment or another candidate's nested state.
        self.states = {
            name: isolated_mapping(state, purpose="initial model state %r" % name)
            for name, state in self.states.items()
        }

    def supports(self, state: State, action: str, context: Context) -> bool:
        """Whether an otherwise declared action is defined on one state."""

        if action != "noop" and action not in self.actions:
            return False
        if self.applicable is None:
            return True
        isolated = isolated_mapping(state, purpose="applicability input state")
        callback_context = isolated_copy(
            context, purpose="applicability input context"
        )
        return bool(self.applicable(isolated, action, callback_context))

    def step(self, state: State, action: str, context: Context) -> State:
        if action != "noop" and action not in self.actions:
            raise ValueError("unsupported action %r for model %s" % (action, self.name))
        if not self.supports(state, action, context):
            raise UndefinedTransition(
                "action %r is undefined on the current support for model %s"
                % (action, self.name)
            )
        isolated = isolated_mapping(state, purpose="transition input state")
        callback_context = isolated_copy(
            context, purpose="transition input context"
        )
        result = self.transition(isolated, action, callback_context)
        return isolated_mapping(result, purpose="transition result state")

    def observe(self, state: State, context: Context) -> Snapshot:
        isolated = isolated_mapping(state, purpose="readout input state")
        callback_context = isolated_copy(
            context, purpose="readout input context"
        )
        result = self.readout(isolated, callback_context)
        return isolated_mapping(result, purpose="readout result")

    def audited_step(self, state: State, action: str, context: Context) -> State:
        """Replay one transition and reject inconsistent support or successors."""

        def attempt() -> Tuple[bool, Optional[State], Optional[UndefinedTransition]]:
            try:
                return True, self.step(state, action, context), None
            except UndefinedTransition as error:
                return False, None, error

        first_defined, first, first_error = attempt()
        second_defined, second, _ = attempt()
        if first_defined != second_defined:
            raise NonDeterministicModelError(
                "non-deterministic action support for %r in model %s"
                % (action, self.name)
            )
        if not first_defined:
            if first_error is None:
                raise RuntimeError(
                    "undefined transition did not provide an error"
                )
            raise first_error
        if first is None or second is None:
            raise RuntimeError(
                "defined transition did not provide a successor state"
            )
        first_key = freeze_value(
            first,
            purpose="transition deterministic structural identity",
        )
        second_key = freeze_value(
            second,
            purpose="transition deterministic structural identity",
        )
        if first_key != second_key:
            raise NonDeterministicModelError(
                "non-deterministic successor for action %r in model %s"
                % (action, self.name)
            )
        return isolated_mapping(first, purpose="audited transition result")

    def audited_observe(self, state: State, context: Context) -> Snapshot:
        """Replay a readout and reject changing observations."""

        first = self.observe(state, context)
        second = self.observe(state, context)
        first_key = freeze_value(
            first,
            purpose="readout deterministic structural identity",
        )
        second_key = freeze_value(
            second,
            purpose="readout deterministic structural identity",
        )
        if first_key != second_key:
            raise NonDeterministicModelError(
                "non-deterministic readout in model %s" % self.name
            )
        return isolated_mapping(first, purpose="audited readout result")

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
        if not isinstance(horizon, int) or isinstance(horizon, bool):
            raise TypeError("simulation horizon must be an integer")
        if horizon < 0:
            raise ValueError("simulation horizon must be non-negative")
        interventions = self._interventions(context)
        for initial_name in self.initial_states:
            for intervention in interventions:
                state = isolated_mapping(
                    self.states[initial_name], purpose="simulation initial state"
                )
                snapshots = [self.audited_observe(state, context)]
                for step in range(horizon):
                    state = self.audited_step(
                        state, intervention.action_at(step), context
                    )
                    snapshots.append(self.audited_observe(state, context))
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
    horizon: int
    spec_fingerprint: str
    model_fingerprint: str
    context_fingerprint: str
    trace_batch_fingerprint: str
    protocol_fingerprint: str
    claim_fingerprint: str
    max_cost: float
    complete: bool = True
    requirements_passed: bool = True
    coverage_authority: str = "candidate-enumeration"

    def __post_init__(self) -> None:
        if not self.spec_name or not self.model_name:
            raise ValueError("satisfaction certificate identities must be non-empty")
        if not isinstance(self.horizon, int) or isinstance(self.horizon, bool):
            raise TypeError("satisfaction certificate horizon must be an integer")
        if self.horizon < 1:
            raise ValueError("satisfaction certificate horizon must be at least one")
        if (
            not isinstance(self.verified_scenarios, int)
            or isinstance(self.verified_scenarios, bool)
        ):
            raise TypeError("verified_scenarios must be an integer")
        if self.verified_scenarios < 0:
            raise ValueError("verified_scenarios must be non-negative")
        if self.satisfied and (not self.complete or not self.requirements_passed):
            raise ValueError(
                "a satisfied certificate must be complete and pass its requirements"
            )
        for label, fingerprint in (
            ("macro specification fingerprint", self.spec_fingerprint),
            ("model evidence fingerprint", self.model_fingerprint),
            ("context fingerprint", self.context_fingerprint),
            ("trace batch fingerprint", self.trace_batch_fingerprint),
            ("satisfaction protocol fingerprint", self.protocol_fingerprint),
            ("satisfaction claim fingerprint", self.claim_fingerprint),
        ):
            validate_fingerprint(fingerprint, purpose=label)
        max_cost = float(self.max_cost)
        if math.isnan(max_cost) or max_cost < 0:
            raise ValueError("certificate max_cost must be non-negative")
        object.__setattr__(self, "max_cost", max_cost)
        from .provenance import (
            satisfaction_claim_fingerprint,
            satisfaction_protocol_fingerprint,
        )

        expected_protocol = satisfaction_protocol_fingerprint(
            self.spec_fingerprint,
            self.model_fingerprint,
            self.context_fingerprint,
            self.trace_batch_fingerprint,
            self.max_cost,
        )
        if expected_protocol != self.protocol_fingerprint:
            raise ValueError(
                "satisfaction certificate fields do not match its protocol fingerprint"
            )
        expected_claim = satisfaction_claim_fingerprint(
            self.protocol_fingerprint,
            self.spec_name,
            self.model_name,
            self.satisfied,
            self.checks,
            self.verified_scenarios,
            self.confidence,
            self.assumptions,
            self.failure_boundaries,
            self.horizon,
            self.complete,
            self.requirements_passed,
            self.coverage_authority,
        )
        if expected_claim != self.claim_fingerprint:
            raise ValueError(
                "satisfaction certificate fields do not match its claim fingerprint"
            )

    def verify_integrity(self) -> bool:
        """Return whether protocol and semantic-result fields remain intact."""

        from .provenance import (
            satisfaction_claim_fingerprint,
            satisfaction_protocol_fingerprint,
        )

        try:
            expected_protocol = satisfaction_protocol_fingerprint(
                self.spec_fingerprint,
                self.model_fingerprint,
                self.context_fingerprint,
                self.trace_batch_fingerprint,
                self.max_cost,
            )
            expected_claim = satisfaction_claim_fingerprint(
                self.protocol_fingerprint,
                self.spec_name,
                self.model_name,
                self.satisfied,
                self.checks,
                self.verified_scenarios,
                self.confidence,
                self.assumptions,
                self.failure_boundaries,
                self.horizon,
                self.complete,
                self.requirements_passed,
                self.coverage_authority,
            )
        except Exception:
            return False
        return (
            expected_protocol == self.protocol_fingerprint
            and expected_claim == self.claim_fingerprint
        )

    @property
    def verification_score(self) -> float:
        return self.confidence.verification_score if self.verify_integrity() else 0.0

    def binds_specification(self, spec: MacroSpec) -> bool:
        from .provenance import macro_spec_fingerprint

        try:
            return (
                self.verify_integrity()
                and macro_spec_fingerprint(spec) == self.spec_fingerprint
            )
        except Exception:
            return False

    def binds_context(self, context: Context) -> bool:
        from .provenance import context_fingerprint

        try:
            return (
                self.verify_integrity()
                and context_fingerprint(context) == self.context_fingerprint
            )
        except Exception:
            return False

    def binds_evidence(
        self,
        model: ExecutableModel,
        traces: Iterable[Trace],
    ) -> bool:
        from .provenance import observed_model_fingerprint

        try:
            return (
                self.verify_integrity()
                and observed_model_fingerprint(model, traces, self.horizon)
                == self.model_fingerprint
            )
        except Exception:
            return False

    def binds_trace_batch(self, batch: Any) -> bool:
        """Whether this certificate was produced from the supplied trace batch."""

        return (
            self.verify_integrity()
            and getattr(batch, "protocol_fingerprint", None)
            == self.trace_batch_fingerprint
            and getattr(batch, "horizon", None) == self.horizon
            and getattr(batch, "model_fingerprint", None)
            == self.model_fingerprint
            and getattr(batch, "context_fingerprint", None)
            == self.context_fingerprint
        )


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

    def __post_init__(self) -> None:
        accepted = self.candidates + self.dominated
        if any(
            not item.certificate.binds_specification(self.spec)
            for item in accepted
        ):
            raise ValueError(
                "accepted realization certificates must bind the result specification"
            )


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
        cost = float(self.cost)
        if not math.isfinite(cost) or cost < 0:
            raise ValueError("experiment cost must be finite and non-negative")
        object.__setattr__(self, "cost", cost)


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

    def __post_init__(self) -> None:
        if not self.certificate.binds_specification(self.hypothesis.spec):
            raise ValueError(
                "interpretation certificate must bind its hypothesis specification"
            )

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
