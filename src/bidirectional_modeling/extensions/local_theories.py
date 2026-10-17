"""Scoped finite theories, checked gluing and cost-preserving extraction.

Theories are data-only local specifications with base-language witnesses. They
add no machine primitive or free runtime table. Exact scope indexing is an
explicit applicability contract, not automatic recognition of arbitrary maths.
"""
from dataclasses import dataclass
from types import MappingProxyType

from ..computation_search import (
    LANGUAGE, BitEvidence, BitProgram, FiniteBitTask, _natural,
    _validate_program, run_bit_program, search_computations,
)
from ..search import SearchBudgetExceeded, SearchWorkBudget, _name
from ..structural import fingerprint_value, ordered_tuple


@dataclass(frozen=True)
class LocalBitTheory:
    name: str
    scope: str
    width: int
    cases: tuple  # nonempty ordered BitEvidence; local specification and source
    witness: BitProgram

    def __post_init__(self):
        _name(self.name)
        _name(self.scope)
        _natural(self.width)
        cases = ordered_tuple(self.cases)
        object.__setattr__(self, 'cases', cases)
        if not cases or any(type(e) is not BitEvidence for e in cases):
            raise ValueError('local theory requires nonempty BitEvidence')
        if (any(len(e.inputs) != self.width for e in cases)
                or len({e.inputs for e in cases}) != len(cases)):
            raise ValueError('local inputs must be distinct and match the width')
        _validate_program(self.witness, self.width)

    @property
    def fingerprint(self):
        return fingerprint_value(('local-bit-theory-v1', LANGUAGE, self.name,
            self.scope, self.width, tuple((e.inputs, e.expected, e.source) for e in self.cases),
            self.witness.fingerprint))


@dataclass(frozen=True)
class LocalTheoryLibrary:
    theories: tuple = ()

    def __post_init__(self):
        theories = ordered_tuple(self.theories)
        object.__setattr__(self, 'theories', theories)
        if any(type(t) is not LocalBitTheory for t in theories):
            raise TypeError('expected LocalBitTheory declarations')
        if len({t.name for t in theories}) != len(theories):
            raise ValueError('duplicate theory name')
        index = {}
        for theory in theories:
            index.setdefault((theory.scope, theory.width), []).append(theory)
        object.__setattr__(self, '_index', MappingProxyType(
            {key: tuple(value) for key, value in index.items()}))

    def activate(self, task):
        if type(task) is not FiniteBitTask:
            raise TypeError('expected FiniteBitTask')
        return self._index.get((task.scope, task.width), ())

    def search(self, task, evidence=(), *, max_cost, max_trials=100000):
        """Hints remain untrusted; full finite target and base enumeration decide."""
        return search_computations(task, evidence, max_cost=max_cost,
            max_trials=max_trials, hints=tuple(t.witness for t in self.activate(task)))


def _theories(task, theories):
    if type(task) is not FiniteBitTask:
        raise TypeError('expected FiniteBitTask')
    # Reuse declaration validation, never activate foreign modules silently.
    theories = LocalTheoryLibrary(theories).theories
    if any((t.scope, t.width) != (task.scope, task.width) for t in theories):
        raise ValueError('theory outside task applicability scope')
    return theories


def _binding(theories):
    return fingerprint_value(('local-bit-cover-v1', tuple(t.fingerprint for t in theories)))


@dataclass(frozen=True)
class LocalCoverCertificate:
    task_fingerprint: str
    theories_fingerprint: str
    program: BitProgram
    cost: int
    local_costs: tuple  # (name, worst local runtime cost), not a global cost sum


@dataclass(frozen=True)
class LocalCoverReport:
    status: str  # valid / invalid / undecided; never an optimality claim
    reason: str
    certificate: object = None


def certify_local_cover(task, theories, program, *, max_cost, budget=None):
    """Check local semantics, overlaps, complete coverage and extracted program.

    A missing case invalidates this coverage claim, not the underlying theory.
    Limits and interpreter faults stay undecided. Program is supplied, never
    synthesized by hiding a dispatch table inside the trusted machine.
    """
    theories = _theories(task, theories)
    _natural(max_cost)
    _validate_program(program, task.width)
    budget = budget if budget is not None else SearchWorkBudget()
    glued, costs = {}, []
    try:
        for theory in theories:
            cost = 0
            for case in theory.cases:
                budget.consume('response_checks')
                run = run_bit_program(theory.witness, case.inputs, max_cost=max_cost)
                if run.status != 'completed':
                    return LocalCoverReport('undecided', run.status)
                if run.output != case.expected:
                    return LocalCoverReport('invalid', 'local_witness_mismatch')
                if case.inputs in glued and glued[case.inputs] != run.output:
                    return LocalCoverReport('invalid', 'overlap_conflict')
                glued[case.inputs] = run.output
                cost = max(cost, run.cost)
            costs.append((theory.name, cost))
        cost = 0
        for inputs, expected in task.cases:
            budget.consume('constraint_checks')
            if inputs not in glued:
                return LocalCoverReport('invalid', 'incomplete_cover')
            if glued[inputs] != expected:
                return LocalCoverReport('invalid', 'target_mismatch')
            run = run_bit_program(program, inputs, max_cost=max_cost)
            if run.status != 'completed':
                return LocalCoverReport('undecided', run.status)
            if run.output != glued[inputs]:
                return LocalCoverReport('invalid', 'extraction_mismatch')
            cost = max(cost, run.cost)
    except SearchBudgetExceeded as error:
        return LocalCoverReport('undecided', error.reason)
    certificate = LocalCoverCertificate(task.fingerprint, _binding(theories),
                                        program, cost, tuple(costs))
    return LocalCoverReport('valid', 'complete_finite_cover', certificate)


def verify_local_cover(task, theories, certificate, *, budget=None):
    """Replay original declarations; do not trust the generating report/helper.

    The machine, declaration checks, finite task domain and codec remain trusted.
    Review executes each witness at its certified bound, and validates exact
    costs independently of any caller-supplied discovery budget.
    """
    theories = _theories(task, theories)
    if (type(certificate) is not LocalCoverCertificate
            or certificate.task_fingerprint != task.fingerprint
            or certificate.theories_fingerprint != _binding(theories)
            or type(certificate.cost) is not int or certificate.cost < 0
            or type(certificate.local_costs) is not tuple
            or len(certificate.local_costs) != len(theories)):
        return 'invalid'
    budget = budget if budget is not None else SearchWorkBudget()
    try:
        _validate_program(certificate.program, task.width)
        for theory, row in zip(theories, certificate.local_costs):
            if (type(row) is not tuple or len(row) != 2 or row[0] != theory.name
                    or type(row[1]) is not int or row[1] < 0):
                return 'invalid'
        expected = dict(task.cases)
        covered = set()
        for theory, (_, bound) in zip(theories, certificate.local_costs):
            actual_cost = 0
            for case in theory.cases:
                budget.consume('response_checks')
                run = run_bit_program(theory.witness, case.inputs, max_cost=bound)
                if run.status != 'completed':
                    return 'undecided'
                if run.output != case.expected or run.output != expected[case.inputs]:
                    return 'invalid'
                covered.add(case.inputs)
                actual_cost = max(actual_cost, run.cost)
            if actual_cost != bound:
                return 'invalid'
        worst_cost = 0
        for inputs, output in task.cases:
            budget.consume('constraint_checks')
            if inputs not in covered:
                return 'invalid'
            run = run_bit_program(certificate.program, inputs, max_cost=certificate.cost)
            if run.status != 'completed':
                return 'undecided'
            if run.output != output:
                return 'invalid'
            worst_cost = max(worst_cost, run.cost)
        return 'valid' if worst_cost == certificate.cost else 'invalid'
    except (TypeError, ValueError):
        return 'invalid'
    except SearchBudgetExceeded:
        return 'undecided'


@dataclass(frozen=True)
class TranslationReview:
    status: str
    reason: str
    original_cost: object = None
    translated_cost: object = None


def verify_cost_preserving_translation(task, original, translated, *, max_cost, budget=None):
    """Prove equivalence and non-increasing worst cost on this complete domain.

    This is one program pair, not coverage of every candidate, target correctness
    or optimality. The identity base path in library.search covers all original
    candidates without translation overhead in the emitted program.
    """
    if type(task) is not FiniteBitTask:
        raise TypeError('expected FiniteBitTask')
    _natural(max_cost)
    _validate_program(original, task.width)
    _validate_program(translated, task.width)
    budget = budget if budget is not None else SearchWorkBudget()
    costs = [0, 0]
    try:
        for inputs, _ in task.cases:
            runs = []
            for i, program in enumerate((original, translated)):
                budget.consume('response_checks')
                run = run_bit_program(program, inputs, max_cost=max_cost)
                if run.status != 'completed':
                    return TranslationReview('undecided', run.status)
                costs[i] = max(costs[i], run.cost)
                runs.append(run.output)
            if runs[0] != runs[1]:
                return TranslationReview('invalid', 'behavior_changed')
    except SearchBudgetExceeded as error:
        return TranslationReview('undecided', error.reason)
    status = 'valid' if costs[1] <= costs[0] else 'invalid'
    return TranslationReview(status, 'cost_preserved' if status == 'valid' else 'cost_increased',
                             *costs)


__all__ = ['LocalBitTheory', 'LocalTheoryLibrary', 'LocalCoverCertificate',
           'LocalCoverReport', 'certify_local_cover', 'verify_local_cover',
           'TranslationReview', 'verify_cost_preserving_translation']
