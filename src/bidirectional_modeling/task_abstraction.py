"""Task-relative factorization and congruence for explicit finite tables.

No callbacks or purpose labels serve as proofs. Responses are discrete strings;
operation tables include every concrete input tuple, with None meaning a known
undefined operation. It never means an unknown prediction.
"""
from dataclasses import dataclass
from itertools import product
from typing import Optional, Tuple

from .structural import fingerprint_value, ordered_tuple


DOMAIN = 'finite-task-abstraction-v1'


def _names(values):
    values = ordered_tuple(values)
    if not values or any(type(v) is not str or not v.strip() for v in values):
        raise ValueError('declare nonempty ordered names')
    if len(set(values)) != len(values):
        raise ValueError('names must be unique')
    return values


def _indices(values):
    values = ordered_tuple(values)
    if any(type(v) is not int or v < 0 for v in values):
        raise ValueError('indices must be nonnegative integers')
    return values


def _responses(rows):
    rows = tuple(ordered_tuple(row) for row in ordered_tuple(rows))
    if any(type(v) is not str for row in rows for v in row):
        raise ValueError('responses must be discrete string labels')
    return rows


def _limit(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('max_checks must be a nonnegative integer or None')


def _offset(inputs, size):
    index = 0
    for value in inputs:
        index = index * size + value
    return index


@dataclass(frozen=True)
class FiniteOperation:
    """Lexicographic table over X**arity; None is known inapplicability."""
    name: str
    arity: int
    outputs: Tuple[Optional[int], ...]

    def __post_init__(self):
        _names((self.name,))
        if type(self.arity) is not int or self.arity < 1:
            raise ValueError('operation arity must be a positive integer')
        outputs = ordered_tuple(self.outputs)
        if any(v is not None and (type(v) is not int or v < 0) for v in outputs):
            raise ValueError('outputs must be nonnegative indices or known undefined')
        object.__setattr__(self, 'outputs', outputs)


@dataclass(frozen=True)
class FiniteTaskAbstraction:
    """Caller-declared scope, judgment columns, alpha and complete data tables.

    Projection must be onto the declared macro domain. complete is an explicit
    coverage declaration about scope, not an inference from table dimensions.
    """
    scope: str
    states: Tuple[str, ...]
    macro_states: Tuple[str, ...]
    judgments: Tuple[str, ...]
    projection: Tuple[int, ...]
    responses: Tuple[Tuple[str, ...], ...]
    operations: Tuple[FiniteOperation, ...] = ()
    complete: bool = False
    assumptions: Tuple[str, ...] = ()

    def __post_init__(self):
        _names((self.scope,))
        for field in ('states', 'macro_states', 'judgments'):
            object.__setattr__(self, field, _names(getattr(self, field)))
        object.__setattr__(self, 'projection', _indices(self.projection))
        object.__setattr__(self, 'responses', _responses(self.responses))
        object.__setattr__(self, 'operations', ordered_tuple(self.operations))
        assumptions = ordered_tuple(self.assumptions)
        if any(type(v) is not str or not v.strip() for v in assumptions):
            raise ValueError('assumptions must be explicit nonempty strings')
        object.__setattr__(self, 'assumptions', assumptions)
        if type(self.complete) is not bool:
            raise ValueError('coverage completeness must be an explicit boolean')
        n, m = len(self.states), len(self.macro_states)
        if len(self.projection) != n or set(self.projection) != set(range(m)):
            raise ValueError('projection must map every state onto the macro domain')
        if len(self.responses) != n or any(len(r) != len(self.judgments) for r in self.responses):
            raise ValueError('declare every state/judgment response')
        if any(type(op) is not FiniteOperation for op in self.operations):
            raise TypeError('operations must be explicit FiniteOperation tables')
        if len({op.name for op in self.operations}) != len(self.operations):
            raise ValueError('operation names must be unique')
        for op in self.operations:
            if len(op.outputs) != n ** op.arity or any(v is not None and v >= n for v in op.outputs):
                raise ValueError('declare every operation input and a valid output')

    @property
    def fingerprint(self):
        return fingerprint_value((DOMAIN, self.scope, self.states, self.macro_states,
            self.judgments, self.projection, self.responses,
            tuple((op.name, op.arity, op.outputs) for op in self.operations),
            self.complete, self.assumptions))


@dataclass(frozen=True)
class AbstractionWitness:
    kind: str  # judgment / operation
    name: str
    left: Tuple[int, ...]
    right: Tuple[int, ...]

    def __post_init__(self):
        object.__setattr__(self, 'left', _indices(self.left))
        object.__setattr__(self, 'right', _indices(self.right))


@dataclass(frozen=True)
class TaskAbstractionCertificate:
    problem_fingerprint: str
    macro_responses: Tuple[Tuple[str, ...], ...]
    macro_operations: Tuple[FiniteOperation, ...]

    def __post_init__(self):
        object.__setattr__(self, 'macro_responses', _responses(self.macro_responses))
        object.__setattr__(self, 'macro_operations', ordered_tuple(self.macro_operations))


@dataclass(frozen=True)
class TaskAbstractionResult:
    status: str  # verified / refuted / undecided
    reason: str
    problem_fingerprint: str
    checks: int
    certificate: Optional[TaskAbstractionCertificate] = None
    witness: Optional[AbstractionWitness] = None


def analyze_task_abstraction(problem, *, max_checks=None):
    """Construct factor tables or return the first deterministic paired refuter.

    One check is a concrete response cell or operation row. Construction, hashes,
    allocation and serialization are outside this logical work allowance.
    """
    _limit(max_checks)
    checks = 0
    fingerprint = problem.fingerprint
    def result(status, reason, certificate=None, witness=None):
        return TaskAbstractionResult(status, reason, fingerprint, checks, certificate, witness)

    representatives = {}
    macro_responses = [None] * len(problem.macro_states)
    for state, macro in enumerate(problem.projection):
        for column, name in enumerate(problem.judgments):
            if max_checks is not None and checks >= max_checks:
                return result('undecided', 'check_limit')
            checks += 1
            first = representatives.setdefault(macro, state)
            if problem.responses[state][column] != problem.responses[first][column]:
                return result('refuted', 'judgment_not_factored', witness=
                    AbstractionWitness('judgment', name, (first,), (state,)))
        macro_responses[macro] = problem.responses[state]

    macro_operations = []
    n, m = len(problem.states), len(problem.macro_states)
    for op in problem.operations:
        projected = {}
        for inputs, output in zip(product(range(n), repeat=op.arity), op.outputs):
            if max_checks is not None and checks >= max_checks:
                return result('undecided', 'check_limit')
            checks += 1
            key = tuple(problem.projection[i] for i in inputs)
            value = None if output is None else problem.projection[output]
            if key in projected and projected[key][1] != value:
                return result('refuted', 'operation_not_congruent', witness=
                    AbstractionWitness('operation', op.name, projected[key][0], inputs))
            projected[key] = (inputs, value)
        macro_operations.append(FiniteOperation(op.name, op.arity,
            tuple(projected[key][1] for key in product(range(m), repeat=op.arity))))
    if not problem.complete:
        return result('undecided', 'scope_coverage_not_declared')
    certificate = TaskAbstractionCertificate(fingerprint, tuple(macro_responses), tuple(macro_operations))
    return result('verified', 'finite_task_factorization', certificate=certificate)


def verify_task_abstraction(problem, certificate, *, max_checks=None):
    """Check proposed factors against original tables, without the analyzer.

    valid concerns this finite task abstraction, not a successful implementation,
    a coarsest quotient, or completeness of a search procedure.
    """
    _limit(max_checks)
    if type(certificate) is not TaskAbstractionCertificate or certificate.problem_fingerprint != problem.fingerprint:
        return 'invalid'
    n, m, q = len(problem.states), len(problem.macro_states), len(problem.judgments)
    if len(certificate.macro_responses) != m or any(len(row) != q for row in certificate.macro_responses):
        return 'invalid'
    if len(certificate.macro_operations) != len(problem.operations):
        return 'invalid'
    for op, factor in zip(problem.operations, certificate.macro_operations):
        if (type(factor) is not FiniteOperation or (factor.name, factor.arity) != (op.name, op.arity)
                or len(factor.outputs) != m ** op.arity
                or any(v is not None and v >= m for v in factor.outputs)):
            return 'invalid'
    checks = 0
    for state, row in enumerate(problem.responses):
        for column, value in enumerate(row):
            if max_checks is not None and checks >= max_checks:
                return 'undecided'
            checks += 1
            if value != certificate.macro_responses[problem.projection[state]][column]:
                return 'invalid'
    for op, factor in zip(problem.operations, certificate.macro_operations):
        for inputs, output in zip(product(range(n), repeat=op.arity), op.outputs):
            if max_checks is not None and checks >= max_checks:
                return 'undecided'
            checks += 1
            abstract_inputs = tuple(problem.projection[i] for i in inputs)
            expected = None if output is None else problem.projection[output]
            if factor.outputs[_offset(abstract_inputs, m)] != expected:
                return 'invalid'
    return 'valid' if problem.complete else 'undecided'


def verify_abstraction_witness(problem, witness):
    """Replay a local refuter; missing scope coverage does not erase a refuter."""
    if type(witness) is not AbstractionWitness:
        return 'invalid'
    n = len(problem.states)
    if (len(witness.left) != len(witness.right) or not witness.left
            or any(i >= n for i in witness.left + witness.right)):
        return 'invalid'
    if tuple(problem.projection[i] for i in witness.left) != tuple(problem.projection[i] for i in witness.right):
        return 'invalid'
    if witness.kind == 'judgment':
        if len(witness.left) != 1 or witness.name not in problem.judgments:
            return 'invalid'
        column = problem.judgments.index(witness.name)
        different = problem.responses[witness.left[0]][column] != problem.responses[witness.right[0]][column]
    elif witness.kind == 'operation':
        op = next((op for op in problem.operations if op.name == witness.name), None)
        if op is None or len(witness.left) != op.arity:
            return 'invalid'
        left, right = (op.outputs[_offset(inputs, n)] for inputs in (witness.left, witness.right))
        different = (None if left is None else problem.projection[left]) != (None if right is None else problem.projection[right])
    else:
        return 'invalid'
    return 'valid' if different else 'invalid'
