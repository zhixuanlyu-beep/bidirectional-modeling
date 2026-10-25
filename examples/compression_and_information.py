"""Rules replace answer tables; compiling data does not remove information."""
from dataclasses import asdict, replace
import json

from bidirectional_modeling.computation_search import (
    BitProgram, FiniteBitTask, inline_initial_bits, run_bit_program,
)
from bidirectional_modeling.task_abstraction import (
    FiniteTaskAbstraction, analyze_task_abstraction,
    verify_abstraction_witness, verify_task_abstraction,
)


def build_report():
    # A rule covers the complete finite fixture without a per-question table
    # inside the candidate. The target table belongs to acceptance, not execution.
    task = FiniteBitTask('two-input XOR / exact fixture', 2, (0, 1, 1, 0))
    rule = BitProgram(('input:0', 'input:1', 'xor', 'halt'))
    runs = tuple(run_bit_program(rule, x, max_cost=9) for x, _ in task.cases)
    assert all(r.status == 'completed' and r.output == y
               for r, (_, y) in zip(runs, task.cases))

    # A fixed bit selects identity versus negation. Inlining retains that bit
    # as a literal in code and raises the halting cost from 8 to 9.
    parameter_runs = []
    for bit in (0, 1):
        original = BitProgram(('input:0', 'xor', 'halt'), (bit,))
        inlined = inline_initial_bits(original, width=1)
        before = tuple(run_bit_program(original, (x,), max_cost=8) for x in (0, 1))
        after = tuple(run_bit_program(inlined, (x,), max_cost=9) for x in (0, 1))
        assert tuple(r.output for r in before) == tuple(r.output for r in after)
        parameter_runs.append({'parameter': bit, 'original': asdict(original),
            'inlined': asdict(inlined), 'before': [asdict(r) for r in before],
            'after': [asdict(r) for r in after]})

    # Two histories give identical available input but require distinct answers.
    # This is an explicit finite fixture, not a claim of empirical randomness.
    hidden = FiniteTaskAbstraction(
        scope='two possible histories of toss 37 / exact fixture',
        states=('history:heads', 'history:tails'),
        macro_states=('same question and no record',),
        judgments=('result of toss 37',), projection=(0, 0),
        responses=(('heads',), ('tails',)), complete=True,
    )
    missing = analyze_task_abstraction(hidden)
    assert missing.status == 'refuted'
    assert verify_abstraction_witness(hidden, missing.witness) == 'valid'
    recorded = replace(hidden, macro_states=('record:heads', 'record:tails'),
                       projection=(0, 1))
    sufficient = analyze_task_abstraction(recorded)
    assert sufficient.status == 'verified'
    assert verify_task_abstraction(recorded, sufficient.certificate) == 'valid'
    return {'rule': {'task': asdict(task), 'program': asdict(rule),
                     'runs': [asdict(r) for r in runs]},
            'finite_parameters': parameter_runs,
            'missing_fact': asdict(missing), 'record_supplied': asdict(sufficient)}


if __name__ == '__main__':
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
