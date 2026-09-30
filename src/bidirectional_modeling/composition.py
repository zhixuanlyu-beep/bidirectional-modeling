"""Bounded selection of microscopic composition rules.

The selector compares candidate transition/composition rules against shared,
caller-owned operational observations.  A rule is eligible only when it fits
every declared test and its reachable residual quotient is complete, stable,
congruent, and reproducible by the extracted finite context basis.  The default
returns all certified rules without preference. The optional shortest_description
policy ranks them by an explicit two-part description-length proxy.
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
from .structural import freeze_value


@dataclass(frozen=True)
class CompositionRule:
    """One candidate microscopic composition rule under a fixed codec."""

    name: str
    transition: Transition
    description_length: Optional[float] = None
    applicable: Optional[Applicability] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("composition rule name must be non-empty")
        if not callable(self.transition):
            raise TypeError("composition rule transition must be callable")
        if self.applicable is not None and not callable(self.applicable):
            raise TypeError("composition rule applicable hook must be callable")
        if self.description_length is None:
            return
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
        if type(self.expected_defined) is not bool:
            raise TypeError("composition test support must be a boolean")
        object.__setattr__(self, "actions", tuple(self.actions))
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("composition test name must be non-empty")
        if not isinstance(self.initial_state, str) or not self.initial_state:
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
        if not isinstance(self.name, str) or not self.name:
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
                freeze_value(self.equivalence.signature(test.expected_observation),
                             purpose="composition expected observation identity")


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
    evaluation_error: Optional[str] = None


@dataclass(frozen=True)
class CompositionCaseResult:
    """One rule evaluated on one finite experiment case."""

    experiment_name: str
    model_name: str
    tests: Tuple[CompositionTestResult, ...]
    residual_report: Optional[ResidualQuotientReport]
    counterexamples: Tuple[Counterexample, ...]
    state_description_length: Optional[float]
    transition_description_length: Optional[float]
    context_description_length: Optional[float]
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
    def structural_description_length(self) -> Optional[float]:
        values = (self.state_description_length, self.transition_description_length,
                  self.context_description_length)
        return None if any(value is None for value in values) else sum(values)


@dataclass(frozen=True)
class CompositionRuleEvaluation:
    """Cross-experiment verification and optional encoding cost."""

    rule: CompositionRule
    cases: Tuple[CompositionCaseResult, ...]

    @property
    def certified(self) -> bool:
        return bool(self.cases) and all(case.certified for case in self.cases)

    @property
    def total_description_length(self) -> Optional[float]:
        lengths = tuple(case.structural_description_length for case in self.cases)
        if self.rule.description_length is None or any(length is None for length in lengths):
            return None
        return self.rule.description_length + sum(lengths)

    @property
    def counterexamples(self) -> Tuple[Counterexample, ...]:
        return tuple(
            counterexample
            for case in self.cases
            for counterexample in case.counterexamples
        )


@dataclass(frozen=True)
class CompositionSelectionReport:
    """Certified, refuted and undecided rules, with optional preference."""

    experiment_names: Tuple[str, ...]
    evaluations: Tuple[CompositionRuleEvaluation, ...]
    ranked: Tuple[CompositionRuleEvaluation, ...]
    rejected: Tuple[CompositionRuleEvaluation, ...]
    selected: Tuple[CompositionRuleEvaluation, ...]
    undecided: Tuple[CompositionRuleEvaluation, ...] = ()
    selection_policy: Optional[str] = None
    boundaries: Tuple[str, ...] = ()

    @property
    def certified(self) -> Tuple[CompositionRuleEvaluation, ...]:
        return tuple(item for item in self.evaluations if item.certified)

    @property
    def unique_selection(self) -> bool:
        """Whether one rule is uniquely shortest under the declared protocol."""

        return len(self.selected) == 1

    @property
    def selected_rule_names(self) -> Tuple[str, ...]:
        return tuple(item.rule.name for item in self.selected)


class CompositionRuleSelector:
    """Verify finite rules and optionally select among certified quotients."""

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
                state = dict(model.audited_step(state, action, experiment.context))
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
                observation = model.audited_observe(state, experiment.context)
                actual_signature = experiment.equivalence.signature(observation)
            except Exception as error:
                actual_defined = None
                error_detail = "%s: %s" % (type(error).__name__, error)

        passed = bool(
            error_detail is None
            and actual_defined == test.expected_defined
            and (
                not test.expected_defined
                or freeze_value(actual_signature) == freeze_value(expected_signature)
            )
        )
        if passed:
            detail = "matched the declared operational outcome"
            counterexample = None
        elif error_detail is not None:
            detail = "composition rule raised an uncertified error: %s" % error_detail
            counterexample = None
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
            evaluation_error=error_detail,
        )
        return result, counterexample

    @staticmethod
    def _residual_counterexamples(
        rule: CompositionRule,
        experiment: CompositionExperiment,
        report: Optional[ResidualQuotientReport],
    ) -> Tuple[Counterexample, ...]:
        if report is None:
            return ()
        counterexamples = []
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
                            "refine the semantic state before judging this partition",
                        ),
                    )
                )
        return tuple(counterexamples)

    @staticmethod
    def _description_lengths(report):
        if report is None:
            return None, None, None
        class_count = report.quotient.class_count
        action_count = len(report.quotient.actions)
        state_width = max(1, class_count.bit_length())
        action_width = max(1, action_count.bit_length())
        return (float(class_count * state_width),
                float(class_count * action_count * (1 + state_width)),
                float(sum(1 + len(word) * action_width for word in report.context_basis)))

    def select(
        self,
        rules: Sequence[CompositionRule],
        experiments: Sequence[CompositionExperiment],
        max_reachability_depth: Optional[int] = None,
        max_states: int = 1_000,
        max_context_depth: Optional[int] = None,
        max_context_tests: int = 256,
        *, selection_policy: Optional[str] = None, full_diagnostics: bool = False,
    ) -> CompositionSelectionReport:
        """Verify all rules; apply a preference only when explicitly requested."""

        rules = tuple(rules)
        experiments = tuple(experiments)
        if not rules:
            raise ValueError("at least one composition rule is required")
        if not experiments:
            raise ValueError("at least one composition experiment is required")
        if type(max_states) is not int or max_states < 1:
            raise ValueError("max_states must be positive")
        if max_reachability_depth is not None and (type(max_reachability_depth) is not int or max_reachability_depth < 0):
            raise ValueError("max_reachability_depth must be non-negative")
        if max_context_depth is not None and (type(max_context_depth) is not int or max_context_depth < 0):
            raise ValueError("max_context_depth must be non-negative")
        if type(max_context_tests) is not int or max_context_tests < 1:
            raise ValueError("max_context_tests must be positive")
        rule_names = tuple(rule.name for rule in rules)
        if len(rule_names) != len(set(rule_names)):
            raise ValueError("composition rule names must be unique")
        experiment_names = tuple(experiment.name for experiment in experiments)
        if len(experiment_names) != len(set(experiment_names)):
            raise ValueError("composition experiment names must be unique")
        if selection_policy == "shortest_description" and any(rule.description_length is None for rule in rules):
            raise ValueError("shortest_description requires a declared rule encoding length")
        if selection_policy not in (None, "shortest_description"):
            raise ValueError("unknown composition selection policy")

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
                        complexity=0.0,  # Verification does not depend on a selection metric.
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
                        if not full_diagnostics:
                            break

                operational_refuted = bool(counterexamples)
                analysis_error = None
                residual_report = None
                # A reliable operational witness already decides rejection.
                if not operational_refuted or full_diagnostics:
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
                        if (not isinstance(residual_report, ResidualQuotientReport)
                                or residual_report.model_name != model.name
                                or not residual_report.binds_context(experiment.context)
                                or not residual_report.binds_equivalence(experiment.equivalence)
                                or (residual_report.max_reachability_depth, residual_report.max_states,
                                    residual_report.max_context_depth, residual_report.max_context_tests)
                                != (max_reachability_depth, max_states, max_context_depth, max_context_tests)):
                            raise ValueError("residual report does not bind this composition analysis")
                    except Exception as error:
                        residual_report = None
                        analysis_error = "%s: %s" % (type(error).__name__, error)
                counterexamples.extend(
                    self._residual_counterexamples(
                        rule,
                        experiment,
                        residual_report,
                    )
                )
                lengths = (self._description_lengths(residual_report)
                           if selection_policy == "shortest_description" else (None, None, None))
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
                        analysis_error=analysis_error,
                    )
                )
                if operational_refuted and not full_diagnostics:
                    break
            evaluations.append(
                CompositionRuleEvaluation(rule=rule, cases=tuple(cases))
            )

        certified = tuple(item for item in evaluations if item.certified)
        rejected = tuple(item for item in evaluations if any(
            not test.passed and test.evaluation_error is None
            for case in item.cases for test in case.tests))
        rejected_names = {item.rule.name for item in rejected}
        undecided = tuple(item for item in evaluations
                          if not item.certified and item.rule.name not in rejected_names)
        ranked = tuple(sorted(certified, key=lambda item: (
            item.total_description_length if selection_policy else 0, item.rule.name)))
        selected = ()
        if selection_policy == "shortest_description" and ranked:
            best_length = ranked[0].total_description_length
            selected = tuple(item for item in ranked if item.total_description_length == best_length)

        boundaries = [
            "rule description lengths are caller-supplied and require one fixed codec",
            "selection is relative to the declared finite experiments and observation tests",
            "a unique shortest rule is not proof of a unique generating mechanism",
        ]
        if not ranked:
            boundaries.append("no candidate rule received a complete certificate")
        elif selection_policy and len(selected) > 1:
            boundaries.append(
                "multiple candidate rules have the same shortest description length"
            )
        return CompositionSelectionReport(
            experiment_names=experiment_names,
            evaluations=tuple(evaluations),
            ranked=ranked,
            rejected=rejected,
            selected=selected,
            undecided=undecided,
            selection_policy=selection_policy,
            boundaries=tuple(boundaries),
        )

