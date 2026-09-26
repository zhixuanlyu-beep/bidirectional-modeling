"""Candidate-lazy executable queries, retaining full-matrix replay per candidate."""
from dataclasses import asdict, dataclass

from .search import (ExperimentHypothesisSearch, SearchBudgetExceeded, SearchHypothesis,
                     SearchWorkBudget, _natural)
from .search_adapter import ExecutableSearchAdapter, model_declaration_fingerprint
from .search_queries import (ConstraintQuery, LowerSubstituteQuery, QueryResult, QueryStatus,
                             _query_scope)
from .structural import fingerprint_value, isolated_copy


@dataclass(frozen=True)
class LazyQueryResult:
    # Receipt binds this immutable finite snapshot, not a future cache state.
    search: ExperimentHypothesisSearch
    receipt: QueryResult
    simulations_used: int
    resolved_candidates: tuple
    batch_bindings: tuple
    diagnostics: tuple
    declaration_fingerprint: str


class LazyExecutableSearch:
    """Resolve only needed candidates; cache only completely replayed predictions.

    Each candidate still executes two complete experiment matrices. No partial
    row is used to prune a candidate. Create a new instance for changed inputs.
    Instances are sequential, not thread-safe. Callback purity remains a caller
    obligation, as with ExecutableSearchAdapter.
    """

    def __init__(self, protocol, candidates, cases, *, target, world_answers,
                 backend='scan', evaluator=None):
        self._candidates = isolated_copy(tuple(candidates), purpose='lazy candidates')
        self._cases = isolated_copy(tuple(cases), purpose='lazy cases')
        self._protocol = protocol
        self._target = target
        self._answers = tuple(world_answers)
        self._backend = backend
        self._adapter = ExecutableSearchAdapter(evaluator)
        base = self._adapter.prepare(protocol, (), self._cases, target=target,
                                     world_answers=self._answers, backend=backend)
        placeholders = tuple(SearchHypothesis(c.model.name, None, 'unresolved',
                             c.description, (), c.materials) for c in self._candidates)
        self._search = base.search.with_hypotheses(placeholders)
        self._bindings = ()
        self._declaration = self._signature()

    def _signature(self):
        return fingerprint_value((self._protocol.fingerprint, self._target, self._answers,
            tuple((model_declaration_fingerprint(c.model), asdict(c.description),
                   c.commitments, c.materials) for c in self._candidates),
            tuple(c.fingerprint for c in self._cases)))

    @property
    def snapshot(self):
        return self._search

    def execute(self, query, *, max_simulations=10000, budget=None):
        """Simulation limit is per call; successful candidate cache survives calls.

        Work budget is shared with finite query evaluation. Cancellation is
        cooperative between candidates; one candidate replay is atomic here.
        """
        _natural(max_simulations)
        if self._signature() != self._declaration:
            raise ValueError('lazy declaration changed; create a new instance')
        _query_scope(self._search, query)
        budget = budget if budget is not None else SearchWorkBudget()
        used, resolved, diagnostics = 0, [], []
        receipt = self._search.query(query, budget=budget)
        if isinstance(query, ConstraintQuery):
            order = ()
        elif isinstance(query, LowerSubstituteQuery):
            order = (query.candidate,) + query.lower_names if query.lower_names else ()
        else:
            order = tuple(c.model.name for c in self._candidates)
        candidates = {c.model.name: c for c in self._candidates}
        for name in dict.fromkeys(order):
            if receipt.status is not QueryStatus.UNKNOWN:
                break
            if receipt.reason in ('work_budget_exhausted', 'cancelled'):
                break
            if any(h.name == name and h.world is not None for h in self._search.hypotheses):
                continue
            if used >= max_simulations:
                diagnostics.append((name, 'RuntimeError', 'simulation_budget_exhausted'))
                break
            try:
                budget.consume('query_checks')
            except SearchBudgetExceeded:
                receipt = self._search.query(query, budget=budget)
                break
            prepared = self._adapter.prepare(self._protocol, (candidates[name],), self._cases,
                target=self._target, world_answers=self._answers,
                max_simulations=max_simulations-used, backend=self._backend)
            used += prepared.simulations_used
            diagnostics.extend(prepared.diagnostics)
            # Reject drift before publishing any new cache entry or receipt.
            if self._signature() != self._declaration:
                raise ValueError('lazy declaration changed; create a new instance')
            hypothesis = prepared.search.hypotheses[0]
            if hypothesis.world is not None:
                self._search = self._search.with_hypotheses(tuple(
                    hypothesis if h.name == name else h for h in self._search.hypotheses))
                self._bindings += prepared.batch_bindings
                resolved.append(name)
            receipt = self._search.query(query, budget=budget)
        return LazyQueryResult(self._search, receipt, used, tuple(resolved),
                               self._bindings, tuple(diagnostics), self._declaration)
