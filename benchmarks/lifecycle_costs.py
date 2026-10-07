"""Full lifecycle diagnostics on fixed finite ladders; timings are not CI gates.

Run: PYTHONPATH=src python benchmarks/lifecycle_costs.py
Work caps bound semantic operations, not constructors, wall time or allocation.
"""
import json
import platform
import runpy
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from bidirectional_modeling import (ConstraintQuery, ExperimentHypothesisSearch, ResponseConstraint,
    SearchExperiment, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.provenance import model_declaration_fingerprint
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter
from bidirectional_modeling.search_queries import verify_query_result
from bidirectional_modeling.search_session import SearchSession
from bidirectional_modeling.search import SearchBudgetExceeded
from bidirectional_modeling.finite_replay import create_replay_bundle, replay_bundle


def measure(function):
    before, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    started = perf_counter()
    result = function()
    seconds = perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    return result, dict(seconds=seconds, additional_peak_bytes=max(0, peak-before),
                        retained_bytes=current-before)


def run(copies=(1, 8, 32), worlds=(8, 64, 512, 4096), max_operations=1000000):
    if tracemalloc.is_tracing():
        raise RuntimeError('lifecycle benchmark requires exclusive tracemalloc ownership')
    workflow_inputs = runpy.run_path(str(Path(__file__).resolve().parents[1] /
        'examples' / 'approval_workflow.py'))['workflow_inputs']
    results = []
    for backend in ('scan', 'indexed'):
        for count in copies:
            tracemalloc.start()
            started = perf_counter()
            try:
                inputs, construction = measure(lambda: workflow_inputs(count))
                protocol, candidates, cases, answers, evidence = inputs
                _, fingerprints = measure(lambda: (protocol.fingerprint,
                    tuple(model_declaration_fingerprint(c.model) for c in candidates)))
                prepared, prediction = measure(lambda: ExecutableSearchAdapter().prepare(
                    protocol, candidates, cases, target='controlled-release', world_answers=answers,
                    backend=backend, max_simulations=6*len(candidates)))
                session, initialization = measure(lambda: SearchSession.from_prepared(prepared, evidence))
                report, search = measure(lambda: session.run(budget=SearchWorkBudget(max_operations)))
                query = ConstraintQuery(('revocation-ignored',), evidence)
                receipt, query_cost = measure(lambda: session.search.query(query,
                    budget=SearchWorkBudget(max_operations)))
                verified, review = measure(lambda: verify_query_result(session.search, query, receipt,
                    budget=SearchWorkBudget(max_operations)))
                updates = []
                for cycle in range(3):
                    for data in (evidence[:2], evidence[:2] + (SearchObservation(
                            'after-revocation', 'pending', 'replacement-' + str(cycle)),)):
                        budget = SearchWorkBudget(max_operations)
                        revoked, cost = measure(lambda: session.replace_evidence(data, budget=budget))
                        refreshed, search_cost = measure(lambda: session.run(budget=budget))
                        updates.append(dict(revoked=len(revoked), update=cost, search=search_cost,
                            operations=budget.work.total, stop_reason=refreshed.stop_reason,
                            undecided=refreshed.undecided))
                bundle, archive = measure(lambda: create_replay_bundle(protocol, candidates, cases,
                    target='controlled-release', world_answers=answers, evidence=evidence, backend=backend,
                    max_operations=max_operations, max_simulations=6*len(candidates)))
                replay, replay_cost = measure(lambda: replay_bundle(bundle))
                completed = (not report.undecided and report.stop_reason not in ('work_budget_exhausted', 'cancelled')
                    and verified.status == 'valid' and replay.status == 'verified'
                    and all(not row['undecided'] and row['stop_reason'] not in ('work_budget_exhausted', 'cancelled')
                            for row in updates))
                results.append(dict(kind='workflow-lifecycle', backend=backend, candidates=len(candidates),
                    states_per_candidate=3, experiments=3, response_worlds=8,
                    construction=construction, fingerprints=fingerprints, prediction=prediction,
                    initialization=initialization, search=search, query=query_cost, review=review,
                    archive=archive, replay=replay_cost, archive_bytes=len(bundle.encode('utf-8')),
                    updates=updates, simulations_used=prepared.simulations_used,
                    search_work=asdict(report.work), review_work=asdict(verified.work),
                    status='completed' if completed else 'undecided',
                    seconds=perf_counter()-started))
                if completed:
                    assert report.compatible == ('guarded',) and replay.report.compatible == ('guarded',)
                    assert all(not row['undecided'] for row in updates)
            except SearchBudgetExceeded as error:
                results.append(dict(kind='workflow-lifecycle', backend=backend,
                    candidates=len(candidates), status='undecided', reason=error.reason,
                    operations=error.work.total, seconds=perf_counter()-started,
                    completed_lifecycle=False))
            finally:
                tracemalloc.stop()
        for size in worlds:
            for workload in ('early-witness', 'exhaustive-absence'):
                tracemalloc.start()
                try:
                    problem, construction = measure(lambda: ExperimentHypothesisSearch(SearchProtocol(
                        'scale-ladder', 'table', (SearchExperiment('read', 'identity readout'),),
                        tuple((str(i),) for i in range(size)), (ResponseConstraint('first', (0,)),)),
                        (), 'response', backend=backend))
                    _, fingerprints = measure(lambda: problem.fingerprint)
                    query = (ConstraintQuery(evidence=(SearchObservation('read', '0', 'fixture'),))
                        if workload == 'early-witness' else ConstraintQuery(('first',),
                            (SearchObservation('read', str(size-1), 'fixture'),)))
                    receipt, query_cost = measure(lambda: problem.query(query,
                        budget=SearchWorkBudget(max_operations)))
                    verified, review = measure(lambda: verify_query_result(problem, query, receipt,
                        budget=SearchWorkBudget(max_operations)))
                    results.append(dict(kind=workload, backend=backend, response_worlds=size,
                        construction=construction, fingerprints=fingerprints, query=query_cost,
                        review=review, query_work=asdict(receipt.work), review_work=asdict(verified.work),
                        status=receipt.status.value, review_status=verified.status))
                finally:
                    tracemalloc.stop()
    return dict(schema_version=1, python=platform.python_version(), platform=platform.platform(),
        max_operations_per_query_or_update_cycle=max_operations, fixture_observations=True,
        collection_latency_included=False, prediction_preparation_included=True,
        constructor_cost_included=True, fingerprint_cost_included=True,
        memory='tracemalloc Python allocations; per-phase additional peaks; not process RSS',
        timings='single diagnostic sample with tracing overhead; no speed threshold', results=results)


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
