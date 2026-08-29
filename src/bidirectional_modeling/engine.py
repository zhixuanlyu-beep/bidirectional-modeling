"""Facade and semantic/behavioral round-trip checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .core import (
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
from .interpretation import Interpreter
from .realization import CandidateSource, Realizer
from .refinement import ClosureAnalyzer, ConceptLibrary


@dataclass(frozen=True)
class MacroRoundTripReport:
    realization: RealizationResult
    interpretations: Tuple[InterpretationResult, ...]
    semantic_preservation: Tuple[bool, ...]

    @property
    def passed(self) -> bool:
        return bool(self.semantic_preservation) and all(self.semantic_preservation)


@dataclass(frozen=True)
class MicroRoundTripReport:
    interpretation: InterpretationResult
    realization: RealizationResult
    behaviorally_equivalent_models: Tuple[str, ...]

    @property
    def passed(self) -> bool:
        return bool(self.behaviorally_equivalent_models)


def behaviorally_equivalent(
    left: ExecutableModel,
    right: ExecutableModel,
    spec: MacroSpec,
    context: Context,
) -> bool:
    def signatures(model: ExecutableModel) -> Tuple[Tuple[Tuple[object, ...], ...], ...]:
        traces = model.simulate(context, spec.horizon)
        result = []
        for trace in traces:
            result.append(tuple(spec.equivalence.signature(snapshot) for snapshot in trace.snapshots))
        return tuple(sorted(result, key=repr))

    return signatures(left) == signatures(right)


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
        hypotheses: Iterable[PurposeHypothesis],
        evidence: Sequence[Evidence] = (),
        experiments: Sequence[Experiment] = (),
        budget: Optional[ResourceBudget] = None,
    ) -> InterpretationResult:
        return self.interpreter.interpret(
            model, context, hypotheses, evidence, experiments, budget
        )

    def check_closure(
        self, model: FiniteStateModel, spec: MacroSpec, context: Context
    ):
        return self.closure.analyze(model, spec, context)

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
        realization = self.realize(spec, context, source, budget)
        interpretations = []
        preservation = []
        hypotheses = tuple(hypotheses)
        for candidate in realization.candidates:
            result = self.interpret(
                candidate.model, context, hypotheses, evidence, experiments, budget
            )
            interpretations.append(result)
            preservation.append(
                any(item.hypothesis.spec.name == spec.name for item in result.candidates)
            )
        return MacroRoundTripReport(realization, tuple(interpretations), tuple(preservation))

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
        interpretation = self.interpret(
            model, context, hypotheses, evidence, experiments, budget
        )
        if not interpretation.candidates:
            raise ValueError("no compatible macro hypothesis can seed the return realization")
        top_spec = interpretation.candidates[0].hypothesis.spec
        realization = self.realize(top_spec, context, realization_source, budget)
        equivalent = tuple(
            item.model.name
            for item in realization.candidates
            if behaviorally_equivalent(model, item.model, top_spec, context)
        )
        return MicroRoundTripReport(interpretation, realization, equivalent)
