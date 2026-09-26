"""Candidate-lazy executable queries, retaining full-matrix replay per candidate."""
from dataclasses import asdict, dataclass

from .search import (ExperimentHypothesisSearch, SearchBudgetExceeded, SearchHypothesis,
                     SearchWorkBudget, _natural)
from .search_adapter import ExecutableSearchAdapter, model_declaration_fingerprint
from .search_queries import (ConstraintQuery, LowerSubstituteQuery, QueryResult, QueryStatus,
                             _query_scope)
from .structural import fingerprint_value, isolated_copy
from .search_partial import collect_partial_prediction, PartialPredictionResult


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
        self._partial = {}
        self._invalidated = False
        self._declaration = self._signature()

    def _signature(self):
        return fingerprint_value((self._protocol.fingerprint, self._target, self._answers,
            tuple((model_declaration_fingerprint(c.model), asdict(c.description),
                   c.commitments, c.materials) for c in self._candidates),
            tuple(c.fingerprint for c in self._cases)))

    @property
    def snapshot(self):
        self._check_declaration()
        return self._search

    def _check_declaration(self):
        if self._invalidated or self._signature() != self._declaration:
            self._invalidated = True
            raise ValueError('lazy declaration changed; create a new instance')

    def _reject_cached_drift(self, diagnostics):
        for name, _, reason in diagnostics:
            if reason in ('model_declaration_changed', 'non_deterministic_response',
                          'prediction_outside_response_universe') and (
                    name in self._partial or any(h.name == name and h.world is not None
                                                 for h in self._search.hypotheses)):
                self._invalidated = True
                raise ValueError('prediction drift detected; create a new instance')

    def predict_experiments(self, candidate, experiments, *, max_simulations=10000, budget=None):
        """Extend a candidate's selected matrix, replaying the entire union twice.

        Complete predictions may enter the query cache after commitment validation.
        Partial predictions never change finite query hypotheses or prune candidates.
        """
        _natural(max_simulations)
        self._check_declaration()
        candidates = {c.model.name: c for c in self._candidates}
        if candidate not in candidates:
            raise ValueError('unknown candidate')
        names = tuple(e.name for e in self._protocol.experiments)
        requested = tuple(experiments)
        if not requested or len(set(requested)) != len(requested) or any(n not in names for n in requested):
            raise ValueError('select distinct experiments from the declared domain')
        budget = budget if budget is not None else SearchWorkBudget()
        try:
            budget.consume('query_checks')
        except SearchBudgetExceeded as error:
            return PartialPredictionResult(None, 0, error.reason)
        cached = self._partial.get(candidate)
        if cached is not None and set(requested) <= set(cached.experiments):
            return PartialPredictionResult(cached, 0, 'cached_selected_matrix')
        selected = tuple(n for n in names if n in requested or
                         (cached is not None and n in cached.experiments))
        result = collect_partial_prediction(self._protocol, candidates[candidate], self._cases,
            selected, max_simulations=max_simulations, budget=budget,
            evaluator=self._adapter.evaluator)
        self._check_declaration()
        self._reject_cached_drift(result.diagnostics)
        prediction = result.prediction
        if prediction is None:
            return result
        values = dict(zip(prediction.experiments, prediction.responses))
        if cached is not None and any(values[n] != r for n, r in zip(cached.experiments, cached.responses)):
            self._invalidated = True
            raise ValueError('partial response changed; create a new instance')
        known = next(h for h in self._search.hypotheses if h.name == candidate)
        if known.world is not None and any(self._protocol.worlds[known.world][names.index(n)] != r
                                          for n, r in values.items()):
            self._invalidated = True
            raise ValueError('partial response changed; create a new instance')
        if selected == names:
            world = self._protocol.worlds.index(prediction.responses)
            c = candidates[candidate]
            hypothesis = SearchHypothesis(candidate, world, self._answers[world],
                                          c.description, c.commitments, c.materials)
            # Validate full-world commitments before publishing either cache.
            self._search = self._search.with_hypotheses(tuple(
                hypothesis if h.name == candidate else h for h in self._search.hypotheses))
            self._bindings = tuple(b for b in self._bindings if b[0] != candidate) + prediction.batch_bindings
        self._partial[candidate] = prediction
        return result

    def execute(self, query, *, max_simulations=10000, budget=None):
        """Simulation limit is per call; successful candidate cache survives calls.

        Work budget is shared with finite query evaluation. Cancellation is
        cooperative between candidates; one candidate replay is atomic here.
        """
        _natural(max_simulations)
        self._check_declaration()
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
        hypotheses = {h.name: h for h in self._search.hypotheses}
        new_bindings = []
        # Filter evidence once. Completed candidates are tested individually;
        # only the initial and final receipts scan the whole catalogue.
        possible = None
        if not isinstance(query, (ConstraintQuery, LowerSubstituteQuery)) and receipt.status is QueryStatus.UNKNOWN:
            try:
                possible = self._search._worlds(evidence=query.evidence, budget=budget)
            except SearchBudgetExceeded:
                receipt = self._search.query(query, budget=budget)
        for name in dict.fromkeys(order):
            if receipt.status is not QueryStatus.UNKNOWN:
                break
            if receipt.reason in ('work_budget_exhausted', 'cancelled'):
                break
            if hypotheses[name].world is not None:
                continue
            if used >= max_simulations:
                diagnostics.append((name, 'RuntimeError', 'simulation_budget_exhausted'))
                break
            try:
                budget.consume('query_checks')
            except SearchBudgetExceeded:
                break
            prepared = self._adapter.prepare(self._protocol, (candidates[name],), self._cases,
                target=self._target, world_answers=self._answers,
                max_simulations=max_simulations-used, backend=self._backend)
            used += prepared.simulations_used
            diagnostics.extend(prepared.diagnostics)
            self._reject_cached_drift(prepared.diagnostics)
            hypothesis = prepared.search.hypotheses[0]
            if hypothesis.world is not None:
                partial = self._partial.get(name)
                names = tuple(e.name for e in self._protocol.experiments)
                if partial is not None and any(
                        self._protocol.worlds[hypothesis.world][names.index(n)] != r
                        for n, r in zip(partial.experiments, partial.responses)):
                    self._invalidated = True
                    raise ValueError('partial response changed; create a new instance')
                hypotheses[name] = hypothesis
                new_bindings.extend(prepared.batch_bindings)
                resolved.append(name)
                try:
                    budget.consume('candidate_checks')
                except SearchBudgetExceeded:
                    break
                if isinstance(query, LowerSubstituteQuery):
                    target = hypotheses[query.candidate]
                    if name == query.candidate:
                        # Scan cached lower candidates once after resolving the target.
                        try:
                            found = False
                            for lower in query.lower_names:
                                budget.consume('candidate_checks')
                                if hypotheses[lower].world == target.world:
                                    found = True
                                    break
                        except SearchBudgetExceeded:
                            break
                        if found:
                            break
                    elif target.world is not None and hypothesis.world == target.world:
                        break
                elif hypothesis.world in possible and hypothesis.macro_answer != query.answer:
                    break
        # Publish successful candidates together; avoid rebuilding/fingerprinting
        # the whole catalogue for each candidate. Prior returned snapshots stay fixed.
        self._check_declaration()
        if resolved:
            self._search = self._search.with_hypotheses(tuple(hypotheses.values()))
            self._bindings += tuple(new_bindings)
        if order and receipt.status is QueryStatus.UNKNOWN:
            receipt = self._search.query(query, budget=budget)
        return LazyQueryResult(self._search, receipt, used, tuple(resolved),
                               self._bindings, tuple(diagnostics), self._declaration)
