from dataclasses import replace
from itertools import product
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from bidirectional_modeling.task_abstraction import (
    AbstractionWitness, FiniteOperation, FiniteTaskAbstraction,
    TaskAbstractionCertificate, analyze_task_abstraction,
    verify_abstraction_witness, verify_task_abstraction,
)
from bidirectional_modeling.task_abstraction_examples import (
    build_abstraction_demo_report, fairness_abstractions, resource_abstractions,
)


class TaskAbstractionContracts(unittest.TestCase):
    def test_semantic_refinement_does_not_reuse_old_specification(self):
        hours, clarified, refined = fairness_abstractions()
        old = analyze_task_abstraction(hours)
        self.assertEqual(old.status, 'verified')
        self.assertEqual(verify_task_abstraction(clarified, old.certificate), 'invalid')
        failure = analyze_task_abstraction(clarified)
        self.assertEqual(failure.status, 'refuted')
        self.assertEqual(failure.witness.name, 'night_burden_acceptable')
        self.assertEqual(verify_abstraction_witness(clarified, failure.witness), 'valid')
        new = analyze_task_abstraction(refined)
        self.assertEqual(new.status, 'verified')
        self.assertEqual(verify_task_abstraction(refined, new.certificate), 'valid')

    def test_static_factorization_does_not_imply_composition(self):
        coarse, refined = resource_abstractions()
        self.assertEqual(analyze_task_abstraction(replace(coarse, operations=())).status, 'verified')
        failure = analyze_task_abstraction(coarse)
        self.assertEqual(failure.reason, 'operation_not_congruent')
        self.assertEqual(failure.witness.left, (0, 0))
        self.assertEqual(failure.witness.right, (0, 1))
        self.assertEqual(verify_abstraction_witness(coarse, failure.witness), 'valid')
        result = analyze_task_abstraction(refined)
        self.assertEqual(result.status, 'verified')
        self.assertEqual(verify_task_abstraction(refined, result.certificate), 'valid')

    def test_all_small_tables_against_pairwise_reference(self):
        # Independent definition compares pairs, without producer's representative maps.
        for arity in (1, 2):
            inputs = tuple(product(range(2), repeat=arity))
            for alpha, values, outputs in product(((0, 0), (0, 1)),
                    product(('yes', 'no'), repeat=2), product((None, 0, 1), repeat=2 ** arity)):
                problem = FiniteTaskAbstraction('reference', ('a', 'b'),
                    ('same',) if alpha == (0, 0) else ('a', 'b'), ('q',), alpha,
                    tuple((value,) for value in values),
                    (FiniteOperation('op', arity, outputs),), True)
                static = all(alpha[i] != alpha[j] or values[i] == values[j]
                    for i in range(2) for j in range(2))
                dynamic = all(
                    tuple(alpha[i] for i in left) != tuple(alpha[i] for i in right)
                    or (None if outputs[l] is None else alpha[outputs[l]]) ==
                       (None if outputs[r] is None else alpha[outputs[r]])
                    for l, left in enumerate(inputs) for r, right in enumerate(inputs))
                result = analyze_task_abstraction(problem)
                self.assertEqual(result.status == 'verified', static and dynamic)
                if result.certificate:
                    self.assertEqual(verify_task_abstraction(problem, result.certificate), 'valid')
                else:
                    self.assertEqual(verify_abstraction_witness(problem, result.witness), 'valid')

    def test_definedness_must_be_preserved_but_micro_outputs_can_differ(self):
        hours, _, _ = fairness_abstractions()
        for outputs, expected in (((None, 0), 'refuted'), ((0, 1), 'verified'), ((None, None), 'verified')):
            problem = replace(hours, operations=(FiniteOperation('step', 1, outputs),))
            result = analyze_task_abstraction(problem)
            self.assertEqual(result.status, expected)
            if result.witness:
                self.assertEqual(verify_abstraction_witness(problem, result.witness), 'valid')
            else:
                self.assertEqual(verify_task_abstraction(problem, result.certificate), 'valid')

    def test_every_budget_cutoff_stays_conservative(self):
        _, problem = resource_abstractions()
        receipt = analyze_task_abstraction(problem)
        total = len(problem.states) * len(problem.judgments) + 4 ** 2
        self.assertEqual(receipt.checks, total)
        for allowance in range(total + 2):
            result = analyze_task_abstraction(problem, max_checks=allowance)
            self.assertEqual(result.status, 'verified' if allowance >= total else 'undecided')
            self.assertLessEqual(result.checks, allowance)
            self.assertEqual(verify_task_abstraction(problem, receipt.certificate, max_checks=allowance),
                'valid' if allowance >= total else 'undecided')
        coarse, _ = resource_abstractions()
        failure = analyze_task_abstraction(coarse)
        for allowance in range(failure.checks):
            self.assertEqual(analyze_task_abstraction(coarse, max_checks=allowance).status, 'undecided')
        self.assertEqual(analyze_task_abstraction(coarse, max_checks=failure.checks).status, 'refuted')

    def test_incomplete_scope_does_not_prove_but_retains_real_refuters(self):
        hours, clarified, _ = fairness_abstractions()
        incomplete = replace(hours, complete=False)
        self.assertEqual(analyze_task_abstraction(incomplete).status, 'undecided')
        certificate = replace(analyze_task_abstraction(hours).certificate,
            problem_fingerprint=incomplete.fingerprint)
        self.assertEqual(verify_task_abstraction(incomplete, certificate), 'undecided')
        failure = analyze_task_abstraction(replace(clarified, complete=False))
        self.assertEqual(failure.status, 'refuted')
        self.assertEqual(verify_abstraction_witness(clarified, failure.witness), 'valid')

    def test_certificate_review_ignores_generator_and_rejects_forgery(self):
        _, problem = resource_abstractions()
        certificate = analyze_task_abstraction(problem).certificate
        factor = certificate.macro_operations[0]
        with patch('bidirectional_modeling.task_abstraction.analyze_task_abstraction',
                side_effect=AssertionError('generator must not run')):
            self.assertEqual(verify_task_abstraction(problem, certificate), 'valid')
            for forged in (None, replace(certificate, problem_fingerprint='stale'),
                    replace(certificate, macro_responses=()),
                    replace(certificate, macro_responses=(('wrong',),) * 4),
                    replace(certificate, macro_responses=((),) * 4),
                    replace(certificate, macro_operations=()),
                    replace(certificate, macro_operations=('bad table',)),
                    replace(certificate, macro_operations=(replace(factor, name='other'),)),
                    replace(certificate, macro_operations=(replace(factor, arity=1),)),
                    replace(certificate, macro_operations=(replace(factor, outputs=(0,)),)),
                    replace(certificate, macro_operations=(replace(factor, outputs=(4,) * 16),)),
                    replace(certificate, macro_operations=(replace(factor, outputs=(None,) * 16),))):
                self.assertEqual(verify_task_abstraction(problem, forged), 'invalid')

    def test_scope_assumptions_names_and_order_bind_the_certificate(self):
        hours, _, _ = fairness_abstractions()
        certificate = analyze_task_abstraction(hours).certificate
        for changed in (replace(hours, scope='another environment'),
                replace(hours, assumptions=('new assumption',)),
                replace(hours, states=tuple(reversed(hours.states))),
                replace(hours, judgments=('new judgment',))):
            self.assertEqual(verify_task_abstraction(changed, certificate), 'invalid')

    def test_witness_review_replays_membership_and_semantics(self):
        _, problem, _ = fairness_abstractions()
        real = analyze_task_abstraction(problem).witness
        self.assertIsInstance(real, AbstractionWitness)
        for forged in (None, replace(real, kind='other'), replace(real, name='missing'),
                replace(real, left=()), replace(real, left=(2,)),
                replace(real, left=(0, 1)), replace(real, right=(0,)),
                replace(real, left=(0, 0), right=(1, 1))):
            self.assertEqual(verify_abstraction_witness(problem, forged), 'invalid')
        _, refined = resource_abstractions()
        self.assertEqual(verify_abstraction_witness(refined, AbstractionWitness('operation', 'combine', (0, 0), (0, 1))), 'invalid')
        coarse, _ = resource_abstractions()
        for witness in (AbstractionWitness('operation', 'missing', (0, 0), (0, 1)),
                AbstractionWitness('operation', 'combine', (0,), (1,)),
                AbstractionWitness('operation', 'combine', (0, 0), (0, 0))):
            self.assertEqual(verify_abstraction_witness(coarse, witness), 'invalid')

    def test_invalid_and_empty_declarations_do_not_receive_proofs(self):
        hours, _, _ = fairness_abstractions()
        invalid = ({'scope': ''}, {'states': ()}, {'macro_states': ('same', 'same')},
            {'macro_states': ('one', 'two')}, {'judgments': ()}, {'projection': (True, 0)},
            {'projection': (0,)}, {'projection': (0, 2)}, {'responses': (('yes',),)},
            {'responses': (('yes', 'no'), ('yes',))}, {'responses': ((True,), ('yes',))},
            {'complete': 1}, {'assumptions': (None,)}, {'operations': ('callable',)},
            {'operations': (FiniteOperation('step', 1, (2, 0)),)},
            {'operations': (FiniteOperation('step', 2, (0, 0)),)},
            {'operations': (FiniteOperation('step', 1, (0, 0)),) * 2})
        for change in invalid:
            with self.assertRaises((ValueError, TypeError)):
                replace(hours, **change)
        for values in ({'a', 'b'}, ('a', None)):
            with self.assertRaises((ValueError, TypeError)):
                replace(hours, states=values)
        for arity, outputs in ((0, ()), (True, (0,)), (1, (True,)), (1, (-1,))):
            with self.assertRaises(ValueError):
                FiniteOperation('step', arity, outputs)
        for function in (analyze_task_abstraction, verify_task_abstraction):
            for budget in (True, -1, 0.5):
                with self.assertRaises(ValueError):
                    if function is analyze_task_abstraction:
                        function(hours, max_checks=budget)
                    else:
                        function(hours, None, max_checks=budget)

    def test_mutable_inputs_are_snapshotted(self):
        responses, projection, outputs = [['yes'], ['yes']], [0, 0], [0, 1]
        problem = FiniteTaskAbstraction('fixture', ['a', 'b'], ['same'], ['q'],
            projection, responses, [FiniteOperation('step', 1, outputs)], True)
        identity = problem.fingerprint
        responses[0][0], projection[0], outputs[0] = 'no', 1, None
        self.assertEqual(problem.fingerprint, identity)
        self.assertEqual(analyze_task_abstraction(problem).status, 'verified')
        for values in ((-1,), (True,)):
            with self.assertRaises(ValueError):
                AbstractionWitness('judgment', 'q', values, (0,))

    def test_demo_and_hash_seed_determinism(self):
        report = build_abstraction_demo_report()
        self.assertEqual(report['fairness']['old_certificate_under_new_spec'], 'invalid')
        self.assertEqual(report['resource_composition']['coarse_model']['review'], 'valid')
        self.assertEqual(report['resource_composition']['refined_model']['review'], 'valid')
        serialized = []
        for seed in ('1', '2'):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            output = subprocess.check_output([sys.executable, '-m', 'bidirectional_modeling.cli',
                'abstraction-demo', '--json'], env=env, text=True)
            self.assertEqual(json.loads(output), json.loads(json.dumps(report)))
            serialized.append(output)
        self.assertEqual(*serialized)


if __name__ == '__main__':
    unittest.main()
