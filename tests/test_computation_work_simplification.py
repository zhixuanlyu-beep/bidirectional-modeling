"""Preparation stays outside candidate loops; independent review still runs."""
from itertools import product
import unittest
from unittest.mock import patch

from bidirectional_modeling import computation_search as cs
from bidirectional_modeling.extensions import local_theories as lt


class ComputationWorkSimplification(unittest.TestCase):
    def setUp(self):
        self.task = cs.FiniteBitTask('AND work fixture', 2, (0, 0, 0, 1))
        self.evidence = (cs.BitEvidence((0, 0), 0, 'first'), cs.BitEvidence((1, 1), 1, 'last'))
        self.program = cs.BitProgram(('input:0', 'input:1', 'and', 'halt'))

    def test_search_prepares_once_and_does_not_repeat_known_inputs(self):
        reads, executed = [], []
        get_cases, run = cs.FiniteBitTask.cases.fget, cs.run_bit_program
        def cases(task):
            reads.append(task)
            return get_cases(task)
        def execute(program, inputs, *, max_cost):
            if program == self.program:
                executed.append(inputs)
            return run(program, inputs, max_cost=max_cost)
        with patch.object(cs.FiniteBitTask, 'cases', property(cases)), \
             patch.object(cs, 'run_bit_program', side_effect=execute):
            result = cs.search_computations(self.task, self.evidence, max_cost=9, hints=(self.program,))
        self.assertEqual(result.certificate.program, self.program)
        self.assertEqual(len(reads), 1)
        self.assertEqual(executed, [(0, 0), (1, 1), (0, 1), (1, 0)])
        self.assertTrue(any(c.authority.startswith('evidence:') for c in result.counterexamples))
        self.assertTrue(any(c.authority == 'complete_specification' for c in result.counterexamples))

    def test_review_prepares_once_and_replays_the_complete_domain(self):
        certificate = cs.search_computations(self.task, self.evidence, max_cost=9).certificate
        reads, executed = [], []
        get_cases, run = cs.FiniteBitTask.cases.fget, cs.run_bit_program
        def cases(task):
            reads.append(task)
            return get_cases(task)
        def execute(program, inputs, *, max_cost):
            if program == certificate.program:
                executed.append(inputs)
            return run(program, inputs, max_cost=max_cost)
        with patch.object(cs.FiniteBitTask, 'cases', property(cases)), \
             patch.object(cs, 'run_bit_program', side_effect=execute), \
             patch.object(cs, '_check_program', side_effect=AssertionError('search acceptance')):
            review = cs.verify_computation_certificate(self.task, self.evidence, certificate)
        self.assertEqual((review.status, review.optimality), ('valid', 'verified'))
        self.assertEqual(len(reads), 1)
        self.assertEqual(executed, list(product((0, 1), repeat=2)))

    def test_evidence_uses_direct_table_lookup_without_materializing_the_domain(self):
        for width in range(4):
            inputs = tuple(product((0, 1), repeat=width))
            outputs = tuple(i % 2 for i in range(len(inputs)))
            task = cs.FiniteBitTask('direct lookup', width, outputs)
            evidence = tuple(cs.BitEvidence(x, y, 'fixture') for x, y in zip(inputs, outputs))
            with patch.object(cs.FiniteBitTask, 'cases', property(lambda _: self.fail('domain allocation'))):
                self.assertEqual(cs._evidence(task, evidence), evidence)
                with self.assertRaises(ValueError):
                    cs._evidence(task, (cs.BitEvidence(inputs[0], 1 - outputs[0], 'wrong'),))
                with self.assertRaises(ValueError):
                    cs._evidence(task, (cs.BitEvidence((0,) * (width + 1), 0, 'outside'),))

    def test_cover_validation_does_not_construct_a_query_library(self):
        local = lt.LocalBitTheory('AND', self.task.scope, self.task.width,
            tuple(cs.BitEvidence(x, y, 'fixture') for x, y in self.task.cases), self.program)
        with patch.object(lt.LocalTheoryLibrary, '__post_init__', side_effect=AssertionError('unused index')):
            report = lt.certify_local_cover(self.task, (local,), self.program, max_cost=9)
            self.assertEqual(report.status, 'valid')
            self.assertEqual(lt.verify_local_cover(self.task, (local,), report.certificate), 'valid')
            for invalid in ((local, local), (None,), {local}):
                with self.assertRaises((TypeError, ValueError)):
                    lt.certify_local_cover(self.task, invalid, self.program, max_cost=9)
