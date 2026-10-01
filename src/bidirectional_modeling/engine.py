"""Facade and semantic/behavioral round-trip checks."""

from __future__ import annotations

from .structural import ordered_tuple
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

from .correspondence import (
    Correspondence,
    CorrespondenceCertificate,
    CorrespondenceSuiteCertificate,
    CorrespondenceValidationCase,
    CorrespondenceValidator,
    ScaleGraph,
)
from .core import (
    ClosureReport,
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
    VerificationIssue,
)
from .evaluation import _checked_trace_batch
from .interpretation import HypothesisSource, Interpreter
from .realization import CandidateSource, Realizer


@dataclass(frozen=True)
class MacroRoundTripReport:
    realization: RealizationResult
    interpretations: Tuple[InterpretationResult, ...]
    semantic_preservation: Tuple[bool, ...]
    simulations_used: int = 0
    truncated: bool = False
    independence_declared: bool = False
    generation_source: str = "catalogue"

    @property
    def compatibility_passed(self) -> bool:
        return (
            not self.truncated
            and not self.realization.undecided
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
    selected_hypothesis: Optional[str] = None
    diagnostics: Tuple[VerificationIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not (self.truncated or self.diagnostics) and bool(self.behaviorally_equivalent_models)


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


def _batches_behaviorally_equivalent(left, right, left_batch, right_batch, spec, context):
    if not (left_batch.complete and right_batch.complete
            and left_batch.traces and right_batch.traces
            and left_batch.binds(left, context, spec.horizon)
            and right_batch.binds(right, context, spec.horizon)):
        return None
    return _traces_behaviorally_equivalent(left_batch.traces, right_batch.traces, spec)


def _collect_comparison_batch(evaluator, model, context, spec, budget):
    try:
        batch = evaluator.collect(model, context, spec.horizon, budget)
        batch = _checked_trace_batch(batch, model, context, spec.horizon, budget.max_simulations)
        return batch, batch.simulations_used, None
    except Exception as error:
        issue = VerificationIssue('round-trip-comparison', '%s: %s' % (type(error).__name__, error),
                                  {'reserved_simulations': budget.max_simulations})
        return None, budget.max_simulations, issue


def behaviorally_equivalent(
    left: ExecutableModel,
    right: ExecutableModel,
    spec: MacroSpec,
    context: Context,
    *, budget: Optional[ResourceBudget] = None, evaluator=None,
) -> Optional[bool]:
    """Compare complete bound batches; None means verification is unresolved."""
    from .evaluation import SatisfactionEvaluator
    evaluator = evaluator or SatisfactionEvaluator()
    budget = budget or ResourceBudget()
    left_batch, used, _ = _collect_comparison_batch(evaluator, left, context, spec, budget)
    remaining = budget.max_simulations - used
    if left_batch is None or not left_batch.complete or remaining <= 0:
        return None
    right_batch, _, _ = _collect_comparison_batch(evaluator, right, context, spec,
                                               replace(budget, max_simulations=remaining))
    if right_batch is None:
        return None
    try:
        return _batches_behaviorally_equivalent(left, right, left_batch, right_batch, spec, context)
    except Exception:
        return None


class BidirectionalModelingEngine:
    def __init__(
        self,
        realizer: Optional[Realizer] = None,
        interpreter: Optional[Interpreter] = None,
        correspondence_validator: Optional[CorrespondenceValidator] = None,
        scale_graph: Optional[ScaleGraph] = None,
    ) -> None:
        self.realizer = realizer or Realizer()
        self.interpreter = interpreter or Interpreter(self.realizer.evaluator)
        self._correspondence_validator = correspondence_validator
        self._scale_graph = scale_graph

    @property
    def correspondence_validator(self):
        if self._correspondence_validator is None:
            self._correspondence_validator = CorrespondenceValidator(self.realizer.evaluator)
        return self._correspondence_validator

    @property
    def scale_graph(self):
        if self._scale_graph is None:
            self._scale_graph = ScaleGraph()
        return self._scale_graph

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
        *, observations=(),
    ) -> InterpretationResult:
        return self.interpreter.interpret(
            model, context, hypotheses, evidence, experiments, budget, observations=observations
        )

    def verify_correspondence(
        self,
        correspondence: Correspondence,
        lower_model: ExecutableModel,
        upper_model: ExecutableModel,
        lower_context: Context,
        upper_context: Optional[Context] = None,
        horizon: int = 1,
        budget: Optional[ResourceBudget] = None,
        record: bool = True,
    ) -> CorrespondenceCertificate:
        """Validate one adjacent-scale link and record it only when proven."""

        certificate = self.correspondence_validator.validate(
            correspondence,
            lower_model,
            upper_model,
            lower_context,
            upper_context,
            horizon,
            budget,
        )
        if record and certificate.passed:
            self.scale_graph.add_verified(correspondence, certificate)
        return certificate

    def verify_correspondence_suite(
        self,
        correspondence: Correspondence,
        cases: Iterable[CorrespondenceValidationCase],
        budget: Optional[ResourceBudget] = None,
        record: bool = True,
    ) -> CorrespondenceSuiteCertificate:
        """Require complete suite coverage plus an independent holdout case."""

        certificate = self.correspondence_validator.validate_suite(
            correspondence,
            cases,
            budget,
        )
        if record and certificate.passed:
            self.scale_graph.add_verified(correspondence, certificate)
        return certificate

    def refine_until_closed(
        self,
        model: FiniteStateModel,
        spec: MacroSpec,
        context: Context,
        feature_selector: Callable[
            [ClosureReport, MacroSpec, FiniteStateModel], Optional[str]
        ],
        max_iterations: int = 8,
        max_depth: Optional[int] = None,
        max_states: int = 1_000,
    ) -> RefinementLoopReport:
        if max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        from .refinement import ClosureAnalyzer
        analyzer = ClosureAnalyzer()
        current_model = model
        current_spec = spec
        steps = []
        for iteration in range(1, max_iterations + 1):
            report = analyzer.analyze(
                current_model, current_spec, context, max_depth, max_states
            )
            if report.closed:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                return RefinementLoopReport(
                    spec, current_spec, current_model, tuple(steps), True, "closed"
                )
            if not report.suggested_features:
                steps.append(
                    RefinementStep(iteration, current_spec, current_model.name, report, None)
                )
                reason = (
                    "closure-analysis-undecided"
                    if not report.complete
                    else "no-separating-feature"
                )
                return RefinementLoopReport(
                    spec,
                    current_spec,
                    current_model,
                    tuple(steps),
                    False,
                    reason,
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
            current_spec = current_spec.promote_observable(selected)
            current_model = current_model.with_promoted_observables((selected,))

        final_report = analyzer.analyze(
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
            stopped_reason = "closure-analysis-undecided"
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
        hypotheses: HypothesisSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
        *, observations=(),
    ) -> MacroRoundTripReport:
        """Check catalogue-relative recovery compatibility and report generation provenance."""

        budget = budget or ResourceBudget()
        experiments, observations = ordered_tuple(experiments), ordered_tuple(observations)
        evidence = ordered_tuple(evidence)
        realization = self.realize(spec, context, source, budget)
        interpretations = []
        preservation = []
        independence_declared = bool(
            getattr(hypotheses, "independence_declared", False)
        )
        hypothesis_source: HypothesisSource = hypotheses
        source_truncated, source_diagnostics = False, ()
        if not (hasattr(hypotheses, "generate") or hasattr(hypotheses, "generate_from_traces")):
            if realization.candidates and realization.simulations_used < budget.max_simulations:
                from ._generation import CandidateStream
                stream = CandidateStream(lambda: hypotheses, budget.max_candidates)
                hypothesis_source = tuple(stream)
                source_truncated = not stream.complete
                source_diagnostics = tuple(stream.diagnostics)
            else:
                hypothesis_source = ()
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
                    truncated=True,
                )
            else:
                interpretation_budget = replace(
                    budget, max_simulations=remaining_simulations
                )
                result = self.interpret(
                    candidate.model,
                    context,
                    hypothesis_source,
                    evidence,
                    experiments,
                    interpretation_budget, observations=observations,
                )
                simulations_used += result.simulations_used
                remaining_simulations -= result.simulations_used
            if source_truncated:
                result = replace(result, truncated=True,
                                 diagnostics=result.diagnostics + source_diagnostics)
            interpretations.append(result)
            truncated = truncated or result.truncated
            preservation.append(
                not result.undecided and not result.truncated and any(
                    item.hypothesis.spec.semantically_equivalent(spec)
                    and candidate.certificate.binds_specification(spec)
                    and candidate.certificate.binds_context(context)
                    and candidate.certificate.model_fingerprint == item.certificate.model_fingerprint
                    for item in result.candidates
                )
            )
        return MacroRoundTripReport(
            realization=realization,
            interpretations=tuple(interpretations),
            semantic_preservation=tuple(preservation),
            simulations_used=simulations_used,
            truncated=truncated,
            independence_declared=independence_declared,
            generation_source=("trace-derived" if callable(getattr(hypotheses, "generate_from_traces", None))
                               else "generator" if hasattr(hypotheses, "generate") else "catalogue"),
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
        allow_identity: bool = False,
        *, selected_hypothesis: Optional[str] = None, observations=(),
    ) -> MicroRoundTripReport:
        """Re-realize inferred behavior; exclude the original object by default."""

        budget = budget or ResourceBudget()
        interpretation = self.interpret(
            model, context, hypotheses, evidence, experiments, budget, observations=observations
        )
        if not interpretation.candidates:
            raise ValueError("no compatible macro hypothesis can seed the return realization")
        if selected_hypothesis is None:
            if interpretation.non_identifiable:
                raise ValueError('select an explicit compatible hypothesis for an ambiguous or undecided interpretation')
            chosen = interpretation.candidates[0]
        else:
            chosen = next((c for c in interpretation.candidates
                           if c.hypothesis.name == selected_hypothesis), None)
            if chosen is None:
                raise ValueError('selected hypothesis is not a verified compatible candidate')
        selected_spec = chosen.hypothesis.spec
        simulations_used = interpretation.simulations_used
        remaining_simulations = max(
            0, budget.max_simulations - simulations_used
        )
        truncated = interpretation.truncated
        if remaining_simulations <= 0:
            realization = RealizationResult(
                spec=selected_spec,
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
                selected_spec, context, realization_source, realization_budget
            )
            simulations_used += realization.simulations_used
            remaining_simulations -= realization.simulations_used
        truncated = truncated or realization.truncated
        satisfying = tuple(
            item
            for item in realization.candidates + realization.dominated
            if allow_identity or item.model is not model
        )
        equivalent = []
        diagnostics = []
        original_batch = None
        if satisfying and remaining_simulations > 0:
            trace_budget = replace(
                budget, max_simulations=remaining_simulations
            )
            original_batch, used, issue = _collect_comparison_batch(
                self.realizer.evaluator, model, context, selected_spec, trace_budget
            )
            simulations_used += used
            remaining_simulations -= used
            if issue is not None:
                diagnostics.append(issue)
            if original_batch is None or not original_batch.complete:
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
                    candidate_batch, used, issue = _collect_comparison_batch(
                        self.realizer.evaluator, item.model, context, selected_spec, trace_budget
                    )
                    simulations_used += used
                    remaining_simulations -= used
                    if issue is not None:
                        diagnostics.append(issue)
                compared += 1
                if candidate_batch is None or not candidate_batch.complete:
                    truncated = True
                    continue
                try:
                    if not (chosen.certificate.binds_specification(selected_spec)
                            and chosen.certificate.binds_context(context)
                            and chosen.certificate.binds_evidence(model, original_batch.traces)
                            and item.certificate.binds_specification(selected_spec)
                            and item.certificate.binds_context(context)
                            and item.certificate.binds_evidence(item.model, candidate_batch.traces)):
                        raise ValueError('round-trip satisfaction evidence changed before behavioral comparison')
                    comparison = _batches_behaviorally_equivalent(
                        model, item.model, original_batch, candidate_batch, selected_spec, context)
                except Exception as error:
                    diagnostics.append(VerificationIssue('round-trip-binding', str(error)))
                    comparison = None
                if comparison is None:
                    truncated = True
                elif comparison:
                    equivalent.append(item.model.name)
        if compared < len(satisfying):
            truncated = True
        return MicroRoundTripReport(
            interpretation,
            realization,
            tuple(equivalent),
            simulations_used,
            truncated,
            chosen.hypothesis.name,
            tuple(diagnostics),
        )
