"""Replayable predictions for an explicitly selected experiment submatrix.

These are model predictions, never observations or complete-world witnesses.
"""
from dataclasses import asdict, dataclass, replace
from typing import Optional, Tuple

from .search import SearchBudgetExceeded, SearchProtocol, SearchWorkBudget, _natural
from .search_adapter import ExecutableSearchAdapter, model_declaration_fingerprint
from .structural import fingerprint_value, isolated_copy


@dataclass(frozen=True)
class PartialPrediction:
    input_fingerprint: str
    candidate: str
    experiments: Tuple[str, ...]
    responses: Tuple[str, ...]
    batch_bindings: tuple

    def __post_init__(self):
        for field in ('experiments', 'responses', 'batch_bindings'):
            object.__setattr__(self, field, tuple(getattr(self, field)))

    @property
    def fingerprint(self):
        return fingerprint_value(('partial-prediction-v1', asdict(self)))


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
    # Projection may collapse different full worlds; retain unique partial rows.
    projected = SearchProtocol(protocol.scope, protocol.coding,
        tuple(protocol.experiments[i] for i in indexes),
        tuple(dict.fromkeys(tuple(w[i] for i in indexes) for w in protocol.worlds)))
    prepared = ExecutableSearchAdapter(evaluator).prepare(projected,
        (replace(candidate, commitments=()),), tuple(cases[i] for i in indexes),
        target='partial-response', world_answers=('prediction',) * len(projected.worlds),
        max_simulations=max_simulations)
    if _binding(protocol, candidate, cases) != digest:
        return PartialPredictionResult(None, prepared.simulations_used, 'input_declaration_changed')
    h = prepared.search.hypotheses[0]
    if h.world is None:
        return PartialPredictionResult(None, prepared.simulations_used, 'prediction_unresolved',
                                       prepared.diagnostics)
    prediction = PartialPrediction(digest, candidate.model.name, selected,
                                   projected.worlds[h.world], prepared.batch_bindings)
    return PartialPredictionResult(prediction, prepared.simulations_used, 'selected_matrix_replayed')


def verify_partial_prediction(protocol, candidate, cases, prediction, *,
                              max_simulations=10000, budget=None, evaluator=None):
    """Independently rerun the selected matrix; fingerprint equality alone is insufficient."""
    cases = tuple(cases)
    if prediction.input_fingerprint != _binding(protocol, candidate, cases):
        return PartialPredictionVerification('invalid', 'binding_mismatch', 0)
    names = tuple(e.name for e in protocol.experiments)
    selected = prediction.experiments
    if (not selected or selected != tuple(n for n in names if n in selected)
            or len(prediction.responses) != len(selected)
            or prediction.candidate != candidate.model.name):
        return PartialPredictionVerification('invalid', 'malformed_prediction', 0)
    replay = collect_partial_prediction(protocol, candidate, cases, selected,
        max_simulations=max_simulations, budget=budget, evaluator=evaluator)
    if replay.prediction is None:
        return PartialPredictionVerification('undecided', replay.reason, replay.simulations_used)
    if replay.prediction != prediction:
        return PartialPredictionVerification('invalid', 'replay_disagrees', replay.simulations_used)
    return PartialPredictionVerification('valid', 'selected_matrix_replayed', replay.simulations_used)
