"""Optional human judgment memory. Records are not proof certificates."""
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Tuple
from ..core import Concept, Counterexample


@dataclass(frozen=True)
class ConceptJudgment:
    concept_name: str
    version: int
    example: str
    accepted: bool
    source: str
    boundary: Optional[str]
    reason: str = ""
    applicability: str = ""


class ConceptLibrary:
    """Small in-memory concept store; persistence can be supplied by an adapter."""

    def __init__(self, concepts: Iterable[Concept] = ()) -> None:
        self._concepts = {concept.name: concept for concept in concepts}
        self._history = []

    @property
    def history(self) -> Tuple[ConceptJudgment, ...]:
        return tuple(self._history)

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
        *, source: str,
    ) -> Concept:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("judgment source must be explicit")
        concept = self.get(name)
        positives = concept.positive_examples
        negatives = concept.negative_examples
        if accepted and example not in positives:
            positives += (example,)
        if accepted and example in negatives:
            negatives = tuple(item for item in negatives if item != example)
        if not accepted and example not in negatives:
            negatives += (example,)
        if not accepted and example in positives:
            positives = tuple(item for item in positives if item != example)
        boundaries = concept.boundaries
        if boundary and boundary not in boundaries:
            boundaries += (boundary,)
        if (
            positives == concept.positive_examples
            and negatives == concept.negative_examples
            and boundaries == concept.boundaries
        ):
            self._history.append(ConceptJudgment(name, concept.version, example,
                                                 accepted, source, boundary))
            return concept
        updated = replace(
            concept,
            positive_examples=positives,
            negative_examples=negatives,
            boundaries=boundaries,
            version=concept.version + 1,
        )
        self._concepts[name] = updated
        self._history.append(ConceptJudgment(name, updated.version, example, accepted, source, boundary))
        return updated

    def refine_from_counterexample(
        self, name: str, counterexample: Counterexample, *,
        source: str, reason: str, applicability: str,
    ) -> Concept:
        """Record a caller-approved relation, never infer concept membership from a witness."""
        if (not isinstance(counterexample, Counterexample)
                or not counterexample.witness or not counterexample.violated):
            raise ValueError("a diagnostic cannot become a concept counterexample")
        if any(not isinstance(v, str) or not v.strip() for v in (source, reason, applicability)):
            raise ValueError("source, reason and applicability must be explicit")
        concept = self.get(name)
        boundary = counterexample.summary
        example = repr(dict(counterexample.witness))
        positives = tuple(item for item in concept.positive_examples if item != example)
        negatives = concept.negative_examples
        if example not in negatives:
            negatives += (example,)
        boundaries = concept.boundaries
        if boundary not in boundaries:
            boundaries += (boundary,)
        definitions = concept.candidate_definitions
        for suggestion in counterexample.suggested_refinements:
            if suggestion not in definitions:
                definitions += (suggestion,)
        changed = (
            positives != concept.positive_examples
            or negatives != concept.negative_examples
            or boundaries != concept.boundaries
            or definitions != concept.candidate_definitions
        )
        if not changed:
            self._history.append(ConceptJudgment(name, concept.version, example,
                                                 False, source, boundary, reason, applicability))
            return concept
        updated = replace(
            concept,
            positive_examples=positives,
            negative_examples=negatives,
            boundaries=boundaries,
            candidate_definitions=definitions,
            version=concept.version + 1,
        )
        self._concepts[name] = updated
        self._history.append(ConceptJudgment(name, updated.version, example, False, source, boundary, reason, applicability))
        return updated
