"""Macro-purpose -> micro-structure search with Pareto selection."""

from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence, Union

from .core import (
    CandidateEvaluation,
    Context,
    Counterexample,
    ExecutableModel,
    MacroSpec,
    ProbeOutcome,
    RealizationResult,
    ResourceBudget,
)
from .evaluation import SatisfactionEvaluator


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
        self.models = tuple(models)

    def generate(
        self, spec: MacroSpec, context: Context, budget: ResourceBudget
    ) -> Iterable[ExecutableModel]:
        return iter(self.models)


class ParametricCandidateGenerator:
    """Synthesizes candidate structures from a finite parameter design space."""

    def __init__(
        self,
        parameter_space: Mapping[str, Sequence[Any]],
        factory: Callable[[Mapping[str, Any], MacroSpec, Context], ExecutableModel],
    ) -> None:
        if any(not values for values in parameter_space.values()):
            raise ValueError("every parameter must have at least one candidate value")
        self.parameter_space = dict(parameter_space)
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
    confidence_no_worse = left.confidence >= right.confidence
    confidence_better = left.confidence > right.confidence
    return no_worse and confidence_no_worse and (strictly_better or confidence_better)


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
    frontier.sort(key=lambda item: (-item.confidence, item.model.metrics.as_tuple(), item.model.name))
    dominated.sort(key=lambda item: (-item.confidence, item.model.name))
    return tuple(frontier), tuple(dominated)


class Realizer:
    def __init__(
        self,
        evaluator: Optional[SatisfactionEvaluator] = None,
        probes: Sequence[RedTeamProbe] = (),
    ) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()
        self.probes = tuple(probes)

    def realize(
        self,
        spec: MacroSpec,
        context: Context,
        source: CandidateSource,
        budget: Optional[ResourceBudget] = None,
    ) -> RealizationResult:
        budget = budget or ResourceBudget()
        if hasattr(source, "generate"):
            models = source.generate(spec, context, budget)  # type: ignore[union-attr]
            source_count = len(models) if hasattr(models, "__len__") else None
        else:
            source_count = len(source) if hasattr(source, "__len__") else None
            models = iter(source)  # type: ignore[arg-type]

        accepted = []
        rejected = []
        searched = 0
        truncated = False
        simulations_used = 0
        remaining_simulations = budget.max_simulations
        model_iterator = iter(models)
        while searched < budget.max_candidates and remaining_simulations > 0:
            try:
                model = next(model_iterator)
            except StopIteration:
                break
            searched += 1
            candidate_budget = replace(budget, max_simulations=remaining_simulations)
            certificate = self.evaluator.evaluate(model, spec, context, candidate_budget)
            simulations_used += certificate.verified_scenarios
            remaining_simulations -= certificate.verified_scenarios
            counterexamples = []
            probe_certificates = []
            if not certificate.complete:
                truncated = True
            if certificate.satisfied:
                for probe in self.probes:
                    if remaining_simulations <= 0:
                        counterexamples.append(
                            Counterexample(
                                kind="verification-budget-exhausted",
                                summary="the candidate could not run all configured red-team probes",
                                witness={"model": model.name},
                                violated=("complete adversarial verification",),
                                suggested_refinements=("increase max_simulations",),
                            )
                        )
                        truncated = True
                        break
                    probe_budget = replace(budget, max_simulations=remaining_simulations)
                    outcome = probe.probe(
                        model, spec, context, self.evaluator, probe_budget
                    )
                    simulations_used += outcome.simulations_used
                    remaining_simulations -= outcome.simulations_used
                    if outcome.certificate is not None:
                        probe_certificates.append(outcome.certificate)
                        if not outcome.certificate.complete:
                            truncated = True
                    if outcome.counterexample is not None:
                        counterexamples.append(outcome.counterexample)
            evaluation = CandidateEvaluation(
                model,
                certificate,
                tuple(counterexamples),
                tuple(probe_certificates),
            )
            if certificate.satisfied and not any(item.blocking for item in counterexamples):
                accepted.append(evaluation)
            else:
                rejected.append(evaluation)

        if remaining_simulations <= 0 and (
            source_count is None or source_count > searched
        ):
            truncated = True
        if searched >= budget.max_candidates and (
            source_count is None or source_count > searched
        ):
            truncated = True

        frontier, dominated = pareto_partition(accepted)
        return RealizationResult(
            spec=spec,
            candidates=frontier,
            rejected=tuple(rejected),
            dominated=dominated,
            searched_candidates=searched,
            truncated=truncated,
            simulations_used=simulations_used,
        )
