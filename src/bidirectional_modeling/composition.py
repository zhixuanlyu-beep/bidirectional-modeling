"""Bounded selection of microscopic composition rules.

The selector compares candidate transition/composition rules against shared,
caller-owned operational observations.  A rule is eligible only when it fits
every declared test and its reachable residual quotient is complete, stable,
congruent, and reproducible by the extracted finite context basis.  Eligible
rules are ranked by an explicit two-part description-length proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Optional, Sequence, Tuple

from .core import (
    Applicability,
    Context,
    Counterexample,
    EquivalenceSpec,
    FiniteStateModel,
    ModelMetrics,
    Readout,
    State,
    Transition,
    UndefinedTransition,
)
from .residual import ResidualQuotientAnalyzer, ResidualQuotientReport


@dataclass(frozen=True)
class CompositionRule:
    """One candidate microscopic composition rule under a fixed codec."""

    name: str
    transition: Transition
    description_length: float
    applicable: Optional[Applicability] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("composition rule name must be non-empty")
        if not callable(self.transition):
            raise TypeError("composition rule transition must be callable")
        if self.applicable is not None and not callable(self.applicable):
            raise TypeError("composition rule applicable hook must be callable")
        length = float(self.description_length)
        if not math.isfinite(length) or length < 0:
            raise ValueError(
                "composition rule description_length must be finite and non-negative"
            )
        object.__setattr__(self, "description_length", length)


@dataclass(frozen=True)
class CompositionTest:
    """One unlabeled operational observation of a completed action context."""

    name: str
    initial_state: str
    actions: Tuple[str, ...]
    expected_defined: bool
    expected_observation: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("composition test name must be non-empty")
        if not self.initial_state:
            raise ValueError("composition test initial_state must be non-empty")
        if self.expected_defined and self.expected_observation is None:
            raise ValueError(
                "a defined composition test requires an expected observation"
            )
        if not self.expected_defined and self.expected_observation is not None:
            raise ValueError(
                "an undefined composition test cannot declare an observation"
            )


@dataclass(frozen=True)
class CompositionExperiment:
    """A fixed finite domain and observation protocol shared by all rules."""

    name: str
    states: Mapping[str, State]
    initial_states: Tuple[str, ...]
    actions: Tuple[str, ...]
    readout: Readout
    equivalence: EquivalenceSpec
    tests: Tuple[CompositionTest, ...]
    context: Context = field(default_factory=Context)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("composition experiment name must be non-empty")
        if not self.initial_states:
            raise ValueError(
                "composition experiments require at least one initial state"
            )
        unknown = set(self.initial_states) - set(self.states)
        if unknown:
            raise ValueError(
                "unknown composition experiment initial states: %s"
                % sorted(unknown)
            )
        if len(self.initial_states) != len(set(self.initial_states)):
            raise ValueError("composition experiment initial states must be unique")
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("composition experiment actions must be unique")
        if not callable(self.readout):
            raise TypeError("composition experiment readout must be callable")
        if not self.tests:
            raise ValueError(
                "composition experiments require at least one operational test"
            )
        test_names = tuple(test.name for test in self.tests)
        if len(test_names) != len(set(test_names)):
            raise ValueError("composition experiment test names must be unique")
        declared_actions = set(self.actions) | {"noop"}
        for test in self.tests:
            if test.initial_state not in self.states:
                raise ValueError(
                    "composition test %r references unknown initial state %r"
                    % (test.name, test.initial_state)
                )
            unknown_actions = set(test.actions) - declared_actions
            if unknown_actions:
                raise ValueError(
                    "composition test %r uses undeclared actions: %s"
                    % (test.name, sorted(unknown_actions))
                )
            if test.expected_defined:
                self.equivalence.signature(test.expected_observation)


@dataclass(frozen=True)
class CompositionTestResult:
    """Observed behavior of one rule on one operational test."""

    test_name: str
    actions: Tuple[str, ...]
    passed: bool
    expected_defined: bool
    actual_defined: Optional[bool]
    expected_signature: Tuple[Any, ...]
    actual_signature: Tuple[Any, ...]
    failure_step: Optional[int] = None
    detail: str = ""


@dataclass(frozen=True)
class CompositionCaseResult:
    """One rule evaluated on one finite experiment case."""

    experiment_name: str
    model_name: str
    tests: Tuple[CompositionTestResult, ...]
    residual_report: Optional[ResidualQuotientReport]
    counterexamples: Tuple[Counterexample, ...]
    state_description_length: float
    transition_description_length: float
    context_description_length: float
    exception_description_length: float
    analysis_error: Optional[str] = None

    @property
    def certified(self) -> bool:
        return (
            self.analysis_error is None
            and self.residual_report is not None
            and self.residual_report.minimal
            and self.residual_report.context_basis_reproduces_partition
            and bool(self.tests)
            and all(test.passed for test in self.tests)
            and not self.counterexamples
        )

    @property
    def class_count(self) -> int:
        if self.residual_report is None:
            return 0
        return self.residual_report.quotient.class_count

    @property
    def structural_description_length(self) -> float:
        return (
            self.state_description_length
            + self.transition_description_length
            + self.context_description_length
            + self.exception_description_length
        )


@dataclass(frozen=True)
class CompositionRuleEvaluation:
    """Cross-experiment certificate and MDL score for one candidate rule."""

    rule: CompositionRule
    cases: Tuple[CompositionCaseResult, ...]

    @property
    def certified(self) -> bool:
        return bool(self.cases) and all(case.certified for case in self.cases)

    @property
    def total_description_length(self) -> float:
        return self.rule.description_length + sum(
            case.structural_description_length for case in self.cases
        )

    @property
    def counterexamples(self) -> Tuple[Counterexample, ...]:
        return tuple(
            counterexample
            for case in self.cases
            for counterexample in case.counterexamples
        )


@dataclass(frozen=True)
class CompositionSelectionReport:
    """Ranked certified rules plus fail-closed rejected candidates."""

    experiment_names: Tuple[str, ...]
    evaluations: Tuple[CompositionRuleEvaluation, ...]
    ranked: Tuple[CompositionRuleEvaluation, ...]
    rejected: Tuple[CompositionRuleEvaluation, ...]
    selected: Tuple[CompositionRuleEvaluation, ...]
    exception_penalty: float
    boundaries: Tuple[str, ...] = ()

    @property
    def unique_selection(self) -> bool:
        """Whether one rule is uniquely shortest under the declared protocol."""

        return len(self.selected) == 1

    @property
    def selected_rule_names(self) -> Tuple[str, ...]:
        return tuple(item.rule.name for item in self.selected)


class CompositionRuleSelector:
    """Reject inconsistent rules and rank the remaining minimal quotients."""

    def __init__(
        self, residual_analyzer: Optional[ResidualQuotientAnalyzer] = None
    ) -> None:
        self.residual_analyzer = residual_analyzer or ResidualQuotientAnalyzer()

    @staticmethod
    def _test_rule(
        rule: CompositionRule,
        experiment: CompositionExperiment,
        model: FiniteStateModel,
        test: CompositionTest,
    ) -> Tuple[CompositionTestResult, Optional[Counterexample]]:
        expected_signature = (
            experiment.equivalence.signature(test.expected_observation)
            if test.expected_defined
            else ()
        )
        state = dict(experiment.states[test.initial_state])
        actual_defined: Optional[bool] = True
        actual_signature: Tuple[Any, ...] = ()
        failure_step = None
        error_detail = None
        for step, action in enumerate(test.actions):
            try:
                state = dict(model.step(state, action, experiment.context))
            except UndefinedTransition:
                actual_defined = False
                failure_step = step
                break
            except Exception as error:
                actual_defined = None
                failure_step = step
                error_detail = "%s: %s" % (type(error).__name__, error)
                break

        if actual_defined:
            try:
                observation = model.observe(state, experiment.context)
                actual_signature = experiment.equivalence.signature(observation)
            except Exception as error:
                actual_defined = None
                error_detail = "%s: %s" % (type(error).__name__, error)

        passed = bool(
            error_detail is None
            and actual_defined == test.expected_defined
            and (
                not test.expected_defined
                or actual_signature == expected_signature
            )
        )
        if passed:
            detail = "matched the declared operational outcome"
            counterexample = None
        elif error_detail is not None:
            detail = "composition rule raised an uncertified error: %s" % error_detail
            kind = "composition-rule-error"
            summary = "candidate rule failed during an operational test"
            counterexample = Counterexample(
                kind=kind,
                summary=summary,
                witness={
                    "rule": rule.name,
                    "experiment": experiment.name,
                    "test": test.name,
                    "initial_state": test.initial_state,
                    "actions": test.actions,
                    "failure_step": failure_step,
                    "error": error_detail,
                },
                violated=("operational test %s" % test.name,),
                suggested_refinements=(
                    "repair the transition implementation before comparing the rule",
                ),
            )
        elif actual_defined != test.expected_defined:
            detail = "candidate and experiment disagree on action support"
            kind = "composition-support-mismatch"
            summary = "candidate rule has the wrong support on a tested context"
            counterexample = Counterexample(
                kind=kind,
                summary=summary,
                witness={
                    "rule": rule.name,
                    "experiment": experiment.name,
                    "test": test.name,
                    "initial_state": test.initial_state,
                    "actions": test.actions,
                    "failure_step": failure_step,
                    "expected_defined": test.expected_defined,
                    "actual_defined": actual_defined,
                },
                violated=("operational test %s" % test.name,),
                suggested_refinements=(
                    "reject the rule or refine its explicit applicability condition",
                ),
            )
        else:
            detail = "candidate produced a different terminal observation"
            kind = "composition-observation-mismatch"
            summary = "candidate rule disagrees with an observed context outcome"
            counterexample = Counterexample(
                kind=kind,
                summary=summary,
                witness={
                    "rule": rule.name,
                    "experiment": experiment.name,
                    "test": test.name,
                    "initial_state": test.initial_state,
                    "actions": test.actions,
                    "expected_signature": expected_signature,
                    "actual_signature": actual_signature,
                },
                violated=("operational test %s" % test.name,),
                suggested_refinements=(
                    "reject the rule or add the distinguishing context to its specification",
                ),
            )

        result = CompositionTestResult(
            test_name=test.name,
            actions=test.actions,
            passed=passed,
            expected_defined=test.expected_defined,
            actual_defined=actual_defined,
            expected_signature=expected_signature,
            actual_signature=actual_signature,
            failure_step=failure_step,
            detail=detail,
        )
        return result, counterexample

    @staticmethod
    def _residual_counterexamples(
        rule: CompositionRule,
        experiment: CompositionExperiment,
        report: Optional[ResidualQuotientReport],
        analysis_error: Optional[str],
    ) -> Tuple[Counterexample, ...]:
        if report is None:
            return (
                Counterexample(
                    kind="composition-analysis-error",
                    summary="the candidate residual quotient could not be analyzed",
                    witness={
                        "rule": rule.name,
                        "experiment": experiment.name,
                        "error": analysis_error,
                    },
                    violated=("complete residual quotient analysis",),
                    suggested_refinements=(
                        "repair the candidate state representation or readout",
                    ),
                ),
            )

        counterexamples = []
        if not report.complete:
            counterexamples.append(
                Counterexample(
                    kind="composition-analysis-incomplete",
                    summary="the candidate reachable transition domain is incomplete",
                    witness={
                        "rule": rule.name,
                        "experiment": experiment.name,
                        "boundaries": report.boundaries,
                    },
                    violated=("complete residual quotient analysis",),
                    suggested_refinements=(
                        "increase the enumeration bounds or repair unknown transitions",
                    ),
                )
            )
        if not report.stable:
            counterexamples.append(
                Counterexample(
                    kind="composition-residual-unstable",
                    summary="context refinement stopped before the residual partition stabilized",
                    witness={
                        "rule": rule.name,
                        "experiment": experiment.name,
                        "filtration_depth": len(report.filtration) - 1,
                    },
                    violated=("stable residual quotient",),
                    suggested_refinements=(
                        "increase max_context_depth before selecting the rule",
                    ),
                )
            )
        for transition in report.quotient.transitions:
            if transition.complete and not transition.well_defined:
                residual_class = report.quotient.classes[
                    transition.source_class
                ]
                counterexamples.append(
                    Counterexample(
                        kind="composition-non-congruence",
                        summary="one macro class has incompatible microscopic successors",
                        witness={
                            "rule": rule.name,
                            "experiment": experiment.name,
                            "source_class": transition.source_class,
                            "member_states": residual_class.members,
                            "action": transition.action,
                            "target_classes": transition.target_classes,
                            "includes_undefined": transition.undefined,
                        },
                        violated=("composition congruence",),
                        suggested_refinements=(
                            "refine the semantic state or reject the composition rule",
                        ),
                    )
                )
        if not report.context_basis_reproduces_partition:
            counterexamples.append(
                Counterexample(
                    kind="composition-context-basis-incomplete",
                    summary="the extracted test basis does not reproduce the selected partition",
                    witness={
                        "rule": rule.name,
                        "experiment": experiment.name,
                        "context_basis": report.context_basis,
                        "boundaries": report.boundaries,
                    },
                    violated=("finite residual context basis",),
                    suggested_refinements=(
                        "increase max_context_tests before selecting the rule",
                    ),
                )
            )
        return tuple(counterexamples)

    @staticmethod
    def _description_lengths(
        report: Optional[ResidualQuotientReport],
        test_results: Sequence[CompositionTestResult],
        exception_penalty: float,
        analysis_error: Optional[str],
    ) -> Tuple[float, float, float, float]:
        failed_tests = sum(not result.passed for result in test_results)
        if report is None:
            return (
                0.0,
                0.0,
                0.0,
                exception_penalty * (failed_tests + int(analysis_error is not None)),
            )

        class_count = report.quotient.class_count
        action_count = len(report.quotient.actions)
        state_width = max(1, int(math.ceil(math.log2(class_count + 1))))
        action_width = max(1, int(math.ceil(math.log2(action_count + 1))))
        state_length = float(class_count * state_width)
        # Each class/action cell encodes one support bit and, when defined, a
        # target class.  This fixed-width upper bound is deliberately simple
        # and comparable across rules in the same experiment.
        transition_length = float(
            class_count * action_count * (1 + state_width)
        )
        context_length = float(
            sum(
                1 + len(word) * action_width
                for word in report.context_basis
            )
        )
        unknown_edges = sum(
            not transition.complete
            for transition in report.quotient.transitions
        )
        conflicting_edges = sum(
            transition.complete and not transition.well_defined
            for transition in report.quotient.transitions
        )
        certification_gaps = (
            int(not report.stable)
            + int(not report.context_basis_reproduces_partition)
        )
        exception_length = exception_penalty * (
            failed_tests
            + unknown_edges
            + conflicting_edges
            + certification_gaps
        )
        return (
            state_length,
            transition_length,
            context_length,
            float(exception_length),
        )

    def select(
        self,
        rules: Sequence[CompositionRule],
        experiments: Sequence[CompositionExperiment],
        max_reachability_depth: Optional[int] = None,
        max_states: int = 1_000,
        max_context_depth: Optional[int] = None,
        max_context_tests: int = 256,
        exception_penalty: float = 64.0,
    ) -> CompositionSelectionReport:
        """Evaluate a finite candidate set and return the shortest certificate."""

        rules = tuple(rules)
        experiments = tuple(experiments)
        if not rules:
            raise ValueError("at least one composition rule is required")
        if not experiments:
            raise ValueError("at least one composition experiment is required")
        if max_states < 1:
            raise ValueError("max_states must be positive")
        if max_reachability_depth is not None and max_reachability_depth < 0:
            raise ValueError("max_reachability_depth must be non-negative")
        if max_context_depth is not None and max_context_depth < 0:
            raise ValueError("max_context_depth must be non-negative")
        if max_context_tests < 1:
            raise ValueError("max_context_tests must be positive")
        rule_names = tuple(rule.name for rule in rules)
        if len(rule_names) != len(set(rule_names)):
            raise ValueError("composition rule names must be unique")
        experiment_names = tuple(experiment.name for experiment in experiments)
        if len(experiment_names) != len(set(experiment_names)):
            raise ValueError("composition experiment names must be unique")
        exception_penalty = float(exception_penalty)
        if not math.isfinite(exception_penalty) or exception_penalty <= 0:
            raise ValueError("exception_penalty must be finite and positive")

        evaluations = []
        for rule in rules:
            cases = []
            for experiment in experiments:
                model = FiniteStateModel(
                    name="%s:%s" % (experiment.name, rule.name),
                    states=experiment.states,
                    initial_states=experiment.initial_states,
                    actions=experiment.actions,
                    transition=rule.transition,
                    readout=experiment.readout,
                    metrics=ModelMetrics(
                        cost=0.0,
                        complexity=rule.description_length,
                        risk=0.0,
                    ),
                    applicable=rule.applicable,
                )
                test_results = []
                counterexamples = []
                for test in experiment.tests:
                    result, counterexample = self._test_rule(
                        rule, experiment, model, test
                    )
                    test_results.append(result)
                    if counterexample is not None:
                        counterexamples.append(counterexample)

                analysis_error = None
                try:
                    residual_report = self.residual_analyzer.analyze(
                        model,
                        experiment.equivalence,
                        experiment.context,
                        max_reachability_depth=max_reachability_depth,
                        max_states=max_states,
                        max_context_depth=max_context_depth,
                        max_context_tests=max_context_tests,
                    )
                except Exception as error:
                    residual_report = None
                    analysis_error = "%s: %s" % (type(error).__name__, error)
                counterexamples.extend(
                    self._residual_counterexamples(
                        rule,
                        experiment,
                        residual_report,
                        analysis_error,
                    )
                )
                lengths = self._description_lengths(
                    residual_report,
                    test_results,
                    exception_penalty,
                    analysis_error,
                )
                cases.append(
                    CompositionCaseResult(
                        experiment_name=experiment.name,
                        model_name=model.name,
                        tests=tuple(test_results),
                        residual_report=residual_report,
                        counterexamples=tuple(counterexamples),
                        state_description_length=lengths[0],
                        transition_description_length=lengths[1],
                        context_description_length=lengths[2],
                        exception_description_length=lengths[3],
                        analysis_error=analysis_error,
                    )
                )
            evaluations.append(
                CompositionRuleEvaluation(rule=rule, cases=tuple(cases))
            )

        ranked = tuple(
            sorted(
                (item for item in evaluations if item.certified),
                key=lambda item: (
                    item.total_description_length,
                    item.rule.name,
                ),
            )
        )
        rejected = tuple(item for item in evaluations if not item.certified)
        if ranked:
            best_length = ranked[0].total_description_length
            selected = tuple(
                item
                for item in ranked
                if math.isclose(
                    item.total_description_length,
                    best_length,
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            )
        else:
            selected = ()

        boundaries = [
            "rule description lengths are caller-supplied and require one fixed codec",
            "selection is relative to the declared finite experiments and observation tests",
            "a unique shortest rule is not proof of a unique generating mechanism",
        ]
        if not ranked:
            boundaries.append("no candidate rule received a complete certificate")
        elif len(selected) > 1:
            boundaries.append(
                "multiple candidate rules have the same shortest description length"
            )
        return CompositionSelectionReport(
            experiment_names=experiment_names,
            evaluations=tuple(evaluations),
            ranked=ranked,
            rejected=rejected,
            selected=selected,
            exception_penalty=float(exception_penalty),
            boundaries=tuple(boundaries),
        )
