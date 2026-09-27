"""Exact finite relational gluing, relative to declared global assignments.

This is an assignment-level check, not a probability-marginal gluing solver.
Pairwise overlap consistency does not imply a global section.
"""
from dataclasses import asdict, dataclass
from itertools import combinations, product

from ..search import SearchBudgetExceeded, SearchWorkBudget, _name
from ..structural import fingerprint_value


@dataclass(frozen=True)
class LocalDescription:
    name: str
    variables: tuple
    assignments: tuple

    def __post_init__(self):
        _name(self.name)
        object.__setattr__(self, 'variables', tuple(self.variables))
        object.__setattr__(self, 'assignments', tuple(tuple(r) for r in self.assignments))
        if not self.variables or len(set(self.variables)) != len(self.variables):
            raise ValueError('declare distinct local variables')
        if any(len(r) != len(self.variables) for r in self.assignments):
            raise ValueError('malformed local assignment')
        for value in self.variables:
            _name(value)
        for row in self.assignments:
            for value in row:
                _name(value)
        if len(set(self.assignments)) != len(self.assignments):
            raise ValueError('duplicate local assignment')


@dataclass(frozen=True)
class GluingProblem:
    # Ordered (variable, finite string domain) pairs; None means the full product.
    domains: tuple
    locals: tuple
    global_assignments: object = None

    def __post_init__(self):
        domains = tuple((name, tuple(values)) for name, values in self.domains)
        object.__setattr__(self, 'domains', domains)
        object.__setattr__(self, 'locals', tuple(self.locals))
        names = tuple(n for n, _ in domains)
        if not names or len(set(names)) != len(names) or not self.locals:
            raise ValueError('nonempty domains and local descriptions required')
        for name, values in domains:
            _name(name)
            if not values or len(set(values)) != len(values):
                raise ValueError('nonempty distinct domain values required')
            for value in values:
                _name(value)
        if len({p.name for p in self.locals}) != len(self.locals):
            raise ValueError('duplicate local name')
        domain = dict(domains)
        for patch in self.locals:
            if any(n not in domain for n in patch.variables):
                raise ValueError('unknown local variable')
            if any(any(v not in domain[n] for n, v in zip(patch.variables, row)) for row in patch.assignments):
                raise ValueError('local assignment outside domain')
        if self.global_assignments is not None:
            rows = tuple(tuple(r) for r in self.global_assignments)
            if any(len(r) != len(names) or any(v not in domain[n] for n, v in zip(names, r)) for r in rows):
                raise ValueError('global assignment outside domain')
            object.__setattr__(self, 'global_assignments', rows)

    @property
    def fingerprint(self):
        return fingerprint_value(('finite-gluing-v1', asdict(self)))

    def rows(self):
        return iter(self.global_assignments) if self.global_assignments is not None else product(
            *(values for _, values in self.domains))


@dataclass(frozen=True)
class GluingReport:
    problem_fingerprint: str
    overlap_consistent: object
    status: str  # found / absent / unknown
    reason: str
    witness: object = None
    conflict_core: tuple = ()
    core_minimal: bool = False


def _overlap_consistent(problem, budget):
    consistent = True
    for a, b in combinations(problem.locals, 2):
        shared = tuple(n for n in a.variables if n in b.variables)
        projections = []
        for patch in (a, b):
            values = set()
            for row in patch.assignments:
                budget.consume('response_checks')
                values.add(tuple(row[patch.variables.index(n)] for n in shared))
            projections.append(values)
        consistent = consistent and projections[0] == projections[1]
    return consistent


def _satisfies(problem, row, selected, budget):
    names = tuple(n for n, _ in problem.domains)
    for patch in selected:
        budget.consume('constraint_checks')
        if tuple(row[names.index(n)] for n in patch.variables) not in patch.assignments:
            return False
    return True


def _witness(problem, selected, budget):
    for row in problem.rows():
        budget.consume('candidate_checks')
        if _satisfies(problem, row, selected, budget):
            return row
    return None


def solve_gluing(problem, *, minimize_core=True, budget=None):
    budget = budget if budget is not None else SearchWorkBudget()
    overlap, absent = None, False
    core = problem.locals
    def result(status, reason, row=None, minimal=False):
        return GluingReport(problem.fingerprint, overlap, status, reason, row,
                            tuple(p.name for p in core) if absent else (), minimal)
    try:
        overlap = _overlap_consistent(problem, budget)
        row = _witness(problem, core, budget)
        if row is not None:
            return result('found', 'global_assignment_found', row)
        absent = True
        if minimize_core:
            for patch in tuple(core):
                trial = tuple(p for p in core if p != patch)
                if _witness(problem, trial, budget) is None:
                    core = trial
        return result('absent', 'declared_global_class_exhausted', minimal=minimize_core)
    except SearchBudgetExceeded as error:
        return result('absent' if absent else 'unknown', error.reason)


def verify_gluing_report(problem, receipt, *, budget=None):
    """Check the supplied witness/core without repeating the original search."""
    budget = budget if budget is not None else SearchWorkBudget()
    if receipt.problem_fingerprint != problem.fingerprint:
        return 'invalid'
    if receipt.status == 'unknown':
        return 'undecided'
    if receipt.status not in ('found', 'absent'):
        return 'invalid'
    patches = {p.name: p for p in problem.locals}
    try:
        budget.consume('certificate_checks')
        if _overlap_consistent(problem, budget) != receipt.overlap_consistent:
            return 'invalid'
        if receipt.status == 'found':
            row = receipt.witness
            if (receipt.conflict_core or receipt.core_minimal or not isinstance(row, tuple)
                    or len(row) != len(problem.domains)):
                return 'invalid'
            for value, (_, domain) in zip(row, problem.domains):
                budget.consume('response_checks')
                if value not in domain:
                    return 'invalid'
            if problem.global_assignments is not None:
                for allowed in problem.global_assignments:
                    budget.consume('candidate_checks')
                    if row == allowed:
                        break
                else:
                    return 'invalid'
            return 'valid' if _satisfies(problem, row, problem.locals, budget) else 'invalid'
        names = receipt.conflict_core
        if receipt.witness is not None or len(set(names)) != len(names) or any(n not in patches for n in names):
            return 'invalid'
        core = tuple(patches[n] for n in names)
        if _witness(problem, core, budget) is not None:
            return 'invalid'
        if receipt.core_minimal:
            for patch in core:
                if _witness(problem, tuple(p for p in core if p != patch), budget) is None:
                    return 'invalid'
        return 'valid'
    except SearchBudgetExceeded:
        return 'undecided'


__all__ = ['LocalDescription', 'GluingProblem', 'GluingReport', 'solve_gluing', 'verify_gluing_report']
