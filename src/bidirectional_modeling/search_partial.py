"""Replayable predictions for an explicitly selected experiment submatrix.

These are model predictions, never observations or complete-world witnesses.
"""
from dataclasses import asdict, dataclass, replace
from typing import Optional, Tuple

from .core import ResourceBudget
from .evaluation import SatisfactionEvaluator
from .provenance import trace_batch_protocol_fingerprint
from .search import SearchBudgetExceeded, SearchWorkBudget, _natural
from .search_adapter import _collect_response_matrix, model_declaration_fingerprint
from .structural import fingerprint_value, isolated_copy


@dataclass(frozen=True)
class PartialPrediction:
    input_fingerprint: str
    candidate: str
    experiments: Tuple[str, ...]
    responses: Tuple[str, ...]
    batch_bindings: tuple
    simulation_limit: Optional[int] = None

    def __post_init__(self):
        for field in ('experiments', 'responses', 'batch_bindings'):
            object.__setattr__(self, field, tuple(getattr(self, field)))

    @property
    def fingerprint(self):
        return fingerprint_value(('partial-prediction-v2', asdict(self)))


@dataclass(frozen=True)
class PartialPredictionResult:
    prediction: Optional[PartialPrediction]
    simulations_used: int
    reason: str
    diagnostics: tuple = ()


@dataclass(frozen=True)
class PartialPredictionVerification:
    status: str  # valid / invalid / undecided
    reason: str
    simulations_used: int


def _binding(protocol, candidate, cases):
    return fingerprint_value(('partial-input-v1', protocol.fingerprint,
        model_declaration_fingerprint(candidate.model), asdict(candidate.description),
        candidate.commitments, candidate.materials, tuple(c.fingerprint for c in cases)))


def collect_partial_prediction(protocol, candidate, cases, experiments, *,
                               max_simulations=10000, budget=None, evaluator=None):
    """Replay the selected matrix twice, in canonical protocol order.

    All case declarations bind the result, including cases not executed. The
    original constraints are not asserted by a partial response. A later full
    promotion must validate them. No combination of separate receipts is used.
    """
    _natural(max_simulations)
    budget = budget if budget is not None else SearchWorkBudget()
    cases = isolated_copy(tuple(cases), purpose='partial cases')
    candidate = isolated_copy(candidate, purpose='partial candidate')
    names = tuple(e.name for e in protocol.experiments)
    selected = tuple(experiments)
    if not selected or len(set(selected)) != len(selected) or any(n not in names for n in selected):
        raise ValueError('select distinct experiments from the declared domain')
    if tuple(c.experiment for c in cases) != names:
        raise ValueError('cases must match the complete ordered experiment domain')
    selected = tuple(n for n in names if n in selected)
    digest = _binding(protocol, candidate, cases)
    try:
        budget.consume('query_checks')
    except SearchBudgetExceeded as error:
        return PartialPredictionResult(None, 0, error.reason)
    indexes = tuple(names.index(n) for n in selected)
    matrix = _collect_response_matrix(candidate, tuple(cases[i] for i in indexes),
        max_simulations=max_simulations, evaluator=evaluator)
    if _binding(protocol, candidate, cases) != digest:
        return PartialPredictionResult(None, matrix.simulations_used, 'input_declaration_changed')
    if matrix.responses is None:
        return PartialPredictionResult(None, matrix.simulations_used, 'prediction_unresolved',
                                       matrix.diagnostics)
    # Check membership without constructing a projected search problem or answer table.
    try:
        for world in protocol.worlds:
            budget.consume('response_checks')
            if tuple(world[i] for i in indexes) == matrix.responses:
                break
        else:
            return PartialPredictionResult(None, matrix.simulations_used, 'prediction_unresolved',
                ((candidate.model.name, 'PredictionDomainError', 'prediction_outside_response_universe'),))
    except SearchBudgetExceeded as error:
        return PartialPredictionResult(None, matrix.simulations_used, error.reason)
    prediction = PartialPrediction(digest, candidate.model.name, selected,
                                   matrix.responses, matrix.batch_bindings, max_simulations)
    return PartialPredictionResult(prediction, matrix.simulations_used, 'selected_matrix_replayed')


class _BoundedReplayEvaluator:
    """Replay original batch protocols under a separate execution ceiling."""
    def __init__(self, evaluator, limit):
        self.evaluator = evaluator or SatisfactionEvaluator()
        self.limit = limit
        self.used = 0

    def collect(self, model, context, horizon, original_budget):
        remaining = self.limit - self.used
        if remaining <= 0:
            raise RuntimeError('verification_simulation_budget_exhausted')
        cap = min(remaining, original_budget.max_simulations)
        try:
            batch = self.evaluator.collect(model, context, horizon,
                                          ResourceBudget(max_simulations=cap))
        except Exception:
            self.used = self.limit
            raise
        self.used += batch.simulations_used
        # Only complete, independently bound batches can be restated under the
        # original (possibly larger) limit. Never upgrade incomplete evidence.
        if batch.complete and batch.binds(model, context, horizon):
            limit = original_budget.max_simulations
            diagnostics = batch.diagnostics
            if cap < limit:
                diagnostics = tuple(d for d in diagnostics if d.code != 'iterator_limit_reached')
            digest = trace_batch_protocol_fingerprint(batch.model_fingerprint,
                batch.context_fingerprint, batch.horizon, limit, batch.coverage_authority,
                batch.complete, batch.coverage, tuple(d.code for d in diagnostics))
            return replace(batch, simulation_limit=limit, protocol_fingerprint=digest,
                           diagnostics=diagnostics)
        return batch


def verify_partial_prediction(protocol, candidate, cases, prediction, *,
                              max_simulations=10000, budget=None, evaluator=None):
    """Independently rerun the selected matrix; fingerprint equality alone is insufficient."""
    _natural(max_simulations)
    cases = tuple(cases)
    if prediction.input_fingerprint != _binding(protocol, candidate, cases):
        return PartialPredictionVerification('invalid', 'binding_mismatch', 0)
    names = tuple(e.name for e in protocol.experiments)
    selected = prediction.experiments
    if (not selected or selected != tuple(n for n in names if n in selected)
            or len(prediction.responses) != len(selected)
            or prediction.candidate != candidate.model.name):
        return PartialPredictionVerification('invalid', 'malformed_prediction', 0)
    if prediction.simulation_limit is None:
        return PartialPredictionVerification('undecided', 'missing_collection_protocol', 0)
    if type(prediction.simulation_limit) is not int or prediction.simulation_limit < 0:
        return PartialPredictionVerification('invalid', 'invalid_collection_protocol', 0)
    bounded = _BoundedReplayEvaluator(evaluator, max_simulations)
    replay = collect_partial_prediction(protocol, candidate, cases, selected,
        max_simulations=prediction.simulation_limit, budget=budget, evaluator=bounded)
    if replay.prediction is None:
        return PartialPredictionVerification('undecided', replay.reason, bounded.used)
    if replay.prediction != prediction:
        return PartialPredictionVerification('invalid', 'replay_disagrees', bounded.used)
    return PartialPredictionVerification('valid', 'selected_matrix_replayed', bounded.used)



@dataclass(frozen=True)
class CandidateExclusionCertificate:
    """One authenticated-by-replay prediction contradicts one supplied observation.

    This rejects only this candidate/version, never its structural descendants.
    Observation provenance remains an experiment-owner declaration.
    """
    prediction: PartialPrediction
    observation: object


@dataclass(frozen=True)
class EvidenceScreeningResult:
    declaration_fingerprint: str
    evidence: tuple
    excluded: tuple
    matching_evidence: tuple
    undecided: tuple
    certificates: tuple
    simulations_used: int
    reason: str
    diagnostics: tuple = ()


def verify_candidate_exclusion(protocol, candidate, cases, certificate, evidence, *,
                               max_simulations=10000, budget=None, evaluator=None):
    observation = certificate.observation
    prediction = certificate.prediction
    if observation not in evidence:
        return PartialPredictionVerification('invalid', 'evidence_dependency_missing', 0)
    values = dict(zip(prediction.experiments, prediction.responses))
    if observation.experiment not in values or values[observation.experiment] == observation.response:
        return PartialPredictionVerification('invalid', 'no_prediction_conflict', 0)
    names = tuple(e.name for e in protocol.experiments)
    budget = budget if budget is not None else SearchWorkBudget()
    if observation.experiment not in names:
        return PartialPredictionVerification('invalid', 'observation_outside_domain', 0)
    try:
        for row in protocol.worlds:
            budget.consume('response_checks')
            if row[names.index(observation.experiment)] == observation.response:
                break
        else:
            return PartialPredictionVerification('invalid', 'observation_outside_domain', 0)
    except SearchBudgetExceeded as error:
        return PartialPredictionVerification('undecided', error.reason, 0)
    return verify_partial_prediction(protocol, candidate, cases, prediction,
        max_simulations=max_simulations, budget=budget, evaluator=evaluator)
