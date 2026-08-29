"""Micro-structure -> contextual effect/function/intention hypotheses."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
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
    normalized_entropy,
)
from .evaluation import SatisfactionEvaluator, TraceBatch


class HypothesisGenerator(Protocol):
    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        ...


HypothesisSource = Union[HypothesisGenerator, Iterable[PurposeHypothesis]]


class CatalogHypothesisGenerator:
    """Supplies contextual function/intention hypotheses from a domain catalog."""

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
            if len(set(map(repr, final_values))) != 1:
                continue
            final_value = final_values[0]
            remains_constant = all(
                all(snapshot[field_name] == final_value for snapshot in trace.snapshots)
                for trace in traces
            )
            if all(initial == final_value for initial in initial_values) and remains_constant:
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
                    prior=0.5,
                    explanation="generated from observed task-horizon behavior; this is an effect, not actor intention",
                )
            )
        return tuple(hypotheses)

    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        traces = tuple(model.simulate(context, self.horizon))
        return self.generate_from_traces(traces)


@dataclass(frozen=True)
class InterpretationScoringPolicy:
    coverage_weight: float = 0.20
    robustness_weight: float = 0.20
    assumption_weight: float = 0.15
    simplicity_weight: float = 0.15
    context_weight: float = 0.30
    minimum_direct_intent_evidence: float = 0.50
    unsupported_intent_cap: float = 0.49

    def __post_init__(self) -> None:
        weights = (
            self.coverage_weight,
            self.robustness_weight,
            self.assumption_weight,
            self.simplicity_weight,
            self.context_weight,
        )
        if any(weight < 0 for weight in weights):
            raise ValueError("interpretation weights must be non-negative")
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("interpretation weights must sum to 1")
        if not 0.0 <= self.minimum_direct_intent_evidence <= 1.0:
            raise ValueError("minimum direct intent evidence must be in [0, 1]")
        if not 0.0 <= self.unsupported_intent_cap <= 1.0:
            raise ValueError("unsupported intent cap must be in [0, 1]")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _context_support(
    hypothesis: PurposeHypothesis, evidence: Sequence[Evidence]
) -> Tuple[float, Tuple[Evidence, ...]]:
    relevant = tuple(item for item in evidence if item.hypothesis == hypothesis.name)
    adjustment = sum(item.strength for item in relevant) * 0.25
    return _clamp(hypothesis.prior + adjustment), relevant


def _is_direct_intent_evidence(item: Evidence, minimum_strength: float) -> bool:
    return item.strength >= minimum_strength and item.kind in {
        "design",
        "choice",
        "statement",
        "selection-history",
    }


def _expected_information_gain(
    candidates: Sequence[InterpretationCandidate], experiment: Experiment
) -> Tuple[float, Mapping[str, float]]:
    # Ranking scores combine fit and evidence for ordering, but are explicitly
    # uncalibrated. Information gain therefore uses the declared hypothesis
    # priors and never silently reinterprets a ranking score as a probability.
    weights = [candidate.hypothesis.prior for candidate in candidates]
    total = sum(weights)
    priors = (
        [weight / total for weight in weights]
        if total > 0
        else [1.0 / len(candidates)] * len(candidates)
    )
    predictions = {
        candidate.hypothesis.name: candidate.hypothesis.predictions.get(experiment.name, 0.5)
        for candidate in candidates
    }
    positive_probability = sum(
        prior * predictions[candidate.hypothesis.name]
        for prior, candidate in zip(priors, candidates)
    )
    negative_probability = 1.0 - positive_probability

    positive_weights = [
        prior * predictions[candidate.hypothesis.name]
        for prior, candidate in zip(priors, candidates)
    ]
    negative_weights = [
        prior * (1.0 - predictions[candidate.hypothesis.name])
        for prior, candidate in zip(priors, candidates)
    ]
    prior_entropy = normalized_entropy(priors)
    posterior_entropy = (
        positive_probability * normalized_entropy(positive_weights)
        + negative_probability * normalized_entropy(negative_weights)
    )
    gain = max(0.0, prior_entropy - posterior_entropy) / (1.0 + experiment.cost)
    return gain, predictions


def _equivalent_groups(
    candidates: Sequence[InterpretationCandidate], experiments: Sequence[Experiment]
) -> Tuple[Tuple[str, ...], ...]:
    signatures = defaultdict(list)
    for candidate in candidates:
        signature = tuple(
            round(candidate.hypothesis.predictions.get(experiment.name, 0.5), 9)
            for experiment in experiments
        )
        signatures[signature].append(candidate.hypothesis.name)
    groups = [tuple(sorted(names)) for names in signatures.values() if len(names) > 1]
    return tuple(sorted(groups))


class Interpreter:
    """Ranks compatible macro hypotheses without claiming purpose is intrinsic."""

    def __init__(
        self,
        evaluator: Optional[SatisfactionEvaluator] = None,
        scoring_policy: Optional[InterpretationScoringPolicy] = None,
    ) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()
        self.scoring_policy = scoring_policy or InterpretationScoringPolicy()

    def interpret(
        self,
        model: ExecutableModel,
        context: Context,
        hypotheses: HypothesisSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> InterpretationResult:
        budget = budget or ResourceBudget()
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
        for hypothesis in hypothesis_items:
            if inspected >= budget.max_candidates:
                truncated = True
                break
            inspected += 1
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
                continue
            fit = certificate.confidence.value
            requirement_count = len(hypothesis.spec.requirements)
            simplicity = 1.0 / (1.0 + 0.15 * max(0, requirement_count - 1))
            robustness = certificate.confidence.robustness
            support, relevant_evidence = _context_support(hypothesis, all_evidence)
            policy = self.scoring_policy
            ranking_score = (
                policy.coverage_weight * certificate.confidence.coverage
                + policy.robustness_weight * robustness
                + policy.assumption_weight
                * certificate.confidence.assumption_reliability
                + policy.simplicity_weight * simplicity
                + policy.context_weight * support
            )
            caveats = []
            if hypothesis.level == PurposeLevel.INTENTION:
                direct = any(
                    _is_direct_intent_evidence(
                        item, policy.minimum_direct_intent_evidence
                    )
                    for item in relevant_evidence
                )
                if not direct:
                    ranking_score = min(
                        ranking_score, policy.unsupported_intent_cap
                    )
                    caveats.append(
                        "structure and behavior support compatibility, but no sufficiently strong actor/design evidence identifies intention"
                    )
            candidates.append(
                InterpretationCandidate(
                    hypothesis=hypothesis,
                    certificate=certificate,
                    fit=fit,
                    simplicity=simplicity,
                    robustness=robustness,
                    context_support=support,
                    ranking_score=_clamp(ranking_score),
                    evidence=relevant_evidence,
                    caveats=tuple(caveats),
                )
            )

        candidates.sort(key=lambda item: (-item.confidence, item.hypothesis.name))
        groups = _equivalent_groups(candidates, experiments)
        query = None
        if len(candidates) > 1 and experiments:
            scored = []
            for experiment in experiments:
                gain, predictions = _expected_information_gain(candidates, experiment)
                scored.append((gain, experiment, predictions))
            gain, experiment, predictions = max(scored, key=lambda item: (item[0], -item[1].cost, item[1].name))
            if gain > 1e-12:
                query = DiscriminatingQuery(
                    experiment=experiment,
                    candidate_names=tuple(item.hypothesis.name for item in candidates),
                    predictions=predictions,
                    expected_information_gain=gain,
                )

        # A proposed experiment does not make the current evidence identifying;
        # it only describes how the ambiguity could be reduced in a later turn.
        non_identifiable = len(candidates) > 1
        return InterpretationResult(
            model_name=model.name,
            candidates=tuple(candidates),
            equivalent_explanations=groups,
            discriminating_query=query,
            non_identifiable=non_identifiable,
            simulations_used=simulations_used,
            truncated=truncated,
        )
