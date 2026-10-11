"""Finite resource-layer synthesis in a data-only autonomous bit-stack machine.

This experimental module deliberately has no Python candidate callbacks. Costs
are abstract machine units, including candidate-specific loading and output.
The existing description-ranked catalogue search retains its own semantics.
"""
from dataclasses import dataclass
from itertools import product
from typing import Optional, Tuple

from .structural import fingerprint_value, ordered_tuple


LANGUAGE = 'autonomous-bit-stack-v1'


def _natural(value):
    if type(value) is not int or value < 0:
        raise ValueError('budget and width must be nonnegative integers')
    return value


def _bits(values):
    values = ordered_tuple(values)
    if any(type(bit) is not int or bit not in (0, 1) for bit in values):
        raise ValueError('bits must be explicit integers 0 or 1')
    return values


@dataclass(frozen=True)
class BitProgram:
    code: Tuple[str, ...]
    initial: Tuple[int, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'code', ordered_tuple(self.code))
        object.__setattr__(self, 'initial', _bits(self.initial))
        if not self.code or any(type(op) is not str for op in self.code):
            raise ValueError('a program needs a nonempty ordered instruction sequence')

    @property
    def fingerprint(self):
        return fingerprint_value((LANGUAGE, self.code, self.initial))


@dataclass(frozen=True)
class BitEvidence:
    inputs: Tuple[int, ...]
    expected: int
    source: str

    def __post_init__(self):
        object.__setattr__(self, 'inputs', _bits(self.inputs))
        _bits((self.expected,))
        if type(self.source) is not str or not self.source.strip():
            raise ValueError('evidence requires a source')


@dataclass(frozen=True)
class FiniteBitTask:
    """An explicit complete specification, separate from acquired evidence.

    Outputs follow lexicographic inputs in {0,1}**width, including () at width 0.
    Scope describes applicability/calibration; it is part of every binding.
    """
    scope: str
    width: int
    outputs: Tuple[int, ...]

    def __post_init__(self):
        _natural(self.width)
        object.__setattr__(self, 'outputs', _bits(self.outputs))
        if type(self.scope) is not str or not self.scope.strip():
            raise ValueError('task requires an applicability scope')
        if len(self.outputs) != 2 ** self.width:
            raise ValueError('specify every input in the finite class')

    @property
    def cases(self):
        return tuple(zip(product((0, 1), repeat=self.width), self.outputs))

    @property
    def fingerprint(self):
        return fingerprint_value((LANGUAGE, 'task-v1', self.scope, self.width, self.outputs))


@dataclass(frozen=True)
class ComputationRun:
    status: str  # completed / resource_limit / execution_error
    output: Optional[int]
    load_units: int
    execution_units: int
    output_units: int
    reason: str = ''

    @property
    def cost(self):
        return self.load_units + self.execution_units + self.output_units


def _alphabet(width):
    return ('zero', 'one') + tuple('input:%d' % i for i in range(width)) + (
        'not', 'and', 'or', 'xor', 'drop', 'dup')


def _validate_program(program, width):
    if type(program) is not BitProgram:
        raise TypeError('expected BitProgram')
    if program.code[-1] not in ('halt', 'again'):
        raise ValueError('program must end with halt or again')
    depth = len(program.initial)
    alphabet = _alphabet(width)
    for op in program.code[:-1]:
        if op not in alphabet:
            raise ValueError('instruction outside the fixed language')
        needed = 2 if op in ('and', 'or', 'xor') else (
            1 if op in ('not', 'drop', 'dup') else 0)
        if depth < needed:
            raise ValueError('stack underflow in program declaration')
        depth += -1 if op in ('and', 'or', 'xor', 'drop') else (
            0 if op == 'not' else 1)
    if depth != 1:
        raise ValueError('terminal instruction requires exactly one output bit')


def run_bit_program(program, inputs, *, max_cost):
    """Execute bounded instructions without external intervention.

    Loading costs one unit per code word and initial bit, then each instruction
    costs one unit. Emitting the final bit costs one more. Input is fixed shared
    task data. `again` resets the machine internally and can never emit output.
    Syntax errors are declaration errors; unexpected interpreter faults are
    execution_error, never experimental counterexamples.
    """
    inputs = _bits(inputs)
    _natural(max_cost)
    _validate_program(program, len(inputs))
    load = len(program.code) + len(program.initial)
    if load > max_cost:
        return ComputationRun('resource_limit', None, max_cost, 0, 0, 'loading')
    stack = list(program.initial)
    steps = 0
    pc = 0
    try:
        while load + steps < max_cost:
            op = program.code[pc]
            steps += 1
            if op == 'halt':
                if load + steps + 1 > max_cost:
                    return ComputationRun('resource_limit', None, load, steps, 0, 'output')
                return ComputationRun('completed', stack[0], load, steps, 1)
            if op == 'again':
                reset = len(stack) + len(program.initial)
                if load + steps + reset > max_cost:
                    return ComputationRun('resource_limit', None, load,
                                          max_cost - load, 0, 'reset')
                steps += reset
                stack = list(program.initial)
                pc = 0
                continue
            if op == 'zero' or op == 'one':
                stack.append(int(op == 'one'))
            elif op.startswith('input:'):
                stack.append(inputs[int(op.split(':')[1])])
            elif op == 'not':
                stack[-1] = 1 - stack[-1]
            elif op == 'dup':
                stack.append(stack[-1])
            elif op == 'drop':
                stack.pop()
            else:
                right, left = stack.pop(), stack.pop()
                stack.append({'and': left & right, 'or': left | right,
                              'xor': left ^ right}[op])
            pc += 1
    except Exception as error:
        return ComputationRun('execution_error', None, load, steps, 0, str(error))
    return ComputationRun('resource_limit', None, load, steps, 0, 'execution')


def enumerate_bit_programs(width, max_cost):
    """Exhaust a finite resource layer, including internally repeating programs.

    A halting member with L words and D initial bits costs 2L+D+1. This bounds
    both lengths; the finite alphabet then bounds every layer. No candidate
    initializer, constant table, macro or Python closure is free background.
    Repeating members are retried at higher layers, not semantically refuted.
    """
    _natural(width)
    _natural(max_cost)
    for length in range(1, (max_cost - 1) // 2 + 1):
        for data_length in range(max_cost - 2 * length):
            for initial in product((0, 1), repeat=data_length):
                for body in product(_alphabet(width), repeat=length - 1):
                    for terminal in ('halt', 'again'):
                        program = BitProgram(body + (terminal,), initial)
                        try:
                            _validate_program(program, width)
                        except ValueError:
                            continue
                        yield program


def _evidence(task, evidence):
    if type(task) is not FiniteBitTask:
        raise TypeError('expected a complete FiniteBitTask')
    evidence = ordered_tuple(evidence)
    expected = dict(task.cases)
    seen = set()
    for item in evidence:
        if type(item) is not BitEvidence:
            raise TypeError('expected BitEvidence')
        if item.inputs in seen:
            raise ValueError('duplicate evidence input')
        seen.add(item.inputs)
        if expected.get(item.inputs) != item.expected:
            raise ValueError('evidence contradicts the task or is outside its domain')
    return evidence


def _evidence_fingerprint(evidence):
    return fingerprint_value(('computation-evidence-v1',
                              tuple((e.inputs, e.expected, e.source) for e in evidence)))


@dataclass(frozen=True)
class ComputationCounterexample:
    program: BitProgram
    inputs: Tuple[int, ...]
    actual: int
    expected: int
    authority: str  # evidence:<source> / complete_specification


@dataclass(frozen=True)
class ComputationCertificate:
    language: str
    task_fingerprint: str
    evidence_fingerprint: str
    program: BitProgram
    cost: int


@dataclass(frozen=True)
class ComputationSearchResult:
    status: str  # found / absent_within_bound / unknown
    certificate: Optional[ComputationCertificate]
    exhausted_through: int
    trials: int
    resource_limits: int
    counterexamples: Tuple[ComputationCounterexample, ...]
    reason: str


def _check_program(task, evidence, program, bound):
    cases = tuple((e.inputs, e.expected, 'evidence:' + e.source) for e in evidence)
    cases += tuple((inputs, output, 'complete_specification') for inputs, output in task.cases)
    worst_cost = 0
    for inputs, expected, authority in cases:
        run = run_bit_program(program, inputs, max_cost=bound)
        if run.status != 'completed':
            return run.status, None, None
        worst_cost = max(worst_cost, run.cost)
        if run.output != expected:
            return 'refuted', ComputationCounterexample(program, inputs, run.output,
                                                        expected, authority), None
    return 'passed', None, worst_cost


def search_computations(task, evidence=(), *, max_cost, max_trials=100000):
    """Restart from the language at each cost layer; inherit only active evidence.

    max_cost bounds individual autonomous runs. max_trials separately limits
    candidate experiments in this search, not Python time/memory or generation.
    No structure from a failed candidate is privileged or mutated into a patch.
    """
    evidence = _evidence(task, evidence)
    _natural(max_cost)
    _natural(max_trials)
    trials = limits = 0
    counterexamples = []
    for bound in range(max_cost + 1):
        for program in enumerate_bit_programs(task.width, bound):
            if trials >= max_trials:
                return ComputationSearchResult('unknown', None, bound - 1, trials, limits,
                    tuple(counterexamples), 'search_trials_exhausted')
            trials += 1
            status, counterexample, cost = _check_program(task, evidence, program, bound)
            if status not in ('passed', 'refuted', 'resource_limit'):
                return ComputationSearchResult('unknown', None, bound - 1, trials, limits,
                    tuple(counterexamples), 'interpreter_error')
            if status == 'resource_limit':
                limits += 1
            elif status == 'refuted':
                counterexamples.append(counterexample)
            else:
                certificate = ComputationCertificate(LANGUAGE, task.fingerprint,
                    _evidence_fingerprint(evidence), program, cost)
                return ComputationSearchResult('found', certificate, bound - 1,
                    trials, limits, tuple(counterexamples), 'complete_finite_target')
    return ComputationSearchResult('absent_within_bound', None, max_cost, trials, limits,
                                  tuple(counterexamples), 'finite_layers_exhausted')


@dataclass(frozen=True)
class ComputationReview:
    status: str  # valid / invalid / undecided
    optimality: str  # verified / refuted / undecided
    trials: int
    reason: str


def verify_computation_certificate(task, evidence, certificate, *, max_trials=100000):
    """Replay the witness and independently exhaust the cheaper finite layer.

    Does not call search_computations or its acceptance helper. Enumeration,
    machine execution, structural encoding and task declarations remain trusted.
    A verified witness survives a review budget limit; optimality stays undecided.
    """
    evidence = _evidence(task, evidence)
    _natural(max_trials)
    if (type(certificate) is not ComputationCertificate
            or certificate.language != LANGUAGE
            or certificate.task_fingerprint != task.fingerprint
            or certificate.evidence_fingerprint != _evidence_fingerprint(evidence)
            or type(certificate.cost) is not int or certificate.cost < 0):
        return ComputationReview('invalid', 'undecided', 0, 'binding_or_shape')
    if max_trials == 0:
        return ComputationReview('undecided', 'undecided', 0, 'review_trials_exhausted')
    trials = 1
    cost = 0
    try:
        _validate_program(certificate.program, task.width)
        for inputs, expected in task.cases:
            run = run_bit_program(certificate.program, inputs, max_cost=certificate.cost)
            if run.status != 'completed':
                return ComputationReview('undecided', 'undecided', trials, run.status)
            if run.output != expected:
                return ComputationReview('invalid', 'undecided', trials, 'wrong_output')
            cost = max(cost, run.cost)
    except (TypeError, ValueError):
        return ComputationReview('invalid', 'undecided', trials, 'invalid_program')
    if cost != certificate.cost:
        return ComputationReview('invalid', 'undecided', trials, 'wrong_cost')
    for program in enumerate_bit_programs(task.width, cost - 1):
        if trials >= max_trials:
            return ComputationReview('valid', 'undecided', trials, 'review_trials_exhausted')
        trials += 1
        fits = True
        for inputs, expected in task.cases:
            run = run_bit_program(program, inputs, max_cost=cost - 1)
            if run.status == 'execution_error':
                return ComputationReview('valid', 'undecided', trials, 'interpreter_error')
            if run.status != 'completed' or run.output != expected:
                fits = False
                break
        if fits:
            return ComputationReview('valid', 'refuted', trials, 'cheaper_witness')
    return ComputationReview('valid', 'verified', trials, 'cheaper_layer_exhausted')


def build_computation_demo_report():
    """Known samples underdetermine AND; full four-input acceptance resolves it."""
    from dataclasses import asdict
    task = FiniteBitTask('two-bit conjunction / exact finite fixture', 2, (0, 0, 0, 1))
    evidence = (BitEvidence((0, 0), 0, 'fixture:00'),
                BitEvidence((1, 1), 1, 'fixture:11'))
    result = search_computations(task, evidence, max_cost=9)
    review = verify_computation_certificate(task, evidence, result.certificate)
    return {'task': asdict(task), 'evidence': [asdict(e) for e in evidence],
            'result': asdict(result), 'review': asdict(review)}
