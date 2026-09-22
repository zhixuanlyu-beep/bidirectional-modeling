"""Controlled repeated-data benchmark; reports overhead rather than promising speedup."""
from dataclasses import asdict
from time import perf_counter
import tracemalloc

from .search import ExperimentHypothesisSearch, SearchWorkBudget, _natural


def benchmark_search(search, evidence, *, rounds=5, max_operations=1_000_000):
    _natural(rounds)
    _natural(max_operations)
    if not rounds:
        raise ValueError('rounds must be positive')
    if tracemalloc.is_tracing():
        raise RuntimeError('benchmark needs exclusive tracemalloc ownership')
    evidence = tuple(evidence)
    if any(h.world is None for h in search.hypotheses):
        raise ValueError('benchmark requires fully known candidate responses')
    reference = ExperimentHypothesisSearch(search.protocol,search.hypotheses,search.target,
                                           backend="scan").search(evidence,learn_conflicts=False)
    if reference.undecided:
        raise ValueError('reference search was incomplete')
    expected = set(reference.compatible)
    results = []
    for mode in ('full_replay','failed_model_cache','conflict_reuse','indexed_conflict_reuse'):
        budget = SearchWorkBudget(max_operations)
        failures, certificates, replay_checks = set(), (), 0
        completed, equivalent, false_pruned = 0, True, set()
        stop_reason = "completed"
        tracemalloc.start()
        started = perf_counter()
        try:
            current = ExperimentHypothesisSearch(search.protocol,search.hypotheses,search.target,
                backend='indexed' if mode == 'indexed_conflict_reuse' else 'scan')
            for _ in range(rounds):
                report = current.search(evidence,certificates,budget=budget,
                                        learn_conflicts=mode in ('conflict_reuse','indexed_conflict_reuse'))
                replay_checks += report.replay_checks
                false_pruned.update(set(report.pruned) & expected)
                if report.undecided or report.stop_reason in ('work_budget_exhausted','cancelled'):
                    stop_reason = report.stop_reason
                    break
                completed += 1
                equivalent = equivalent and set(report.compatible) == expected
                if mode == 'failed_model_cache':
                    failures.update(report.rejected)
                    # Cache is valid only for this exact fixed problem/evidence.
                    # No certificates/minimality claims use the reduced catalogue.
                    current = ExperimentHypothesisSearch(search.protocol,
                        tuple(h for h in search.hypotheses if h.name not in failures),search.target)
                elif mode in ('conflict_reuse','indexed_conflict_reuse'):
                    certificates = report.conflicts
            elapsed = perf_counter()-started
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        results.append(dict(strategy=mode,seconds=elapsed,peak_bytes=peak,
                            rounds_completed=completed,replay_checks=replay_checks,
                            work=asdict(budget.work),total_operations=budget.work.total,
                            agrees_with_reference=equivalent if completed == rounds else None,
                            stop_reason=stop_reason,false_pruned=sorted(false_pruned)))
    return dict(problem_fingerprint=search.fingerprint,rounds=rounds,
                prediction_preparation_included=False,new_experiments=0,
                results=results)
