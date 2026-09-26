"""Exact finite relational gluing, relative to declared global assignments.

This is an assignment-level check, not a probability-marginal gluing solver.
Pairwise overlap consistency does not imply a global section.
"""
from dataclasses import asdict, dataclass
from itertools import combinations, product

from .search import SearchBudgetExceeded, SearchWorkBudget, _name
from .structural import fingerprint_value


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


def solve_gluing(problem, *, minimize_core=True, budget=None):
    budget = budget if budget is not None else SearchWorkBudget()
    names = tuple(n for n, _ in problem.domains)
    overlap = None
    absent = False
    overlap_complete = False
    core = tuple(p.name for p in problem.locals)
    by_name = {p.name: p for p in problem.locals}
    def witness(selected):
        for row in problem.rows():
            budget.consume('candidate_checks')
            for key in selected:
                patch = by_name[key]
                budget.consume('constraint_checks')
                if tuple(row[names.index(n)] for n in patch.variables) not in patch.assignments:
                    break
            else:
                return row
        return None
    def result(status, reason, row=None, minimal=False):
        return GluingReport(problem.fingerprint, overlap, status, reason, row,
                            core if absent else (), minimal)
    try:
        overlap = True
        for a, b in combinations(problem.locals, 2):
            shared = tuple(n for n in a.variables if n in b.variables)
            projections = []
            for patch in (a, b):
                values = set()
                for row in patch.assignments:
                    budget.consume('response_checks')
                    values.add(tuple(row[patch.variables.index(n)] for n in shared))
                projections.append(values)
            if projections[0] != projections[1]:
                overlap = False
        overlap_complete = True
        row = witness(core)
        if row is not None:
            return result('found', 'global_assignment_found', row)
        absent = True
        if minimize_core:
            for key in tuple(core):
                trial = tuple(n for n in core if n != key)
                if witness(trial) is None:
                    core = trial
        return result('absent', 'declared_global_class_exhausted', minimal=minimize_core)
    except SearchBudgetExceeded as error:
        if not overlap_complete:
            overlap = None
        # An already proved absence survives interruption of core minimization.
        return result('absent' if absent else 'unknown', error.reason)


def verify_gluing_report(problem, receipt, *, budget=None):
    """Recheck the asserted core, including valid interrupted minimizations."""
    budget = budget if budget is not None else SearchWorkBudget()
    if receipt.problem_fingerprint != problem.fingerprint:
        return 'invalid'
    if receipt.status == 'unknown':
        return 'undecided'
    if receipt.status not in ('found', 'absent'):
        return 'invalid'
    replay = solve_gluing(problem, minimize_core=False, budget=budget)
    if replay.status == 'unknown':
        return 'undecided'
    if replay.status != receipt.status or replay.overlap_consistent != receipt.overlap_consistent:
        return 'invalid'
    names = tuple(n for n, _ in problem.domains)
    patches = {p.name: p for p in problem.locals}
    def satisfies(row, core):
        for key in core:
            budget.consume('constraint_checks')
            patch = patches[key]
            if tuple(row[names.index(n)] for n in patch.variables) not in patch.assignments:
                return False
        return True
    def has_witness(core):
        for row in problem.rows():
            budget.consume('candidate_checks')
            if satisfies(row, core):
                return True
        return False
    try:
        if receipt.status == 'found':
            if receipt.conflict_core or receipt.core_minimal:
                return 'invalid'
            # Check membership in the declared global class, not just domains.
            for row in problem.rows():
                budget.consume('candidate_checks')
                if row == receipt.witness:
                    return 'valid' if satisfies(row, tuple(patches)) else 'invalid'
            return 'invalid'
        core = receipt.conflict_core
        if receipt.witness is not None or len(set(core)) != len(core) or any(n not in patches for n in core):
            return 'invalid'
        if has_witness(core):
            return 'invalid'
        if receipt.core_minimal:
            for name in core:
                if not has_witness(tuple(n for n in core if n != name)):
                    return 'invalid'
        return 'valid'
    except SearchBudgetExceeded:
        return 'undecided'
