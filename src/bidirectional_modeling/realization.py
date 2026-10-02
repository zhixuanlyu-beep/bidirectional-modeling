"""Macro-purpose -> micro-structure search with Pareto selection."""

from __future__ import annotations

from .structural import ordered_tuple
from dataclasses import replace
from itertools import product
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence, Union

from .core import (
    CandidateEvaluation,
    Context,
    Counterexample,
    VerificationIssue,
    ExecutableModel,
    MacroSpec,
    ProbeOutcome,
    RealizationResult,
    ResourceBudget,
    SatisfactionCertificate,
)
from .evaluation import SatisfactionEvaluator, _checked_evaluator_result, _checked_satisfaction_result
from ._generation import CandidateStream
from .provenance import (safe_macro_spec_fingerprint, safe_context_fingerprint,
                         _model_input_snapshot, _model_input_unchanged)


def _probe_flags(probes):
    flags = tuple(getattr(probe, 'blocking', True) for probe in probes)
    if any(type(flag) is not bool for flag in flags):
        raise TypeError('probe blocking must be an explicit boolean')
    return flags


class CandidateGenerator(Protocol):
    def generate(
        self, spec: MacroSpec, context: Context, budget: ResourceBudget
    ) -> Iterable[ExecutableModel]:
        ...


class RedTeamProbe(Protocol):
    def probe(
        self,
        model: ExecutableModel,
        spec: MacroSpec,
        context: Context,
        evaluator: SatisfactionEvaluator,
        budget: ResourceBudget,
    ) -> ProbeOutcome:
        ...


CandidateSource = Union[CandidateGenerator, Iterable[ExecutableModel]]


class RegistryGenerator:
    """A minimal generator for a known design library."""

    def __init__(self, models: Iterable[ExecutableModel]) -> None:
        self.models = ordered_tuple(models)

    def generate(
        self, spec: MacroSpec, context: Context, budget: ResourceBudget
    ) -> Iterable[ExecutableModel]:
        # Preserve the concrete finite collection so Realizer can distinguish
        # exact completion from candidate-limit truncation.
        return self.models


class ParametricCandidateGenerator:
    """Synthesizes candidate structures from a finite parameter design space."""

    def __init__(
        self,
        parameter_space: Mapping[str, Sequence[Any]],
        factory: Callable[[Mapping[str, Any], MacroSpec, Context], ExecutableModel],
    ) -> None:
        if any(not values for values in parameter_space.values()):
            raise ValueError("every parameter must have at least one candidate value")
        self.parameter_space = {name: ordered_tuple(values) for name, values in parameter_space.items()}
        self.factory = factory

    def generate(
        self, spec: MacroSpec, context: Context, budget: ResourceBudget
    ) -> Iterable[ExecutableModel]:
        names = tuple(sorted(self.parameter_space))
        value_sets = tuple(self.parameter_space[name] for name in names)
        for values in product(*value_sets):
            parameters = dict(zip(names, values))
            yield self.factory(parameters, spec, context)


def _dominates(left: CandidateEvaluation, right: CandidateEvaluation) -> bool:
    left_metrics = left.model.metrics.as_tuple()
    right_metrics = right.model.metrics.as_tuple()
    no_worse = all(a <= b for a, b in zip(left_metrics, right_metrics))
    strictly_better = any(a < b for a, b in zip(left_metrics, right_metrics))
    return no_worse and strictly_better


def pareto_partition(
    evaluations: Sequence[CandidateEvaluation],
) -> tuple[tuple[CandidateEvaluation, ...], tuple[CandidateEvaluation, ...]]:
    frontier = []
    dominated = []
    for candidate in evaluations:
        if any(_dominates(other, candidate) for other in evaluations if other is not candidate):
            dominated.append(candidate)
        else:
            frontier.append(candidate)
    frontier.sort(key=lambda item: (item.model.metrics.as_tuple(), item.model.name))
    dominated.sort(key=lambda item: (item.model.metrics.as_tuple(), item.model.name))
    return tuple(frontier), tuple(dominated)


class Realizer:
    def __init__(
        self,
        evaluator: Optional[SatisfactionEvaluator] = None,
        probes: Sequence[RedTeamProbe] = (),
    ) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()
        self.probes = ordered_tuple(probes)
        _probe_flags(self.probes)

    def realize(
        self,
        spec: MacroSpec,
        context: Context,
        source: CandidateSource,
        budget: Optional[ResourceBudget] = None,
    ) -> RealizationResult:
        budget = budget or ResourceBudget()
        required_flags = _probe_flags(self.probes)
        initial_scope = (safe_macro_spec_fingerprint(spec), safe_context_fingerprint(context))
        def factory():
            return source.generate(spec, context, budget) if hasattr(source, "generate") else source
        stream = CandidateStream(factory, budget.max_candidates)

        accepted = []
        rejected = []
        undecided = []
        searched = 0
        truncated = False
        simulations_used = 0
        remaining_simulations = budget.max_simulations
        while searched < budget.max_candidates and remaining_simulations > 0:
            try:
                model = next(stream)
            except StopIteration:
                break
            searched += 1
            input_snapshot = _model_input_snapshot(model)
            candidate_budget = replace(budget, max_simulations=remaining_simulations)
            reserved = 0
            try:
                certificate = self.evaluator.evaluate(
                    model, spec, context, candidate_budget
                )
                certificate = _checked_satisfaction_result(certificate, model, spec, context,
                                                           candidate_budget)
            except Exception as error:
                certificate = SatisfactionEvaluator().failure_certificate(
                    model, spec, context, str(error), candidate_budget
                )
                # No reliable accounting survived this evaluator failure.
                reserved = remaining_simulations
                simulations_used += reserved
                remaining_simulations = 0
                truncated = True
            simulations_used += certificate.simulations_used
            remaining_simulations -= certificate.simulations_used
            counterexamples = []
            probe_certificates = []
            diagnostics = []
            if reserved:
                diagnostics.append(VerificationIssue('base-budget',
                    'evaluator consumption unknown; remaining allowance reserved',
                    {'reserved_simulations': reserved}))
            unresolved = not certificate.complete
            if not certificate.complete:
                diagnostics.append(VerificationIssue("base-verification", "; ".join(
                    tuple(c.evaluation_error for c in certificate.checks if c.evaluation_error)
                    + certificate.failure_boundaries) or "verification incomplete"))
            if certificate.satisfied:
                for probe_index, probe in enumerate(self.probes):
                    required = required_flags[probe_index]
                    if remaining_simulations <= 0:
                        diagnostics.append(VerificationIssue(
                            "probe-budget", "configured probes could not all run"))
                        unresolved = unresolved or any(required_flags[probe_index:])
                        truncated = True
                        break
                    probe_budget = replace(budget, max_simulations=remaining_simulations)
                    try:
                        outcome = probe.probe(
                            model, spec, context, self.evaluator, probe_budget
                        )
                        if (not isinstance(outcome, ProbeOutcome)
                                or (outcome.certificate is not None
                                    and not isinstance(outcome.certificate, SatisfactionCertificate))
                                or (outcome.counterexample is not None
                                    and not isinstance(outcome.counterexample, Counterexample))
                                or not isinstance(outcome.diagnostics, tuple)
                                or any(not isinstance(d, VerificationIssue) for d in outcome.diagnostics)):
                            raise TypeError('probe returned a malformed outcome')
                        outcome = _checked_evaluator_result(outcome, ProbeOutcome, remaining_simulations)
                        if outcome.certificate is not None:
                            _checked_satisfaction_result(outcome.certificate, model, None, context,
                                                         probe_budget)
                    except Exception as error:
                        diagnostics.append(VerificationIssue(
                            "probe-execution", str(error), {"probe": type(probe).__name__}))
                        unresolved = unresolved or any(required_flags[probe_index:])
                        # An arbitrary probe may have consumed any portion of the
                        # budget before raising. Fail closed and reserve all of
                        # the remaining allowance instead of risking overspend.
                        simulations_used += remaining_simulations
                        remaining_simulations = 0
                        truncated = True
                        break
                    simulations_used += outcome.simulations_used
                    remaining_simulations -= outcome.simulations_used
                    if (outcome.certificate is None and outcome.counterexample is None
                            and not outcome.diagnostics):
                        unresolved = unresolved or required
                        diagnostics.append(VerificationIssue('probe-verification',
                            'probe returned no completed claim', {'probe': type(probe).__name__}))
                    if outcome.certificate is not None:
                        probe_certificates.append(outcome.certificate)
                        if not outcome.certificate.complete:
                            unresolved = unresolved or required
                            diagnostics.append(VerificationIssue("probe-verification", "probe certificate incomplete"))
                        elif not outcome.certificate.satisfied and outcome.counterexample is None:
                            counterexamples.append(Counterexample(
                                "probe-requirement-failure", "probe certificate contains a failed requirement",
                                {"model": model.name, "failed_checks": tuple(
                                    c.name for c in outcome.certificate.checks if not c.passed)},
                                violated=("configured probe requirements",), blocking=required))
                    if outcome.diagnostics:
                        unresolved = unresolved or required
                        diagnostics.extend(outcome.diagnostics)
                    if outcome.counterexample is not None:
                        counterexamples.append(outcome.counterexample)
            evaluation = CandidateEvaluation(
                model,
                certificate,
                tuple(counterexamples),
                tuple(probe_certificates),
                tuple(diagnostics),
            )
            if unresolved:
                undecided.append(evaluation)
            elif certificate.satisfied and not any(item.blocking for item in counterexamples):
                accepted.append((evaluation, input_snapshot))
            else:
                rejected.append((evaluation, input_snapshot))

        truncated = truncated or not stream.complete

        scope_unchanged = initial_scope == (safe_macro_spec_fingerprint(spec),
                                             safe_context_fingerprint(context))
        for bucket in (accepted, rejected):
            current = []
            for evaluation, snapshot in bucket:
                if (scope_unchanged and evaluation.certificate.binds_specification(spec)
                        and evaluation.certificate.binds_context(context)
                        and _model_input_unchanged(evaluation.model, snapshot)):
                    current.append(evaluation)
                else:
                    undecided.append(replace(evaluation, diagnostics=evaluation.diagnostics + (
                        VerificationIssue('final-binding', 'verification inputs changed before the result was returned'),)))
            bucket[:] = current

        frontier, dominated = pareto_partition(accepted)
        return RealizationResult(
            spec=spec,
            candidates=frontier,
            rejected=tuple(rejected),
            dominated=dominated,
            searched_candidates=searched,
            truncated=truncated,
            simulations_used=simulations_used,
            undecided=tuple(undecided),
            diagnostics=tuple(stream.diagnostics),
        )
