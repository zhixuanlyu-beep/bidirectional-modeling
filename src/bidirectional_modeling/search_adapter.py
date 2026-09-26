"""Convert independently certified executable-model responses to search hypotheses."""
from dataclasses import dataclass, replace
from typing import Tuple

from .core import Context, ExecutableModel, FiniteStateModel, ResourceBudget, ScenarioKey
from .evaluation import SatisfactionEvaluator
from .provenance import context_fingerprint
from .search import (DescriptionLength, ExperimentHypothesisSearch, SearchHypothesis,
                     SearchProtocol, _name, _natural)
from .structural import callable_signature, fingerprint_value, isolated_copy


@dataclass(frozen=True)
class ModelSearchCase:
    experiment: str
    context: Context
    scenario: ScenarioKey
    field: str
    horizon: int = 1

    def __post_init__(self):
        _name(self.experiment)
        _name(self.field)
        _natural(self.horizon)
        if self.horizon < 1:
            raise ValueError('horizon must be positive')
        object.__setattr__(self, 'context', isolated_copy(self.context, purpose='search case'))

    @property
    def fingerprint(self):
        return fingerprint_value((self.experiment, context_fingerprint(self.context),
                                  self.scenario.initial_state, self.scenario.intervention,
                                  self.field, self.horizon))


@dataclass(frozen=True)
class ModelSearchCandidate:
    model: ExecutableModel
    description: DescriptionLength
    commitments: Tuple[str, ...] = ()
    materials: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelSearchResult:
    search: ExperimentHypothesisSearch
    simulations_used: int
    # Each entry binds a candidate/case to both independently collected batches.
    batch_bindings: tuple
    diagnostics: tuple


def model_declaration_fingerprint(model):
    """Bind inspectable configuration; opaque adapters must declare a stable signature."""
    if type(model) is FiniteStateModel:
        declaration = (
            model.name, model.states, model.initial_states, model.actions,
            model.metrics.as_tuple(), model.assumptions, model.failure_boundaries,
            model.prior_reliability, model.capabilities,
            callable_signature(model.transition), callable_signature(model.readout),
            None if model.applicable is None else callable_signature(model.applicable),
        )
    else:
        signature = getattr(model, 'search_signature', None)
        if not callable(signature):
            raise ValueError('third-party model requires search_signature()')
        declaration = (type(model).__module__,type(model).__qualname__,model.name,signature())
    return fingerprint_value(declaration)


class PredictionDomainError(ValueError):
    """A completed prediction lies outside the declared response universe."""


class ExecutableSearchAdapter:
    def __init__(self, evaluator=None):
        self.evaluator = evaluator or SatisfactionEvaluator()

    def prepare(self, protocol: SearchProtocol, candidates, cases, *, target,
                world_answers, max_simulations=10000, backend="scan"):
        """Read the selected scenario's final field after two complete collections.

        Output labels must already be strings. Target semantics are an explicit
        answer table over the entire response universe, not model-supplied labels.
        No observations are created here: predictions are never experimental data.
        """
        _natural(max_simulations)
        if backend not in ('scan','indexed'):
            raise ValueError('backend must be scan or indexed')
        _name(target)
        candidates, cases, world_answers = tuple(candidates), tuple(cases), tuple(world_answers)
        if len(world_answers) != len(protocol.worlds):
            raise ValueError('target answer table must cover the response universe')
        for answer in world_answers:
            _name(answer)
        if tuple(c.experiment for c in cases) != tuple(e.name for e in protocol.experiments):
            raise ValueError('cases must match the complete ordered experiment domain')
        cases = isolated_copy(cases, purpose='adapter cases')
        case_ids = tuple(c.fingerprint for c in cases)
        bound_protocol = replace(protocol, experiments=tuple(
            replace(e, semantics=e.semantics + ':' + c.fingerprint)
            for e,c in zip(protocol.experiments,cases)))
        bound_target = target + ':' + fingerprint_value(world_answers)
        used, bindings, diagnostics, hypotheses = 0, [], [], []
        for candidate in candidates:
            name = candidate.model.name
            responses, candidate_bindings = [], []
            try:
                # Collect on one isolated configuration; do not mutate caller state.
                model = isolated_copy(candidate.model, purpose='search model snapshot')
                declaration = model_declaration_fingerprint(model)
                batches = [[] for _ in cases]
                # Replay the complete experimental matrix, not adjacent pairs only.
                for _ in range(2):
                    for i,case in enumerate(cases):
                        if used >= max_simulations:
                            raise RuntimeError('simulation_budget_exhausted')
                        if model_declaration_fingerprint(model) != declaration:
                            raise RuntimeError('model_declaration_changed')
                        remaining = max_simulations-used
                        try:
                            batch = self.evaluator.collect(model, isolated_copy(case.context), case.horizon,
                                                           ResourceBudget(max_simulations=remaining))
                        except Exception:
                            used = max_simulations
                            raise
                        used += batch.simulations_used
                        if model_declaration_fingerprint(model) != declaration:
                            raise RuntimeError('model_declaration_changed')
                        if not batch.complete or not batch.binds(model,case.context,case.horizon):
                            raise RuntimeError('incomplete_or_unbound_batch')
                        batches[i].append(batch)
                for case,pair in zip(cases,batches):
                    if pair[0].model_fingerprint != pair[1].model_fingerprint:
                        raise RuntimeError('non_deterministic_response')
                    selected = [t for t in pair[0].traces if t.scenario_key == case.scenario]
                    if len(selected) != 1:
                        raise ValueError('selected scenario is absent or duplicated')
                    response = selected[0].snapshots[-1][case.field]
                    _name(response)
                    responses.append(response)
                    candidate_bindings.append((name, case.experiment,
                                     pair[0].protocol_fingerprint, pair[1].protocol_fingerprint,
                                     declaration))
                if tuple(c.fingerprint for c in cases) != case_ids:
                    raise RuntimeError('experiment declaration changed during collection')
                if tuple(responses) not in protocol.worlds:
                    raise PredictionDomainError('prediction_outside_response_universe')
                world = protocol.worlds.index(tuple(responses))
                hypothesis = SearchHypothesis(name, world, world_answers[world],
                                              candidate.description, candidate.commitments, candidate.materials)
                ExperimentHypothesisSearch(bound_protocol,(hypothesis,),bound_target,
                                           world_answers=world_answers)
                bindings.extend(candidate_bindings)
            except Exception as error:
                diagnostics.append((name, type(error).__name__, str(error)))
                hypothesis = SearchHypothesis(name,None,'unresolved',candidate.description,
                                              (),candidate.materials)
            hypotheses.append(hypothesis)
        search = ExperimentHypothesisSearch(bound_protocol,tuple(hypotheses),bound_target,backend=backend,world_answers=world_answers)
        return ModelSearchResult(search,used,tuple(bindings),tuple(diagnostics))
