"""Micro-structure -> contextual effect/function/intention hypotheses."""

from __future__ import annotations

from collections import defaultdict
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
    EquivalenceSpec,
    MacroSpec,
    normalized_entropy,
)
from .evaluation import SatisfactionEvaluator


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
        self.horizon = horizon

    def generate(
        self, model: ExecutableModel, context: Context
    ) -> Iterable[PurposeHypothesis]:
        traces = tuple(model.simulate(context, self.horizon))
        if not traces:
            return ()
        common_fields = set(traces[0].snapshots[0])
        for trace in traces:
            common_fields.intersection_update(trace.snapshots[0])
            common_fields.intersection_update(trace.snapshots[-1])
        hypotheses = []
        for field_name in sorted(common_fields):
            initial_values = [trace.snapshots[0][field_name] for trace in traces]
            final_values = [trace.snapshots[-1][field_name] for trace in traces]
            if len(set(map(repr, final_values))) != 1:
                continue
            final_value = final_values[0]
            if all(initial == final_value for initial in initial_values):
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
                    explanation="generated from a stable observed transition; this is an effect, not actor intention",
                )
            )
        return tuple(hypotheses)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _context_support(
    hypothesis: PurposeHypothesis, evidence: Sequence[Evidence]
) -> Tuple[float, Tuple[Evidence, ...]]:
    relevant = tuple(item for item in evidence if item.hypothesis == hypothesis.name)
    adjustment = sum(item.strength for item in relevant) * 0.25
    return _clamp(hypothesis.prior + adjustment), relevant


def _is_direct_intent_evidence(item: Evidence) -> bool:
    return item.strength > 0 and item.kind in {"design", "choice", "statement", "selection-history"}


def _expected_information_gain(
    candidates: Sequence[InterpretationCandidate], experiment: Experiment
) -> Tuple[float, Mapping[str, float]]:
    weights = [max(candidate.confidence, 1e-12) for candidate in candidates]
    total = sum(weights)
    priors = [weight / total for weight in weights]
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

    def __init__(self, evaluator: Optional[SatisfactionEvaluator] = None) -> None:
        self.evaluator = evaluator or SatisfactionEvaluator()

    def interpret(
        self,
        model: ExecutableModel,
        context: Context,
        hypotheses: HypothesisSource,
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> InterpretationResult:
        all_evidence = tuple(context.history) + tuple(evidence)
        if hasattr(hypotheses, "generate"):
            hypothesis_items = hypotheses.generate(model, context)  # type: ignore[union-attr]
        else:
            hypothesis_items = iter(hypotheses)  # type: ignore[arg-type]
        candidates = []
        for hypothesis in hypothesis_items:
            certificate = self.evaluator.evaluate(model, hypothesis.spec, context, budget)
            if not certificate.satisfied:
                continue
            fit = certificate.confidence.value
            requirement_count = len(hypothesis.spec.requirements)
            simplicity = 1.0 / (1.0 + 0.15 * max(0, requirement_count - 1))
            robustness = certificate.confidence.robustness
            support, relevant_evidence = _context_support(hypothesis, all_evidence)
            confidence = 0.40 * fit + 0.15 * simplicity + 0.25 * robustness + 0.20 * support
            caveats = []
            if hypothesis.level == PurposeLevel.INTENTION:
                direct = any(_is_direct_intent_evidence(item) for item in relevant_evidence)
                if not direct:
                    confidence = min(confidence, 0.49)
                    caveats.append(
                        "structure and behavior support compatibility, but no actor/design evidence identifies intention"
                    )
            candidates.append(
                InterpretationCandidate(
                    hypothesis=hypothesis,
                    certificate=certificate,
                    fit=fit,
                    simplicity=simplicity,
                    robustness=robustness,
                    context_support=support,
                    confidence=_clamp(confidence),
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
        )
