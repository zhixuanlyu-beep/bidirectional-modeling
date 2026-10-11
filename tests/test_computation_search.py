"""Resource accounting, full-class acceptance and independent optimality review."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import bidirectional_modeling.computation_search as cs


class ComputationContracts(unittest.TestCase):
    def setUp(self):
        self.task = cs.FiniteBitTask('exact two-bit fixture', 2, (0, 0, 0, 1))
        self.evidence = (cs.BitEvidence((0, 0), 0, 'fixture:00'),
                         cs.BitEvidence((1, 1), 1, 'fixture:11'))

    def test_full_target_not_just_known_samples(self):
        result = cs.search_computations(self.task, self.evidence, max_cost=9)
        self.assertEqual(result.status, 'found')
        self.assertEqual(result.certificate.cost, 9)
        self.assertEqual(result.exhausted_through, 8)
        self.assertGreater(result.resource_limits, 0)
        self.assertTrue(any(c.authority == 'complete_specification'
                            for c in result.counterexamples))
        self.assertTrue(any(c.authority.startswith('evidence:')
                            for c in result.counterexamples))
        # Both projections fit the known samples, but neither solves AND.
        for op in ('input:0', 'input:1'):
            partial = cs.BitProgram((op, 'halt'))
            for e in self.evidence:
                self.assertEqual(cs.run_bit_program(partial, e.inputs, max_cost=9).output,
                                 e.expected)
            self.assertTrue(any(cs.run_bit_program(partial, x, max_cost=9).output != y
                                for x, y in self.task.cases))
        review = cs.verify_computation_certificate(self.task, self.evidence,
                                                  result.certificate)
        self.assertEqual((review.status, review.optimality), ('valid', 'verified'))

    def test_loading_output_and_loop_costs_at_every_cutoff(self):
        program = cs.BitProgram(('halt',), (1,))
        for bound in range(7):
            run = cs.run_bit_program(program, (), max_cost=bound)
            self.assertLessEqual(run.cost, bound)
            self.assertEqual(run.status, 'completed' if bound >= 4 else 'resource_limit')
            if bound >= 4:
                self.assertEqual((run.load_units, run.execution_units, run.output_units),
                                 (2, 1, 1))
        # A precomputed 20-bit initial state is not free, even if discarded.
        hidden = cs.BitProgram(('drop',) * 19 + ('halt',), (0,) * 20)
        self.assertEqual(cs.run_bit_program(hidden, (), max_cost=60).status,
                         'resource_limit')
        self.assertEqual(cs.run_bit_program(hidden, (), max_cost=61).cost, 61)
        repeat = cs.BitProgram(('again',), (0,))
        for bound in range(2, 20):
            run = cs.run_bit_program(repeat, (), max_cost=bound)
            self.assertEqual(run.status, 'resource_limit')
            self.assertEqual(run.cost, bound)
            self.assertIsNone(run.output)
        small = tuple(cs.enumerate_bit_programs(0, 4))
        large = tuple(cs.enumerate_bit_programs(0, 5))
        self.assertIn(repeat, small)
        self.assertIn(repeat, large)

    def test_all_small_truth_tables_against_independent_reference(self):
        # Independent ordinary Boolean formula costs in this fixed machine:
        # initial constants + halt: 4; read + halt: 5; read + not + halt: 7.
        minima = {(0, 0): 4, (1, 1): 4, (0, 1): 5, (1, 0): 7}
        for outputs, optimum in minima.items():
            task = cs.FiniteBitTask('one-bit exhaustive reference', 1, outputs)
            for bound in range(8):
                result = cs.search_computations(task, max_cost=bound)
                self.assertEqual(result.status,
                    'found' if bound >= optimum else 'absent_within_bound')
                if result.certificate:
                    self.assertEqual(result.certificate.cost, optimum)
                    review = cs.verify_computation_certificate(task, (), result.certificate)
                    self.assertEqual(review.optimality, 'verified')
        # Reference execution for every well-formed program in a small layer.
        for program in cs.enumerate_bit_programs(1, 7):
            if program.code[-1] == 'again':
                continue
            for bit in (0, 1):
                stack = list(program.initial)
                for op in program.code[:-1]:
                    if op == 'zero':
                        stack.append(0)
                    elif op == 'one':
                        stack.append(1)
                    elif op == 'input:0':
                        stack.append(bit)
                    elif op == 'not':
                        stack[-1] = 1 - stack[-1]
                    elif op == 'drop':
                        stack.pop()
                    elif op == 'dup':
                        stack.append(stack[-1])
                    else:
                        right = stack.pop()
                        left = stack.pop()
                        stack.append(int((left == 1 and right == 1) if op == 'and'
                            else (left == 1 or right == 1) if op == 'or' else left != right))
                run = cs.run_bit_program(program, (bit,), max_cost=7)
                self.assertEqual(run.output, stack[0])
                self.assertEqual(run.cost, 2 * len(program.code) + len(program.initial) + 1)

    def test_trial_limits_cannot_establish_absence_or_minimality(self):
        complete = cs.search_computations(self.task, self.evidence, max_cost=9)
        for limit in range(complete.trials):
            result = cs.search_computations(self.task, self.evidence, max_cost=9,
                                            max_trials=limit)
            self.assertEqual(result.status, 'unknown')
            self.assertIsNone(result.certificate)
            self.assertEqual(result.trials, limit)
            self.assertLess(result.exhausted_through, 9)
        self.assertEqual(cs.search_computations(self.task, self.evidence, max_cost=9,
                          max_trials=complete.trials).status, 'found')
        review = cs.verify_computation_certificate(self.task, self.evidence,
            complete.certificate, max_trials=0)
        self.assertEqual(review.status, 'undecided')
        review = cs.verify_computation_certificate(self.task, self.evidence,
            complete.certificate, max_trials=1)
        self.assertEqual((review.status, review.optimality), ('valid', 'undecided'))
        full = cs.verify_computation_certificate(self.task, self.evidence, complete.certificate)
        for limit in range(1, full.trials):
            review = cs.verify_computation_certificate(self.task, self.evidence,
                complete.certificate, max_trials=limit)
            self.assertEqual((review.status, review.optimality), ('valid', 'undecided'))

    def test_new_evidence_withdrawal_and_scope_rebind(self):
        result = cs.search_computations(self.task, self.evidence, max_cost=9)
        certificate = result.certificate
        changed = (replace(self.evidence[0], source='new calibration'), self.evidence[1])
        for task, evidence in ((replace(self.task, scope='new scope'), self.evidence),
                               (self.task, changed), (self.task, self.evidence[:1]),
                               (self.task, ())):
            self.assertEqual(cs.verify_computation_certificate(task, evidence, certificate).status,
                             'invalid')
            fresh = cs.search_computations(task, evidence, max_cost=9)
            self.assertEqual(cs.verify_computation_certificate(task, evidence,
                                                               fresh.certificate).status, 'valid')

    def test_review_replays_without_generator_acceptance(self):
        result = cs.search_computations(self.task, self.evidence, max_cost=9)
        with patch.object(cs, 'search_computations', side_effect=AssertionError('generator')), \
             patch.object(cs, '_check_program', side_effect=AssertionError('acceptance')):
            review = cs.verify_computation_certificate(self.task, self.evidence, result.certificate)
            self.assertEqual(review.optimality, 'verified')
            wrong = replace(result.certificate, program=cs.BitProgram(('input:0', 'halt')), cost=5)
            self.assertEqual(cs.verify_computation_certificate(self.task, self.evidence, wrong).status,
                             'invalid')
        constant = cs.FiniteBitTask('constant', 0, (0,))
        cert = cs.ComputationCertificate(cs.LANGUAGE, constant.fingerprint,
            cs._evidence_fingerprint(()), cs.BitProgram(('zero', 'halt')), 5)
        review = cs.verify_computation_certificate(constant, (), cert)
        self.assertEqual((review.status, review.optimality), ('valid', 'refuted'))

    def test_certificate_tampering_and_errors_stay_unresolved(self):
        cert = cs.search_computations(self.task, self.evidence, max_cost=9).certificate
        for wrong in (None, replace(cert, language='unknown'),
                      replace(cert, task_fingerprint='bad'),
                      replace(cert, evidence_fingerprint='bad'),
                      replace(cert, cost=True), replace(cert, cost=-1),
                      replace(cert, cost=10), replace(cert, program='not data'),
                      replace(cert, program=cs.BitProgram(('not', 'halt')))):
            self.assertEqual(cs.verify_computation_certificate(self.task, self.evidence, wrong).status,
                             'invalid')
        self.assertEqual(cs.verify_computation_certificate(self.task, self.evidence,
            replace(cert, cost=8)).status, 'undecided')
        fault = cs.ComputationRun('execution_error', None, 1, 1, 0, 'fault injection')
        with patch.object(cs, 'run_bit_program', return_value=fault):
            result = cs.search_computations(self.task, max_cost=9)
            self.assertEqual((result.status, result.counterexamples), ('unknown', ()))
            self.assertEqual(cs.verify_computation_certificate(self.task, self.evidence, cert).status,
                             'undecided')
        real_run = cs.run_bit_program
        def fail_alternatives(program, inputs, *, max_cost):
            return real_run(program, inputs, max_cost=max_cost) if program == cert.program else fault
        with patch.object(cs, 'run_bit_program', side_effect=fail_alternatives):
            review = cs.verify_computation_certificate(self.task, self.evidence, cert)
            self.assertEqual((review.status, review.optimality), ('valid', 'undecided'))
        with patch.object(cs, '_check_program', return_value=('unrecognized', None, None)):
            self.assertEqual(cs.search_computations(self.task, max_cost=9).status, 'unknown')

    def test_invalid_declarations_and_finite_domains(self):
        for value in (-1, True, 1.2, '3'):
            with self.assertRaises(ValueError):
                cs.search_computations(self.task, max_cost=value)
            with self.assertRaises(ValueError):
                cs.search_computations(self.task, max_cost=9, max_trials=value)
            with self.assertRaises(ValueError):
                cs.FiniteBitTask('scope', value, (0,))
        for values in ((2,), (True,), ('1',)):
            with self.assertRaises(ValueError):
                cs.BitProgram(('halt',), values)
        for scope in ('', ' ', 1):
            with self.assertRaises(ValueError):
                cs.FiniteBitTask(scope, 0, (0,))
            with self.assertRaises(ValueError):
                cs.BitEvidence((), 0, scope)
        with self.assertRaises(ValueError):
            cs.FiniteBitTask('incomplete', 2, (0,))
        for evidence, error in (((self.evidence[0], self.evidence[0]), ValueError),
                                ((cs.BitEvidence((0, 0), 1, 'contradiction'),), ValueError),
                                ((cs.BitEvidence((), 0, 'outside'),), ValueError),
                                ((object(),), TypeError), ({self.evidence[0]}, TypeError)):
            with self.assertRaises(error):
                cs.search_computations(self.task, evidence, max_cost=9)
        with self.assertRaises(TypeError):
            cs.search_computations(object(), max_cost=9)
        for code in ((), (1,), ('unknown', 'halt'), ('halt', 'halt'),
                     ('input:2', 'halt'), ('not', 'halt'), ('one', 'one', 'halt'),
                     ('one',)):
            with self.assertRaises(ValueError):
                cs.run_bit_program(cs.BitProgram(code), (0, 0), max_cost=9)
        with self.assertRaises(TypeError):
            cs.run_bit_program(object(), (), max_cost=9)
        with self.assertRaises(TypeError):
            cs.BitProgram({'halt'})

    def test_unexpected_vm_fault_is_not_a_counterexample(self):
        # Fault injected after syntax checking; ordinary declarations cannot underflow.
        with patch.object(cs, '_validate_program'):
            run = cs.run_bit_program(cs.BitProgram(('drop', 'halt')), (), max_cost=9)
        self.assertEqual(run.status, 'execution_error')
        self.assertIsNone(run.output)

    def test_demo_and_deterministic_layer_identity(self):
        demo = cs.build_computation_demo_report()
        self.assertEqual(demo['review']['optimality'], 'verified')
        first = tuple(p.fingerprint for p in cs.enumerate_bit_programs(1, 7))
        self.assertEqual(first, tuple(p.fingerprint for p in cs.enumerate_bit_programs(1, 7)))
        self.assertEqual(len(first), len(set(first)))
        for p in cs.enumerate_bit_programs(1, 7):
            self.assertLessEqual(2 * len(p.code) + len(p.initial) + 1, 7)


if __name__ == '__main__':
    unittest.main()
