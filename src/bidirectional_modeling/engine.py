"""Facade and semantic/behavioral round-trip checks."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

from .core import (
    ClosureReport,
    Concept,
    Context,
    Evidence,
    ExecutableModel,
    Experiment,
    FiniteStateModel,
    InterpretationResult,
    MacroSpec,
    PurposeHypothesis,
    RealizationResult,
    ResourceBudget,
)
from .interpretation import HypothesisSource, Interpreter
from .realization import CandidateSource, Realizer
from .refinement import ClosureAnalyzer, ConceptLibrary


@dataclass(frozen=True)
class MacroRoundTripReport:
    realization: RealizationResult
    interpretations: Tuple[InterpretationResult, ...]
    semantic_preservation: Tuple[bool, ...]
    simulations_used: int = 0
    truncated: bool = False

    @property
    def passed(self) -> bool:
        return (
            not self.truncated
            and bool(self.semantic_preservation)
            and all(self.semantic_preservation)
        )


@dataclass(frozen=True)
class MicroRoundTripReport:
    interpretation: InterpretationResult
    realization: RealizationResult
    behaviorally_equivalent_models: Tuple[str, ...]
    simulations_used: int = 0
    truncated: bool = False

    @property
    def passed(self) -> bool:
        return not self.truncated and bool(self.behaviorally_equivalent_models)


@dataclass(frozen=True)
class RefinementStep:
    iteration: int
    spec: MacroSpec
    model_name: str
    closure_report: ClosureReport
    accepted_feature: Optional[str]


@dataclass(frozen=True)
class RefinementLoopReport:
    initial_spec: MacroSpec
    final_spec: MacroSpec
    final_model: FiniteStateModel
    steps: Tuple[RefinementStep, ...]
    closed: bool
    stopped_reason: str


def _traces_behaviorally_equivalent(left_traces, right_traces, spec: MacroSpec) -> bool:
    def grouped_traces(traces) -> Dict[Tuple[str, str], list]:
        result: Dict[Tuple[str, str], list] = defaultdict(list)
        for trace in traces:
            result[(trace.initial_state, trace.intervention)].append(trace)
        return result

    def trace_equivalent(left_trace, right_trace) -> bool:
        if len(left_trace.snapshots) != len(right_trace.snapshots):
            return False
        return all(
            spec.equivalence.equivalent(left_snapshot, right_snapshot)
            for left_snapshot, right_snapshot in zip(
                left_trace.snapshots, right_trace.snapshots
            )
        )

    def has_perfect_matching(left_traces, right_traces) -> bool:
        if len(left_traces) != len(right_traces):
            return False
        matches = {}

        def augment(left_index: int, visited: set) -> bool:
            for right_index, right_trace in enumerate(right_traces):
                if right_index in visited or not trace_equivalent(
                    left_traces[left_index], right_trace
                ):
                    continue
                visited.add(right_index)
                if right_index not in matches or augment(matches[right_index], visited):
                    matches[right_index] = left_index
                    return True
            return False

        return all(augment(index, set()) for index in range(len(left_traces)))

    left_groups = grouped_traces(left_traces)
    right_groups = grouped_traces(right_traces)
    if set(left_groups) != set(right_groups):
        return False
    return all(
        has_perfect_matching(left_groups[name], right_groups[name])
        for name in left_groups
    )


def behaviorally_equivalent(
    left: ExecutableModel,
    right: ExecutableModel,
    spec: MacroSpec,
    context: Context,
) -> bool:
    return _traces_behaviorally_equivalent(
        tuple(left.simulate(context, spec.horizon)),
        tuple(right.simulate(context, spec.horizon)),
        spec,
    )


class BidirectionalModelingEngine:
    def __init__(
        self,
        realizer: Optional[Realizer] = None,
        interpreter: Optional[Interpreter] = None,
        concept_library: Optional[ConceptLibrary] = None,
    ) -> None:
        self.realizer = realizer or Realizer()
        self.interpreter = interpreter or Interpreter(self.realizer.evaluator)
        self.closure = ClosureAnalyzer()
        self.concepts = concept_library or ConceptLibrary()

    def realize(
        self,
        spec: MacroSpec,
        context: Context,
        source: CandidateSource,
        budget: Optional[ResourceBudget] = None,
    ) -> RealizationResult:
        return self.realizer.realize(spec, context, source, budget)

    def interpret(
        self,
        model: ExecutableModel,
        context: Context,
        hypotheses: HypothesisSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> InterpretationResult:
        return self.interpreter.interpret(
            model, context, hypotheses, evidence, experiments, budget
        )

    def check_closure(
        self,
        model: FiniteStateModel,
        spec: MacroSpec,
        context: Context,
        max_depth: Optional[int] = None,
        max_states: int = 1_000,
    ):
        return self.closure.analyze(model, spec, context, max_depth, max_states)

    def refine_until_closed(
        self,
        model: FiniteStateModel,
        spec: MacroSpec,
        context: Context,
        feature_selector: Callable[
            [ClosureReport, MacroSpec, FiniteStateModel], Optional[str]
        ],
        concept_name: Optional[str] = None,
        max_iterations: int = 8,
        max_depth: Optional[int] = None,
        max_states: int = 1_000,
    ) -> RefinementLoopReport:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        current_model = model
        current_spec = spec
        steps = []
        for iteration in range(1, max_iterations + 1):
            report = self.check_closure(
                current_model, current_spec, context, max_depth, max_states
            )
            if report.closed:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                return RefinementLoopReport(
                    spec, current_spec, current_model, tuple(steps), True, "closed"
                )
            if not report.complete:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                return RefinementLoopReport(
                    spec,
                    current_spec,
                    current_model,
                    tuple(steps),
                    False,
                    "closure-analysis-budget-exhausted",
                )
            if not report.suggested_features:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                return RefinementLoopReport(
                    spec,
                    current_spec,
                    current_model,
                    tuple(steps),
                    False,
                    "no-separating-feature",
                )
            selected = feature_selector(report, current_spec, current_model)
            if selected is None:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                return RefinementLoopReport(
                    spec, current_spec, current_model, tuple(steps), False, "not-approved"
                )
            if selected not in report.suggested_features:
                raise ValueError(
                    "selected feature %r is not among the closure separators %r"
                    % (selected, report.suggested_features)
                )
            steps.append(
                RefinementStep(
                    iteration, current_spec, current_model.name, report, selected
                )
            )
            if concept_name and report.counterexamples:
                try:
                    self.concepts.get(concept_name)
                except KeyError:
                    self.concepts.add(
                        Concept(
                            concept_name,
                            "task-relative equivalence for %s" % spec.name,
                        )
                    )
                self.concepts.refine_from_counterexample(
                    concept_name, report.counterexamples[0]
                )
            current_spec = current_spec.promote_observable(selected)
            current_model = current_model.with_promoted_observables((selected,))

        final_report = self.check_closure(
            current_model, current_spec, context, max_depth, max_states
        )
        steps.append(
            RefinementStep(
                max_iterations + 1,
                current_spec,
                current_model.name,
                final_report,
                None,
            )
        )
        if final_report.closed:
            stopped_reason = "closed"
        elif not final_report.complete:
            stopped_reason = "closure-analysis-budget-exhausted"
        else:
            stopped_reason = "max-iterations-reached"
        return RefinementLoopReport(
            spec,
            current_spec,
            current_model,
            tuple(steps),
            final_report.closed,
            stopped_reason,
        )

    def macro_round_trip(
        self,
        spec: MacroSpec,
        context: Context,
        source: CandidateSource,
        hypotheses: Iterable[PurposeHypothesis],
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> MacroRoundTripReport:
        budget = budget or ResourceBudget()
        realization = self.realize(spec, context, source, budget)
        interpretations = []
        preservation = []
        hypotheses = tuple(hypotheses)
        simulations_used = realization.simulations_used
        remaining_simulations = max(
            0, budget.max_simulations - simulations_used
        )
        truncated = realization.truncated
        for candidate in realization.candidates:
            if remaining_simulations <= 0:
                result = InterpretationResult(
                    model_name=candidate.model.name,
                    candidates=(),
                    equivalent_explanations=(),
                    discriminating_query=None,
                    non_identifiable=False,
                    truncated=True,
                )
            else:
                interpretation_budget = replace(
                    budget, max_simulations=remaining_simulations
                )
                result = self.interpret(
                    candidate.model,
                    context,
                    hypotheses,
                    evidence,
                    experiments,
                    interpretation_budget,
                )
                simulations_used += result.simulations_used
                remaining_simulations -= result.simulations_used
            interpretations.append(result)
            truncated = truncated or result.truncated
            preservation.append(
                any(
                    item.hypothesis.spec.semantically_equivalent(spec)
                    for item in result.candidates
                )
            )
        return MacroRoundTripReport(
            realization,
            tuple(interpretations),
            tuple(preservation),
            simulations_used,
            truncated,
        )

    def micro_round_trip(
        self,
        model: ExecutableModel,
        context: Context,
        hypotheses: Iterable[PurposeHypothesis],
        realization_source: CandidateSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> MicroRoundTripReport:
        budget = budget or ResourceBudget()
        interpretation = self.interpret(
            model, context, hypotheses, evidence, experiments, budget
        )
        if not interpretation.candidates:
            raise ValueError("no compatible macro hypothesis can seed the return realization")
        top_spec = interpretation.candidates[0].hypothesis.spec
        simulations_used = interpretation.simulations_used
        remaining_simulations = max(
            0, budget.max_simulations - simulations_used
        )
        truncated = interpretation.truncated
        if remaining_simulations <= 0:
            realization = RealizationResult(
                spec=top_spec,
                candidates=(),
                rejected=(),
                dominated=(),
                searched_candidates=0,
                truncated=True,
                simulations_used=0,
            )
        else:
            realization_budget = replace(
                budget, max_simulations=remaining_simulations
            )
            realization = self.realize(
                top_spec, context, realization_source, realization_budget
            )
            simulations_used += realization.simulations_used
            remaining_simulations -= realization.simulations_used
        truncated = truncated or realization.truncated
        satisfying = realization.candidates + realization.dominated
        equivalent = []
        original_batch = None
        if satisfying and remaining_simulations > 0:
            trace_budget = replace(
                budget, max_simulations=remaining_simulations
            )
            original_batch = self.realizer.evaluator.collect(
                model, context, top_spec.horizon, trace_budget
            )
            simulations_used += original_batch.simulations_used
            remaining_simulations -= original_batch.simulations_used
            if not original_batch.complete:
                truncated = True

        compared = 0
        if original_batch is not None and original_batch.complete:
            for item in satisfying:
                if item.model is model:
                    candidate_batch = original_batch
                else:
                    if remaining_simulations <= 0:
                        truncated = True
                        break
                    trace_budget = replace(
                        budget, max_simulations=remaining_simulations
                    )
                    candidate_batch = self.realizer.evaluator.collect(
                        item.model, context, top_spec.horizon, trace_budget
                    )
                    simulations_used += candidate_batch.simulations_used
                    remaining_simulations -= candidate_batch.simulations_used
                compared += 1
                if not candidate_batch.complete:
                    truncated = True
                    continue
                if _traces_behaviorally_equivalent(
                    original_batch.traces, candidate_batch.traces, top_spec
                ):
                    equivalent.append(item.model.name)
        if compared < len(satisfying):
            truncated = True
        return MicroRoundTripReport(
            interpretation,
            realization,
            tuple(equivalent),
            simulations_used,
            truncated,
        )
