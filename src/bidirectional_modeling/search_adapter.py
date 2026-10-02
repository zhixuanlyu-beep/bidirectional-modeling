"""Convert independently certified executable-model responses to search hypotheses."""
from dataclasses import dataclass, replace
from typing import Optional, Tuple

from .core import Context, ExecutableModel, ResourceBudget, ScenarioKey
from .evaluation import SatisfactionEvaluator, TraceBatch, _checked_evaluator_result
from .provenance import context_fingerprint, model_declaration_fingerprint
from .search import (DescriptionLength, ExperimentHypothesisSearch, SearchHypothesis,
                     SearchProtocol, _name, _natural)
from .structural import fingerprint_value, isolated_copy, ordered_tuple


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

    def __post_init__(self):
        object.__setattr__(self, 'commitments', ordered_tuple(self.commitments))
        object.__setattr__(self, 'materials', ordered_tuple(self.materials))


@dataclass(frozen=True)
class ModelSearchResult:
    search: ExperimentHypothesisSearch
    simulations_used: int
    # Each entry binds a candidate/case to both independently collected batches.
    batch_bindings: tuple
    diagnostics: tuple




class PredictionDomainError(ValueError):
    """A completed prediction lies outside the declared response universe."""


def _promote_prediction(problem, candidate, responses):
    """Validate the complete row and commitments before publishing a hypothesis."""
    try:
        world = problem.protocol.worlds.index(responses)
    except ValueError:
        raise PredictionDomainError('prediction_outside_response_universe') from None
    proposed = SearchHypothesis(candidate.model.name, world, problem.world_answers[world],
        candidate.description, candidate.commitments, candidate.materials)
    problem._validate_hypothesis(proposed)
    return proposed


@dataclass(frozen=True)
class _ResponseMatrix:
    responses: Optional[Tuple[str, ...]]
    batch_bindings: tuple
    simulations_used: int
    diagnostics: tuple = ()


def _collect_response_matrix(candidate, cases, *, max_simulations, evaluator=None):
    """Collect and replay one selected matrix; no candidate or target semantics."""
    evaluator = evaluator or SatisfactionEvaluator()
    name, used = candidate.model.name, 0
    try:
        model = isolated_copy(candidate.model, purpose='search model snapshot')
        declaration = model_declaration_fingerprint(model)
        case_ids = tuple(c.fingerprint for c in cases)
        batches = [[] for _ in cases]
        for _ in range(2):
            for i, case in enumerate(cases):
                if used >= max_simulations:
                    raise RuntimeError('simulation_budget_exhausted')
                if model_declaration_fingerprint(model) != declaration:
                    raise RuntimeError('model_declaration_changed')
                try:
                    batch = evaluator.collect(model, isolated_copy(case.context), case.horizon,
                        ResourceBudget(max_simulations=max_simulations-used))
                    batch = _checked_evaluator_result(batch, TraceBatch, max_simulations-used)
                except Exception:
                    used = max_simulations
                    raise
                used += batch.simulations_used
                if model_declaration_fingerprint(model) != declaration:
                    raise RuntimeError('model_declaration_changed')
                if not batch.complete or not batch.binds(model, case.context, case.horizon):
                    raise RuntimeError('incomplete_or_unbound_batch')
                batches[i].append(batch)
        responses, bindings = [], []
        for case, pair in zip(cases, batches):
            if pair[0].model_fingerprint != pair[1].model_fingerprint:
                raise RuntimeError('non_deterministic_response')
            selected = [t for t in pair[0].traces if t.scenario_key == case.scenario]
            if len(selected) != 1:
                raise ValueError('selected scenario is absent or duplicated')
            response = selected[0].snapshots[-1][case.field]
            _name(response)
            responses.append(response)
            bindings.append((name, case.experiment, pair[0].protocol_fingerprint,
                             pair[1].protocol_fingerprint, declaration))
        if tuple(c.fingerprint for c in cases) != case_ids:
            raise RuntimeError('experiment declaration changed during collection')
        return _ResponseMatrix(tuple(responses), tuple(bindings), used)
    except Exception as error:
        return _ResponseMatrix(None, (), used, ((name, type(error).__name__, str(error)),))


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
        candidates, cases, world_answers = ordered_tuple(candidates), ordered_tuple(cases), ordered_tuple(world_answers)
        if len(world_answers) != len(protocol.worlds):
            raise ValueError('target answer table must cover the response universe')
        for answer in world_answers:
            _name(answer)
        if tuple(c.experiment for c in cases) != tuple(e.name for e in protocol.experiments):
            raise ValueError('cases must match the complete ordered experiment domain')
        cases = isolated_copy(cases, purpose='adapter cases')
        bound_protocol = replace(protocol, experiments=tuple(
            replace(e, semantics=e.semantics + ':' + c.fingerprint)
            for e,c in zip(protocol.experiments,cases)))
        bound_target = target + ':' + fingerprint_value(world_answers)
        base = ExperimentHypothesisSearch(bound_protocol, (), bound_target,
            backend=backend, world_answers=world_answers)
        used, bindings, diagnostics, hypotheses = 0, [], [], []
        for candidate in candidates:
            name = candidate.model.name
            matrix = _collect_response_matrix(candidate, cases,
                max_simulations=max_simulations-used, evaluator=self.evaluator)
            used += matrix.simulations_used
            diagnostics.extend(matrix.diagnostics)
            hypothesis = SearchHypothesis(name, None, 'unresolved', candidate.description,
                                          (), candidate.materials)
            if matrix.responses is not None:
                try:
                    hypothesis = _promote_prediction(base, candidate, matrix.responses)
                    bindings.extend(matrix.batch_bindings)
                except Exception as error:
                    diagnostics.append((name, type(error).__name__, str(error)))
            hypotheses.append(hypothesis)
        search = base.with_hypotheses(tuple(hypotheses))
        return ModelSearchResult(search,used,tuple(bindings),tuple(diagnostics))
