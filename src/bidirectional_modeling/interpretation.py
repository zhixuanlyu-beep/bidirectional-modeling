"""Micro-structure -> contextual effect/function/intention hypotheses."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Iterable, Mapping, Optional, Protocol, Sequence, Tuple, Union

from .core import (
    Context,
    DiscriminatingQuery,
    Evidence,
    ExecutableModel,
    Experiment,
    FieldRequirement,
    InterpretationCandidate,
    InterpretationResult,
    PurposeHypothesis,
    PurposeLevel,
    ResourceBudget,
    Trace,
    EquivalenceSpec,
    MacroSpec,
    InterpretationObservation,
)
from .evaluation import SatisfactionEvaluator, TraceBatch
from .structural import freeze_value


class HypothesisGenerator(Protocol):
    independent_recovery: bool

    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        ...


HypothesisSource = Union[HypothesisGenerator, Iterable[PurposeHypothesis]]


class CatalogHypothesisGenerator:
    """Supplies contextual function/intention hypotheses from a domain catalog."""

    independent_recovery = False

    def __init__(self, hypotheses: Iterable[PurposeHypothesis]) -> None:
        self.hypotheses = tuple(hypotheses)

    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        return iter(self.hypotheses)


class ObservedEffectGenerator:
    """Creates conservative effect hypotheses directly from observed transitions.

    Function and intention are intentionally not synthesized here: those require
    environmental or actor evidence beyond the structure itself.
    """

    independent_recovery = True

    def __init__(self, horizon: int = 1) -> None:
        if horizon < 1:
            raise ValueError("horizon must be at least one")
        self.horizon = horizon

    def generate_from_traces(
        self, traces: Sequence[Trace], complete: bool = True
    ) -> Iterable[PurposeHypothesis]:
        """Derive effects from one completely enumerated, reusable trace batch."""

        if not complete or not traces or any(not trace.snapshots for trace in traces):
            return ()
        common_fields = set(traces[0].snapshots[0])
        for trace in traces:
            for snapshot in trace.snapshots:
                common_fields.intersection_update(snapshot)
        hypotheses = []
        for field_name in sorted(common_fields):
            initial_values = [trace.snapshots[0][field_name] for trace in traces]
            final_values = [trace.snapshots[-1][field_name] for trace in traces]
            final_identities = tuple(
                freeze_value(
                    value,
                    purpose="observed effect deterministic structural identity",
                )
                for value in final_values
            )
            if len(set(final_identities)) != 1:
                continue
            final_value = final_values[0]
            final_identity = final_identities[0]
            remains_constant = all(
                all(
                    freeze_value(
                        snapshot[field_name],
                        purpose="observed effect deterministic structural identity",
                    )
                    == final_identity
                    for snapshot in trace.snapshots
                )
                for trace in traces
            )
            if all(
                freeze_value(
                    initial,
                    purpose="observed effect deterministic structural identity",
                )
                == final_identity
                for initial in initial_values
            ) and remains_constant:
                label = "maintain %s at %r" % (field_name, final_value)
            elif all(
                isinstance(initial, (int, float))
                and isinstance(final_value, (int, float))
                and final_value > initial
                for initial in initial_values
            ):
                label = "increase %s to %r" % (field_name, final_value)
            elif all(
                isinstance(initial, (int, float))
                and isinstance(final_value, (int, float))
                and final_value < initial
                for initial in initial_values
            ):
                label = "decrease %s to %r" % (field_name, final_value)
            else:
                label = "produce %s=%r" % (field_name, final_value)
            spec = MacroSpec(
                name=label,
                observables=(field_name,),
                objectives=(FieldRequirement(label, field_name, "eq", final_value),),
                equivalence=EquivalenceSpec((field_name,)),
                horizon=self.horizon,
            )
            hypotheses.append(
                PurposeHypothesis(
                    name=label,
                    level=PurposeLevel.EFFECT,
                    spec=spec,
                    explanation="generated from observed task-horizon behavior; this is an effect, not actor intention",
                )
            )
        return tuple(hypotheses)

    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        traces = tuple(model.simulate(context, self.horizon))
        return self.generate_from_traces(traces)


def _possibilities(hypothesis, experiment):
    allowed = hypothesis.allowed_outcomes.get(experiment.name)
    return tuple(outcome for outcome in experiment.outcomes
                 if allowed is None or outcome in allowed)


def _equivalent_groups(candidates, experiments):
    # Missing declarations are unknown, not evidence of behavioral equivalence.
    signatures = defaultdict(list)
    if not experiments:
        return ()
    for candidate in candidates:
        h = candidate.hypothesis
        if any(e.name not in h.allowed_outcomes for e in experiments):
            continue
        signature = tuple(_possibilities(h, e) for e in experiments)
        signatures[signature].append(h.name)
    return tuple(sorted(tuple(sorted(names)) for names in signatures.values() if len(names) > 1))


def _select_experiment(candidates, experiments, observed):
    best = None
    best_key = None
    for experiment in sorted(experiments, key=lambda e: e.name):
        if experiment.name in observed:
            continue
        allowed = {c.hypothesis.name: _possibilities(c.hypothesis, experiment) for c in candidates}
        # Count declared response signatures once; duplicates do not add weight.
        signatures = tuple(set(frozenset(v) for v in allowed.values()))
        eliminated = [sum(outcome not in values for values in signatures)
                      for outcome in experiment.outcomes
                      if any(outcome in values for values in signatures)]
        guaranteed = min(eliminated, default=0)
        separated = sum(not left.intersection(right)
                        for i, left in enumerate(signatures) for right in signatures[i+1:])
        if not guaranteed:
            continue
        score = guaranteed / (1.0 + experiment.cost)
        key = (score, separated, -experiment.cost)
        if best_key is None or key > best_key:
            best_key = key
            best = DiscriminatingQuery(experiment, tuple(sorted(allowed)),
                allowed, guaranteed, separated, score)
    return best


class Interpreter:
    """Retains compatible hypotheses; selects experiments without probabilities."""

    def __init__(
        self,
        evaluator: Optional[SatisfactionEvaluator] = None,
    ) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()

    def interpret(
        self,
        model: ExecutableModel,
        context: Context,
        hypotheses: HypothesisSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
        *, observations: Sequence[InterpretationObservation] = (),
    ) -> InterpretationResult:
        budget = budget or ResourceBudget()
        experiments = tuple(experiments)
        by_experiment = {e.name: e for e in experiments}
        if len(by_experiment) != len(experiments):
            raise ValueError('duplicate experiment name')
        observations = tuple(observations)
        observed = {}
        for observation in observations:
            experiment = by_experiment.get(observation.experiment)
            if experiment is None or observation.outcome not in experiment.outcomes:
                raise ValueError('observation outside declared experiment domain')
            if observation.experiment in observed and observed[observation.experiment] != observation.outcome:
                raise ValueError('conflicting outcomes require distinct experiment instances')
            observed[observation.experiment] = observation.outcome
        excluded, rejected, undecided = [], [], []
        all_evidence = tuple(context.history) + tuple(evidence)
        batches: dict[int, TraceBatch] = {}
        simulations_used = 0
        remaining_simulations = budget.max_simulations
        truncated = False

        trace_generator = getattr(hypotheses, "generate_from_traces", None)
        if callable(trace_generator):
            horizon = int(getattr(hypotheses, "horizon"))
            batch_budget = replace(
                budget, max_simulations=remaining_simulations
            )
            batch = self.evaluator.collect(model, context, horizon, batch_budget)
            batches[horizon] = batch
            simulations_used += batch.simulations_used
            remaining_simulations -= batch.simulations_used
            truncated = not batch.complete
            hypothesis_items = trace_generator(batch.traces, batch.complete)
        elif hasattr(hypotheses, "generate"):
            hypothesis_items = hypotheses.generate(model, context)  # type: ignore[union-attr]
        else:
            hypothesis_items = iter(hypotheses)  # type: ignore[arg-type]
        candidates = []
        inspected = 0
        names = set()
        for hypothesis in hypothesis_items:
            if inspected >= budget.max_candidates:
                truncated = True
                break
            inspected += 1
            if hypothesis.name in names:
                raise ValueError("duplicate hypothesis name")
            names.add(hypothesis.name)
            for name, allowed in hypothesis.allowed_outcomes.items():
                if name not in by_experiment or not set(allowed) <= set(by_experiment[name].outcomes):
                    raise ValueError('hypothesis outcome declaration outside experiment domain')
            conflict = next((o for o in observations
                             if o.outcome not in _possibilities(hypothesis, by_experiment[o.experiment])), None)
            if conflict is not None:
                excluded.append((hypothesis.name, conflict))
                continue
            horizon = hypothesis.spec.horizon
            batch = batches.get(horizon)
            if batch is None:
                if remaining_simulations <= 0:
                    truncated = True
                    break
                batch_budget = replace(
                    budget, max_simulations=remaining_simulations
                )
                batch = self.evaluator.collect(
                    model, context, horizon, batch_budget
                )
                batches[horizon] = batch
                simulations_used += batch.simulations_used
                remaining_simulations -= batch.simulations_used
            if not batch.complete:
                truncated = True
            certificate = self.evaluator.evaluate_batch(
                model, hypothesis.spec, context, batch, budget
            )
            if not certificate.satisfied:
                if not certificate.complete:
                    reasons = tuple(c.evaluation_error for c in certificate.checks
                                    if c.evaluation_error is not None)
                    undecided.append((hypothesis.name, reasons + certificate.failure_boundaries
                                      or ('verification_incomplete',)))
                else:
                    rejected.append((hypothesis.name, certificate))
                continue
            relevant = tuple(e for e in all_evidence if e.hypothesis == hypothesis.name)
            direct = tuple(e for e in relevant if e.kind in {
                'design', 'choice', 'statement', 'selection-history'})
            caveats = []
            if hypothesis.level == PurposeLevel.INTENTION:
                caveats.append(
                    'behavioral compatibility does not identify intention; evidence records are caller declarations')
                if not direct:
                    caveats.append('no direct actor/design evidence supplied')
            candidates.append(InterpretationCandidate(
                hypothesis=hypothesis, certificate=certificate,
                requirement_count=len(hypothesis.spec.requirements),
                direct_intent_evidence=direct, evidence=relevant, caveats=tuple(caveats)))

        candidates.sort(key=lambda item: item.hypothesis.name)
        groups = _equivalent_groups(candidates, experiments)
        query = _select_experiment(candidates, experiments, observed) if len(candidates) > 1 and not (truncated or undecided) else None

        # A proposed experiment does not make the current evidence identifying;
        # it only describes how the ambiguity could be reduced in a later turn.
        return InterpretationResult(
            model_name=model.name,
            candidates=tuple(candidates),
            equivalent_explanations=groups,
            discriminating_query=query,
            excluded=tuple(excluded), observations=observations,
            rejected=tuple(rejected), undecided=tuple(undecided),
            simulations_used=simulations_used,
            truncated=truncated,
        )

