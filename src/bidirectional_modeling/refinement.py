"""Dynamical closure checks and a versioned concept memory."""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import Dict, Iterable, Optional, Tuple

from .core import ClosureReport, Concept, Context, Counterexample, FiniteStateModel, MacroSpec


class ClosureAnalyzer:
    """Finds x1 ~ x2 but T(x1) !~ T(x2) witnesses."""

    def analyze(
        self, model: FiniteStateModel, spec: MacroSpec, context: Context
    ) -> ClosureReport:
        violations = []
        checked = 0
        state_items = tuple(model.states.items())
        actions = model.actions or ("noop",)
        for (left_name, left), (right_name, right) in combinations(state_items, 2):
            left_observed = model.observe(left, context)
            right_observed = model.observe(right, context)
            if not spec.equivalence.equivalent(left_observed, right_observed):
                continue
            for action in actions:
                checked += 1
                left_next = model.step(left, action, context)
                right_next = model.step(right, action, context)
                left_next_observed = model.observe(left_next, context)
                right_next_observed = model.observe(right_next, context)
                if spec.equivalence.equivalent(left_next_observed, right_next_observed):
                    continue
                differing = tuple(
                    sorted(
                        key
                        for key in set(left).intersection(right)
                        if left[key] != right[key]
                    )
                )
                violations.append(
                    {
                        "left_name": left_name,
                        "right_name": right_name,
                        "left": dict(left),
                        "right": dict(right),
                        "action": action,
                        "left_next": dict(left_next_observed),
                        "right_next": dict(right_next_observed),
                        "differing": differing,
                    }
                )

        separating_counts: Dict[str, int] = {}
        for violation in violations:
            for feature in violation["differing"]:
                separating_counts[feature] = separating_counts.get(feature, 0) + 1
        ranked_features = tuple(
            feature
            for feature, _ in sorted(
                separating_counts.items(), key=lambda item: (-item[1], item[0])
            )
        )

        counterexamples = []
        for violation in violations:
            local_ranked = tuple(feature for feature in ranked_features if feature in violation["differing"])
            counterexamples.append(
                Counterexample(
                    kind="dynamical-non-closure",
                    summary="two macro-equivalent states evolve into different macro classes",
                    witness={
                        "left_state": violation["left_name"],
                        "right_state": violation["right_name"],
                        "left_micro": violation["left"],
                        "right_micro": violation["right"],
                        "action": violation["action"],
                        "left_next_macro": violation["left_next"],
                        "right_next_macro": violation["right_next"],
                    },
                    violated=("dynamical closure of %s" % spec.name,),
                    suggested_refinements=tuple(
                        "promote micro feature %r to a macro observable" % feature
                        for feature in local_ranked
                    ),
                )
            )
        return ClosureReport(
            closed=not counterexamples,
            checked_pairs=checked,
            counterexamples=tuple(counterexamples),
        )


class ConceptLibrary:
    """Small in-memory concept store; persistence can be supplied by an adapter."""

    def __init__(self, concepts: Iterable[Concept] = ()) -> None:
        self._concepts = {concept.name: concept for concept in concepts}

    def add(self, concept: Concept) -> None:
        if concept.name in self._concepts:
            raise ValueError("concept %r already exists" % concept.name)
        self._concepts[concept.name] = concept

    def get(self, name: str) -> Concept:
        return self._concepts[name]

    def all(self) -> Tuple[Concept, ...]:
        return tuple(sorted(self._concepts.values(), key=lambda item: item.name))

    def record_judgment(
        self,
        name: str,
        example: str,
        accepted: bool,
        boundary: Optional[str] = None,
    ) -> Concept:
        concept = self.get(name)
        positives = concept.positive_examples
        negatives = concept.negative_examples
        if accepted and example not in positives:
            positives += (example,)
        if not accepted and example not in negatives:
            negatives += (example,)
        boundaries = concept.boundaries
        if boundary and boundary not in boundaries:
            boundaries += (boundary,)
        updated = replace(
            concept,
            positive_examples=positives,
            negative_examples=negatives,
            boundaries=boundaries,
            version=concept.version + 1,
        )
        self._concepts[name] = updated
        return updated

    def refine_from_counterexample(self, name: str, counterexample: Counterexample) -> Concept:
        boundary = counterexample.summary
        example = repr(dict(counterexample.witness))
        concept = self.record_judgment(name, example, accepted=False, boundary=boundary)
        definitions = concept.candidate_definitions
        for suggestion in counterexample.suggested_refinements:
            if suggestion not in definitions:
                definitions += (suggestion,)
        updated = replace(concept, candidate_definitions=definitions, version=concept.version + 1)
        self._concepts[name] = updated
        return updated

