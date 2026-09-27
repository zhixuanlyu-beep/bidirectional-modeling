"""Small deterministic workloads; counters are gates, timings are diagnostics.

Run: PYTHONPATH=src python benchmarks/review_costs.py
Constructors are outside measured calls. Screening includes declaration checks
and simulation; its declaration counter covers catalogue-boundary hashes only.
"""
import json
from time import perf_counter
from unittest.mock import patch

from bidirectional_modeling import (
    ConstraintQuery, Context, DescriptionLength, ExperimentHypothesisSearch,
    LazyExecutableSearch, ModelSearchCandidate, ModelSearchCase, ScenarioKey,
    SearchExperiment, SearchObservation, SearchProtocol, SearchWorkBudget,
    verify_query_result,
)
from bidirectional_modeling.context_network import (
    ContextChange, ContextTransition, ModelingContext, validate_context_transition,
)
from bidirectional_modeling.extensions.boolean import BooleanExpression
import bidirectional_modeling.search_lazy as lazy_module


def protocol(size):
    return SearchProtocol('scope', 'code', (SearchExperiment('a', 'read'),),
                          tuple((str(i),) for i in range(size)))


def run():
    rows = []
    for size in (10, 1000):
        p = protocol(size)
        problem = ExperimentHypothesisSearch(p, (), 'out')
        query = ConstraintQuery(evidence=(SearchObservation('a', '0', 'lab'),))
        # Binding preparation is explicit, outside semantic query/verification work.
        started = perf_counter()
        problem.fingerprint
        binding_seconds = perf_counter() - started
        started = perf_counter()
        receipt = problem.query(query)
        elapsed = perf_counter() - started
        verified = verify_query_result(problem, query, receipt)
        assert receipt.witness_world == 0 and verified.status == 'valid'
        assert receipt.work.total <= 10 and verified.work.total <= 3
        rows.append(dict(kind='query', worlds=size, binding_seconds=binding_seconds,
                         query_seconds=elapsed, query_operations=receipt.work.total,
                         verification_operations=verified.work.total))
        context = ModelingContext('c', p, 'lab', 'exact', ('x',), 'finite', 'out')
        transition = ContextTransition(context, context, ContextChange.REFINEMENT,
            (('a', 'a'),), tuple(('a', str(i), str(i)) for i in range(size)))
        budget = SearchWorkBudget()
        started = perf_counter()
        report = validate_context_transition(transition, budget=budget)
        assert report.status == 'valid' and budget.work.total <= 4*size
        rows.append(dict(kind='relation', worlds=size, preparation_operations=budget.work.total,
                         seconds=perf_counter()-started))
    for size in (4, 8, 16):
        p = protocol(2)
        candidates = tuple(ModelSearchCandidate(BooleanExpression(('false',)).to_model(str(i)),
                                               DescriptionLength()) for i in range(size))
        cases = (ModelSearchCase('a', Context(), ScenarioKey('s', 'baseline'), 'y'),)
        lazy = LazyExecutableSearch(p, candidates, cases, target='out', world_answers=('no', 'yes'))
        evidence = (SearchObservation('a', '1', 'lab'),)
        for cache in ('cold', 'warm'):
            with patch.object(lazy_module, 'model_declaration_fingerprint',
                              wraps=lazy_module.model_declaration_fingerprint) as calls:
                started = perf_counter()
                report = lazy.screen_evidence(evidence)
                seconds = perf_counter()-started
                assert len(report.excluded) == size and calls.call_count <= 2*size
                assert report.simulations_used == (2*size if cache == 'cold' else 0)
                rows.append(dict(kind='screen', candidates=size, cache=cache,
                    simulations=report.simulations_used, catalogue_declaration_hashes=calls.call_count,
                    seconds=seconds))
    return dict(constructor_cost_included=False, timing_is_diagnostic=True, results=rows)


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
