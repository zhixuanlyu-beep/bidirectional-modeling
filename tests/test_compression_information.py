"""Finite data relocation and task-relative information loss (C-COMPUTATION,
C-ABSTRACTION). No claim about a universal compressor or halting decider.
"""
from dataclasses import replace
from itertools import product
import unittest

from bidirectional_modeling import computation_search as cs
from bidirectional_modeling.task_abstraction import (
    FiniteTaskAbstraction, analyze_task_abstraction,
    verify_abstraction_witness, verify_task_abstraction,
)


class CompressionInformationContracts(unittest.TestCase):
    def test_inlining_preserves_small_program_outputs_and_charges_extra_work(self):
        # Every well-formed small program, including empty and multi-bit data,
        # discarded bits, input reads, Boolean operators and internal resets.
        for width in (0, 1):
            for program in cs.enumerate_bit_programs(width, 9):
                inlined = cs.inline_initial_bits(program, width=width)
                self.assertEqual(inlined.initial, ())
                for inputs in product((0, 1), repeat=width):
                    before = cs.run_bit_program(program, inputs, max_cost=9)
                    after = cs.run_bit_program(inlined, inputs,
                                              max_cost=9 + len(program.initial))
                    self.assertEqual((after.status, after.output),
                                     (before.status, before.output))
                    if before.status == 'completed':
                        self.assertEqual(after.load_units, before.load_units)
                        self.assertEqual(after.cost, before.cost + len(program.initial))
                    else:
                        self.assertIsNone(after.output)
                # Moving data twice cannot add further literal instructions.
                self.assertEqual(cs.inline_initial_bits(inlined, width=width), inlined)

    def test_order_parameter_information_and_budget_are_not_erased(self):
        # `drop` distinguishes top from bottom, catching reversed serialization.
        original = cs.BitProgram(('drop', 'halt'), (0, 1))
        inlined = cs.inline_initial_bits(original, width=0)
        self.assertEqual(cs.run_bit_program(original, (), max_cost=7).output, 0)
        for limit in range(11):
            run = cs.run_bit_program(inlined, (), max_cost=limit)
            self.assertEqual(run.status, 'completed' if limit >= 9 else 'resource_limit')
            self.assertLessEqual(run.cost, limit)
            self.assertEqual(run.output, 0 if limit >= 9 else None)
        for bit in (0, 1):
            source = cs.BitProgram(('input:0', 'xor', 'halt'), (bit,))
            target = cs.inline_initial_bits(source, width=1)
            self.assertEqual(tuple(cs.run_bit_program(target, (x,), max_cost=9).output
                                   for x in (0, 1)), (bit, 1 - bit))
        other = cs.inline_initial_bits(cs.BitProgram(('drop', 'halt'), (1, 0)), width=0)
        self.assertNotEqual(inlined.fingerprint, other.fingerprint)

    def test_relocation_does_not_transfer_cost_or_optimality_certificate(self):
        task = cs.FiniteBitTask('constant one / exact fixture', 0, (1,))
        original = cs.search_computations(task, max_cost=4).certificate
        inlined = cs.inline_initial_bits(original.program, width=0)
        stale_cost = replace(original, program=inlined)
        self.assertNotEqual(original.program.fingerprint, inlined.fingerprint)
        self.assertNotEqual(cs.verify_computation_certificate(task, (), stale_cost).status,
                            'valid')
        fresh = replace(stale_cost, cost=5)
        review = cs.verify_computation_certificate(task, (), fresh)
        self.assertEqual((review.status, review.optimality), ('valid', 'refuted'))

    def test_invalid_declarations_cannot_be_compiled(self):
        for width in (-1, True, 1.0):
            with self.assertRaises(ValueError):
                cs.inline_initial_bits(cs.BitProgram(('one', 'halt')), width=width)
        for program in (None, cs.BitProgram(('input:0', 'halt')),
                        cs.BitProgram(('not', 'halt')), cs.BitProgram(('one',)),
                        cs.BitProgram(('one', 'one', 'halt'))):
            with self.assertRaises((TypeError, ValueError)):
                cs.inline_initial_bits(program, width=0)

    def test_indistinguishable_histories_require_an_additional_record(self):
        hidden = FiniteTaskAbstraction('two histories / exact fixture',
            ('heads history', 'tails history'), ('question without record',),
            ('toss result',), (0, 0), (('heads',), ('tails',)), complete=True)
        result = analyze_task_abstraction(hidden)
        self.assertEqual(result.status, 'refuted')
        self.assertEqual(verify_abstraction_witness(hidden, result.witness), 'valid')
        # Even this two-answer problem has no output function of the available
        # input alone. Explicitly restore the missing distinction to factor it.
        recorded = replace(hidden, macro_states=('record:heads', 'record:tails'),
                           projection=(0, 1))
        solved = analyze_task_abstraction(recorded)
        self.assertEqual(solved.status, 'verified')
        self.assertEqual(verify_task_abstraction(recorded, solved.certificate), 'valid')
        self.assertEqual(verify_task_abstraction(hidden, solved.certificate), 'invalid')
        for limit in (0, 1):
            self.assertEqual(analyze_task_abstraction(hidden, max_checks=limit).status,
                             'undecided')
