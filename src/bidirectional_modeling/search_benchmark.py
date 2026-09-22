"""Controlled repeated-data benchmark; reports overhead rather than promising speedup."""
from dataclasses import asdict, dataclass, replace
from statistics import median
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
                                           backend="scan",world_answers=search.world_answers).search(evidence,learn_conflicts=False)
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
            current = ExperimentHypothesisSearch(replace(search.protocol),search.hypotheses,search.target,
                backend='indexed' if mode == 'indexed_conflict_reuse' else 'scan',world_answers=search.world_answers)
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
                        tuple(h for h in search.hypotheses if h.name not in failures),search.target,
                        world_answers=search.world_answers)
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


@dataclass(frozen=True)
class SearchBenchmarkStep:
    name: str
    evidence: tuple
    additions: tuple = ()

    def __post_init__(self):
        from .search import _name
        _name(self.name)
        object.__setattr__(self,'evidence',tuple(self.evidence))
        object.__setattr__(self,'additions',tuple(self.additions))


def benchmark_search_updates(search, steps, *, repetitions=3, max_operations=1_000_000):
    """Replay candidate growth and replacement evidence with independently checked truth.

    Each measured trial starts with a fresh protocol/cache and one cumulative
    budget. Prediction preparation and external acquisition remain out of scope.
    """
    _natural(repetitions)
    _natural(max_operations)
    if not repetitions:
        raise ValueError('repetitions must be positive')
    if tracemalloc.is_tracing():
        raise RuntimeError('benchmark needs exclusive tracemalloc ownership')
    steps=tuple(steps)
    if not steps:
        raise ValueError('at least one update step is required')
    reference=ExperimentHypothesisSearch(search.protocol,search.hypotheses,search.target,
                                         world_answers=search.world_answers)
    expected=[]
    for step in steps:
        reference=reference.with_hypotheses(reference.hypotheses+step.additions)
        if any(h.world is None for h in reference.hypotheses):
            raise ValueError('benchmark requires fully known candidate responses')
        report=reference.search(step.evidence,learn_conflicts=False)
        if report.undecided or report.stop_reason in ('work_budget_exhausted','cancelled'):
            raise ValueError('reference search was incomplete')
        expected.append(set(report.compatible))
    results=[]
    for strategy in ('full_replay','failed_model_cache','conflict_reuse','indexed_conflict_reuse'):
        trials=[]
        for _ in range(repetitions):
            budget=SearchWorkBudget(max_operations)
            stages=[]
            failures=set()
            certificates=()
            previous_evidence=None
            stop_reason='completed'
            tracemalloc.start()
            started=perf_counter()
            try:
                current=ExperimentHypothesisSearch(replace(search.protocol),search.hypotheses,search.target,
                    backend='indexed' if strategy=='indexed_conflict_reuse' else 'scan',
                    world_answers=search.world_answers)
                for step,truth in zip(steps,expected):
                    current=current.with_hypotheses(current.hypotheses+step.additions)
                    reset=previous_evidence is not None and previous_evidence != step.evidence
                    if reset:
                        failures.clear()
                    previous_evidence=step.evidence
                    evaluated=current.with_hypotheses(tuple(h for h in current.hypotheses if h.name not in failures)) \
                              if strategy=='failed_model_cache' else current
                    old_certificates=certificates
                    report=evaluated.search(step.evidence,certificates,budget=budget,
                        learn_conflicts=strategy in ('conflict_reuse','indexed_conflict_reuse'))
                    complete=not report.undecided and report.stop_reason not in ('work_budget_exhausted','cancelled')
                    excluded=set(report.pruned)|set(report.rejected)|failures
                    stages.append(dict(name=step.name,candidate_count=len(current.hypotheses),
                        added_count=len(step.additions),evidence_changed=reset,
                        cache_reset=reset and strategy=='failed_model_cache',
                        compatible=list(report.compatible),replay_checks=report.replay_checks,
                        agrees_with_reference=set(report.compatible)==truth if complete else None,
                        false_excluded=sorted(excluded & truth),stop_reason=report.stop_reason,
                        revoked_certificates=len(set(old_certificates)-set(report.conflicts)) if complete else None,
                        work=asdict(budget.work)))
                    if not complete:
                        stop_reason=report.stop_reason
                        break
                    if strategy=='failed_model_cache':
                        failures.update(report.rejected)
                    elif strategy in ('conflict_reuse','indexed_conflict_reuse'):
                        certificates=report.conflicts
                elapsed=perf_counter()-started
                _,peak=tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            trials.append(dict(seconds=elapsed,peak_bytes=peak,stop_reason=stop_reason,
                               completed_steps=len(stages) if stop_reason=='completed' else len(stages)-1,
                               stages=stages,total_operations=budget.work.total))
        complete=all(t['completed_steps']==len(steps) for t in trials)
        results.append(dict(strategy=strategy,trials=trials,complete=complete,
            median_seconds=median(t['seconds'] for t in trials) if complete else None,
            median_peak_bytes=median(t['peak_bytes'] for t in trials) if complete else None,
            agrees_with_reference=all(s['agrees_with_reference'] for t in trials for s in t['stages']) if complete else None))
    return dict(problem_fingerprint=search.fingerprint,repetitions=repetitions,
                step_count=len(steps),prediction_preparation_included=False,
                new_experiments=0,results=results)
