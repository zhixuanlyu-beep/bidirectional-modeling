"""Experiments separating consumed work, exact conditions and claim scope."""
from dataclasses import asdict, replace
from fractions import Fraction
import json
import unittest

from bidirectional_modeling import (Aggregation, Context, Experiment, FieldRequirement,
    InterpretationObservation, Interpreter, Realizer, ResourceBudget, SatisfactionEvaluator, Trace)
from bidirectional_modeling.core import Concept, Counterexample, VerificationIssue
from bidirectional_modeling.extensions.concepts import ConceptLibrary
from bidirectional_modeling.probes import HorizonExtensionProbe
from bidirectional_modeling.structural import freeze_value
from test_interpretation_status import model, spec, hypothesis


class WorkAccountingTests(unittest.TestCase):
    def test_internal_failure_cannot_run_three_candidates_for_one_credit(self):
        calls = []
        def crash(s, a, c):
            calls.append(1)
            raise RuntimeError('work performed before failure')
        models = tuple(replace(model(str(i)), transition=crash) for i in range(3))
        result = Realizer().realize(spec(), Context(), models, ResourceBudget(max_simulations=1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.simulations_used, 1)
        self.assertEqual(result.searched_candidates, 1)
        self.assertTrue(result.truncated)
        self.assertFalse(result.candidates or result.rejected)
        cert = result.undecided[0].certificate
        self.assertEqual(cert.verified_scenarios, 0)
        self.assertEqual(cert.simulations_used, 1)

    def test_failure_after_a_prefix_preserves_evidence_but_reserves_work(self):
        class Interrupted:
            name = 'partial'
            metrics = model().metrics
            def simulate(self, context, horizon):
                yield Trace(self.name, 's', 'baseline', ({'x': 1}, {'x': 1}))
                raise RuntimeError('later scenario failed')
        evaluator, m, c = SatisfactionEvaluator(), Interrupted(), Context()
        batch = evaluator.collect(m, c, 1, ResourceBudget(max_simulations=3))
        self.assertEqual(len(batch.traces), 1)
        self.assertEqual(batch.simulations_used, 3)
        cert = evaluator.evaluate_batch(m, spec(), c, batch)
        self.assertEqual(cert.verified_scenarios, 1)
        self.assertEqual(cert.simulations_used, 3)
        self.assertTrue(cert.binds_trace_batch(batch))
        self.assertFalse(replace(cert, charged_simulations=1).binds_trace_batch(batch))

    def test_discarded_trace_still_costs_work(self):
        class Invalid:
            name = 'invalid'
            metrics = model().metrics
            def simulate(self, context, horizon): return [object()]
        batch = SatisfactionEvaluator().collect(Invalid(), Context(), 1, ResourceBudget(max_simulations=2))
        self.assertFalse(batch.traces)
        self.assertEqual(batch.simulations_used, 2)
        self.assertFalse(batch.complete)

    def test_interpreter_does_not_start_another_horizon_after_failure(self):
        calls = []
        def crash(s, a, c):
            calls.append(1)
            raise RuntimeError('failed')
        hs = (hypothesis('one'), replace(hypothesis('two'), spec=replace(spec(), horizon=2)))
        result = Interpreter().interpret(replace(model(), transition=crash), Context(), hs,
                                         budget=ResourceBudget(max_simulations=1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.simulations_used, 1)
        self.assertEqual(result.identification_status, 'undecided')


class ExactMeanTests(unittest.TestCase):
    def test_large_integer_mean_is_not_rounded_into_a_false_equality(self):
        n = 2**53
        trace = Trace('m', 's', 'baseline', ({'x': n}, {'x': n+1}))
        requirement = FieldRequirement('mean', 'x', 'eq', n, aggregation=Aggregation.MEAN)
        self.assertFalse(requirement.evaluate(model(), (trace,), Context()).passed)
        check = replace(requirement, expected=Fraction(2*n+1, 2)).evaluate(model(), (trace,), Context())
        self.assertTrue(check.passed)
        self.assertEqual(check.observed, (Fraction(2*n+1, 2),))

    def test_rational_targets_have_stable_identity_and_complete_certificates(self):
        goal = replace(spec(), objectives=(FieldRequirement('mean', 'x', 'eq', Fraction(1),
                                                           aggregation=Aggregation.MEAN),))
        cert = SatisfactionEvaluator().evaluate(model(), goal, Context())
        self.assertTrue(cert.complete and cert.satisfied)
        self.assertEqual(freeze_value(Fraction(2, 6)), freeze_value(Fraction(1, 3)))
        self.assertNotEqual(freeze_value(Fraction(1, 3)), freeze_value(float(Fraction(1, 3))))


class ProbeScopeTests(unittest.TestCase):
    def test_new_terminal_claim_does_not_refute_original_claim(self):
        m = replace(model(), transition=lambda s,a,c: {'x': 1-s['x'], 'y': 1})
        goal = spec(expected=0)
        result = Realizer(probes=(HorizonExtensionProbe(1),)).realize(goal, Context(), (m,))
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.rejected or result.undecided)
        item = result.candidates[0]
        self.assertTrue(item.certificate.satisfied)
        extra = item.counterexamples[0]
        self.assertFalse(extra.blocking)
        self.assertNotEqual(extra.witness['original_spec_fingerprint'], extra.witness['tested_spec_fingerprint'])
        required = Realizer(probes=(HorizonExtensionProbe(1, blocking=True),)).realize(goal, Context(), (m,))
        self.assertEqual(len(required.rejected), 1)
        self.assertTrue(required.rejected[0].certificate.satisfied)

    def test_advisory_probe_cannot_hide_a_later_required_probe(self):
        probes = (HorizonExtensionProbe(), HorizonExtensionProbe(blocking=True))
        result = Realizer(probes=probes).realize(spec(), Context(), (model(),),
                                               ResourceBudget(max_simulations=1))
        self.assertFalse(result.candidates or result.rejected)
        self.assertEqual(len(result.undecided), 1)
        self.assertTrue(result.truncated)

    def test_unrun_advisory_probe_does_not_erase_base_proof(self):
        result = Realizer(probes=(HorizonExtensionProbe(),)).realize(spec(), Context(), (model(),),
                                                                  ResourceBudget(max_simulations=1))
        self.assertEqual(len(result.candidates), 1)
        self.assertFalse(result.undecided)
        self.assertEqual(result.candidates[0].diagnostics[0].phase, 'probe-budget')


class JudgmentAndExclusionTests(unittest.TestCase):
    def test_concept_relation_requires_explicit_judgment(self):
        lib = ConceptLibrary((Concept('c', 'definition'),))
        counterexample = Counterexample('measurement-error-model', 'witness', {'x': 1}, ('condition',))
        with self.assertRaises(TypeError): lib.refine_from_counterexample('c', counterexample)
        with self.assertRaises(ValueError):
            lib.refine_from_counterexample('c', counterexample, source='', reason='why', applicability='relation')
        self.assertFalse(lib.history)
        lib.refine_from_counterexample('c', counterexample, source='reviewer', reason='why', applicability='relation')
        judgment = lib.history[0]
        self.assertEqual((judgment.source, judgment.reason, judgment.applicability), ('reviewer', 'why', 'relation'))
        with self.assertRaises(ValueError):
            lib.refine_from_counterexample('c', VerificationIssue('phase','error'),
                                            source='reviewer', reason='why', applicability='relation')

    def test_exclusion_export_contains_the_disproved_declaration(self):
        e = Experiment('probe', 'question', ('yes','no'))
        o = InterpretationObservation('probe', 'no', 'lab')
        allowed = {'probe': ['yes']}
        h = hypothesis('h', allowed=allowed)
        result = Interpreter().interpret(model(), Context(), (h,), experiments=(e,), observations=(o,))
        allowed['probe'].append('no')
        exclusion = result.excluded[0]
        self.assertEqual(exclusion.allowed_outcomes, ('yes',))
        self.assertEqual(exclusion.experiment, e)
        exported = json.loads(json.dumps(result.to_dict()))['excluded'][0]
        self.assertEqual(exported['allowed_outcomes'], ['yes'])
        self.assertEqual(exported['observation']['source'], 'lab')
        self.assertEqual(len(exported['spec_fingerprint']), 64)
        self.assertEqual(len(exported['context_fingerprint']), 64)
        with self.assertRaises(ValueError): replace(exclusion, allowed_outcomes=('no',))
        self.assertEqual(asdict(exclusion)['observation']['outcome'], 'no')
        restored = Interpreter().interpret(model(), Context(), (h,), experiments=(e,))
        self.assertFalse(restored.excluded)
        self.assertEqual(len(restored.candidates), 1)
