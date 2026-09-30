"""Optional finite conflict explanations and minimum commitment retractions."""
from dataclasses import dataclass
from itertools import combinations

from .search import SearchBudgetExceeded, SearchWorkBudget, SearchObservation, _well_formed_conflict, _natural


@dataclass(frozen=True)
class ConsistencyRepairCertificate:
    """All smallest commitment removals for this exact evidence and catalogue."""
    problem_fingerprint: str
    commitments: tuple
    evidence: tuple
    core: object
    retractions: tuple


@dataclass(frozen=True)
class ConsistencyRepairResult:
    status: str  # verified / already_consistent / not_applicable / undecided
    reason: str
    certificate: object = None
    feasible_retractions: tuple = ()  # diagnostic witnesses if interrupted
    subsets_checked: int = 0


def suggest_consistency_repairs(problem, commitments, evidence, *,
                                max_subsets=10000, budget=None):
    """Find all minimum-cardinality removals without changing observations.

    A budget interruption may retain feasible removals, but never certifies
    their minimality or completeness. This task is separate from an unsat core.
    """
    _natural(max_subsets)
    budget = budget if budget is not None else SearchWorkBudget()
    declared = tuple(commitments)
    names = tuple(dict.fromkeys(declared))
    if len(names) != len(declared):
        raise ValueError('commitments must be distinct')
    known = {rule.name for rule in problem.protocol.constraints}
    if any(name not in known for name in names):
        raise ValueError('unknown commitment')
    evidence = tuple(evidence)
    checked, feasible, core = 0, [], None
    try:
        evidence = problem._evidence(evidence, budget)
        core = problem.learn_conflict(names, evidence, budget=budget)
        if core is None:
            if not problem._has_world(evidence=evidence, budget=budget):
                return ConsistencyRepairResult('not_applicable', 'evidence_inconsistent')
            if not problem._has_world(names, budget=budget):
                return ConsistencyRepairResult('not_applicable', 'commitments_inconsistent_alone')
            return ConsistencyRepairResult('already_consistent', 'no_empirical_conflict')
        for size in range(1, len(names) + 1):
            feasible = []
            for removed in combinations(names, size):
                if checked >= max_subsets:
                    return ConsistencyRepairResult('undecided', 'subset_limit_reached',
                        feasible_retractions=tuple(feasible), subsets_checked=checked)
                budget.consume('subset_checks')
                checked += 1
                survivors = tuple(name for name in names if name not in removed)
                if problem._has_world(survivors, evidence, budget=budget):
                    feasible.append(removed)
            if feasible:
                certificate = ConsistencyRepairCertificate(problem.fingerprint,
                    names, evidence, core, tuple(feasible))
                return ConsistencyRepairResult('verified', 'all_minimum_retractions',
                    certificate, tuple(feasible), checked)
        raise AssertionError('removing every commitment must restore consistent evidence')
    except SearchBudgetExceeded as error:
        return ConsistencyRepairResult('undecided', error.reason,
            feasible_retractions=tuple(feasible), subsets_checked=checked)


def verify_consistency_repairs(problem, certificate, evidence, *, budget=None):
    """Independently verify scope, conflict, sufficiency and exact minimum set."""
    budget = budget if budget is not None else SearchWorkBudget()
    if (not isinstance(certificate, ConsistencyRepairCertificate)
            or not isinstance(certificate.problem_fingerprint, str)
            or not _well_formed_conflict(certificate.core)
            or not isinstance(certificate.commitments, tuple)
            or any(not isinstance(name, str) for name in certificate.commitments)
            or not isinstance(certificate.evidence, tuple)
            or any(not isinstance(o, SearchObservation) for o in certificate.evidence)
            or not isinstance(certificate.retractions, tuple)
            or any(not isinstance(row, tuple) or any(not isinstance(name, str) for name in row)
                   for row in certificate.retractions)):
        return 'invalid'
    try:
        budget.consume('certificate_checks')
        active = problem._evidence(evidence, budget)
        if (certificate.problem_fingerprint != problem.fingerprint
                or tuple(certificate.evidence) != active):
            return 'invalid'
        names = tuple(certificate.commitments)
        if (not names or len(set(names)) != len(names)
                or any(name not in {r.name for r in problem.protocol.constraints}
                       for name in names)
                or not problem._has_world(evidence=active, budget=budget)
                or not problem._has_world(names, budget=budget)
                or not problem.validates_conflict(certificate.core, active, budget=budget)
                or not set(certificate.core.commitments).issubset(names)):
            return 'invalid'
        proposed = tuple(tuple(removed) for removed in certificate.retractions)
        if not proposed or len(set(proposed)) != len(proposed):
            return 'invalid'
        size = len(proposed[0])
        if size < 1 or size > len(names):
            return 'invalid'
        actual = []
        for k in range(1, size + 1):
            for removed in combinations(names, k):
                budget.consume('subset_checks')
                survivors = tuple(name for name in names if name not in removed)
                if problem._has_world(survivors, active, budget=budget):
                    if k < size:
                        return 'invalid'
                    actual.append(removed)
        return 'valid' if tuple(actual) == proposed else 'invalid'
    except SearchBudgetExceeded:
        return 'undecided'
    except (AttributeError, TypeError, ValueError):
        return 'invalid'
