"""Distinguishing experiments for concepts, witnesses, and exclusion scope."""
import io
import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from itertools import product
from unittest.mock import patch

from bidirectional_modeling import (Aggregation, Context, DescriptionLength, ExperimentHypothesisSearch, FieldRequirement, ResourceBudget, ResponseConstraint, SatisfactionEvaluator, SearchExperiment, SearchHypothesis, SearchObservation, SearchProtocol, Trace)
from bidirectional_modeling.engine import (BidirectionalModelingEngine)
from bidirectional_modeling.certificate_transport import (transport_conflict)
from bidirectional_modeling import (cli)
from bidirectional_modeling.examples import scale_correspondence_scenario
from test_context_network import identity_transition
from test_interpretation_status import model, spec
from test_regressions import TupleTraceModel, scenario_context


def unavailable(key):
    raise RuntimeError('scenario translation unavailable')


class AggregationContracts(unittest.TestCase):
    def evaluate(self, aggregation, operator, expected):
        trace = Trace('sequence', 's', 'baseline', tuple({'x': v} for v in (8, 2, 12, 7)))
        requirement = FieldRequirement('condition', 'x', operator, expected, aggregation=aggregation)
        goal = replace(spec(), objectives=(requirement,), horizon=3)
        return SatisfactionEvaluator().evaluate(TupleTraceModel('sequence', (trace,)),
            goal, scenario_context(('s','baseline')))

    def test_each_scalar_aggregation_has_a_distinct_observed_value(self):
        expected = {Aggregation.INITIAL: 8, Aggregation.FINAL: 7, Aggregation.MIN: 2,
                    Aggregation.MAX: 12, Aggregation.MEAN: 7.25, Aggregation.DELTA: -1}
        for aggregation, value in expected.items():
            with self.subTest(aggregation=aggregation):
                passed = self.evaluate(aggregation, 'eq', value)
                failed = self.evaluate(aggregation, 'eq', value+1)
                self.assertTrue(passed.complete and passed.satisfied)
                self.assertEqual(passed.checks[0].observed, (value,))
                self.assertTrue(failed.complete)
                self.assertFalse(failed.satisfied)

    def test_each_cannot_be_substituted_by_final_value(self):
        self.assertTrue(self.evaluate(Aggregation.EACH, 'ge', 2).satisfied)
        self.assertFalse(self.evaluate(Aggregation.EACH, 'ge', 3).satisfied)
        self.assertTrue(self.evaluate(Aggregation.FINAL, 'ge', 3).satisfied)

    def test_exact_typed_membership_and_equality(self):
        for operator, expected, answer in (('in',(True,),False), ('in',(1,),True),
                                          ('eq',True,False), ('ne',True,True),
                                          ('ne',1,False), ('ne',2,True)):
            with self.subTest(operator=operator, expected=expected):
                goal = replace(spec(), objectives=(FieldRequirement('typed','x',operator,expected),))
                certificate = SatisfactionEvaluator().evaluate(model(),goal,Context())
                self.assertTrue(certificate.complete)
                self.assertEqual(certificate.satisfied,answer)

    def test_unknown_aggregation_is_rejected_at_declaration(self):
        with self.assertRaises(TypeError):
            FieldRequirement('typo','x','eq',1,aggregation='typo')


class CorrespondenceContracts(unittest.TestCase):
    def verify(self, transform=lambda c:c, budget=None):
        c, lower, upper, lc, uc = scale_correspondence_scenario()
        return BidirectionalModelingEngine().verify_correspondence(transform(c), lower, upper,
            lc, uc, horizon=2, budget=budget, record=False)

    def test_success_and_observed_mismatch_have_different_witnesses(self):
        good = self.verify()
        self.assertEqual(good.status,'verified')
        self.assertTrue(good.passed)
        bad = self.verify(lambda c: replace(c, projection=lambda s, c: {'total':s['left']+s['right']+1}))
        self.assertTrue(bad.complete)
        self.assertEqual(bad.status,'refuted')
        self.assertFalse(bad.passed)
        self.assertFalse(bad.diagnostics or bad.applicability_failures)
        for witness in bad.counterexamples:
            self.assertEqual(witness.kind,'non-commuting-step')
            self.assertEqual(witness.projected_snapshot['total'], witness.upper_snapshot['total']+1)
            self.assertIsNotNone(witness.step)

    def test_execution_failure_is_not_a_behavioral_counterexample(self):
        failed = self.verify(lambda c: replace(c, scenario_projection=unavailable))
        self.assertEqual(failed.status,'undecided')
        self.assertFalse(failed.passed)
        self.assertIsNone(failed.commutes)
        self.assertFalse(failed.counterexamples)
        self.assertIn('scenario-projection-failed',{d.kind for d in failed.diagnostics})
        with self.assertRaises(ValueError): replace(failed, complete=True)

    def test_missing_interface_is_not_an_execution_error_or_behavioral_counterexample(self):
        failed = self.verify(lambda c: replace(c, projection=lambda s,c:{'wrong':0}))
        self.assertEqual(failed.status,'not_applicable')
        self.assertFalse(failed.passed or failed.counterexamples or failed.diagnostics)
        self.assertIsNone(failed.commutes)
        self.assertEqual({d.kind for d in failed.applicability_failures},{'projection-interface-mismatch'})

    def test_budget_exhaustion_does_not_claim_commutation(self):
        failed = self.verify(budget=ResourceBudget(max_simulations=1))
        self.assertEqual(failed.status,'undecided')
        self.assertIsNone(failed.commutes)
        self.assertFalse(failed.passed or failed.counterexamples)
        self.assertTrue(failed.diagnostics)

    def test_suite_does_not_claim_unstarted_cases_commute(self):
        correspondence, cases = cli.scale_correspondence_suite()
        report = BidirectionalModelingEngine().verify_correspondence_suite(
            correspondence, cases, budget=ResourceBudget(max_simulations=1), record=False)
        self.assertTrue(report.truncated)
        self.assertIsNone(report.commutes)
        for case in report.cases:
            self.assertFalse(case.certificate.passed)
            self.assertIsNone(case.certificate.commutes)
            self.assertEqual(case.certificate.status, 'undecided')


class ExclusionConeContracts(unittest.TestCase):
    def setUp(self):
        worlds = tuple(product(('0','1'),repeat=2))
        self.protocol = SearchProtocol('scope','code',(SearchExperiment('a','a'),SearchExperiment('b','b')),
            worlds,(ResponseConstraint('u',(0,2,3)),ResponseConstraint('v',(1,2,3))))
        self.hypotheses = tuple(SearchHypothesis(n,w,'same',DescriptionLength(),cs,('shared',))
            for n,w,cs in [('u',0,('u',)),('v',1,('v',)),('joint',2,('u','v'))])
        self.search = ExperimentHypothesisSearch(self.protocol,self.hypotheses,'g')
        self.evidence = (SearchObservation('a','0','lab'),)
        self.certificate = self.search.learn_conflict(('u','v'),self.evidence)

    def test_joint_conflict_only_prunes_full_commitment_supersets(self):
        self.assertEqual(set(self.certificate.commitments),{'u','v'})
        report = self.search.search(self.evidence,(self.certificate,),learn_conflicts=False)
        self.assertEqual(set(report.compatible),{'u','v'})
        self.assertEqual(report.pruned,('joint',))

    def test_retraction_and_rebinding_remove_the_old_exclusion_cone(self):
        self.assertFalse(self.search.validates_conflict(self.certificate,()))
        report = self.search.search((),(self.certificate,),learn_conflicts=False)
        self.assertFalse(report.pruned)
        self.assertIn('joint',report.compatible)
        rebound = tuple(replace(o,source='other lab') for o in self.evidence)
        self.assertFalse(self.search.validates_conflict(self.certificate,rebound))
        changed = ExperimentHypothesisSearch(replace(self.protocol,scope='other'),self.hypotheses,'g')
        self.assertFalse(changed.validates_conflict(self.certificate,self.evidence))

    def test_target_self_inconsistency_cannot_inherit_empirical_refutation(self):
        p = SearchProtocol('old','code',(SearchExperiment('a','a'),),(('0',),('1',)),
                           (ResponseConstraint('C',(0,)),))
        q = replace(p,scope='new',constraints=(ResponseConstraint('C',()),))
        old,new = ExperimentHypothesisSearch(p,(),'g'),ExperimentHypothesisSearch(q,(),'g')
        evidence = (SearchObservation('a','1','lab'),)
        c = old.learn_conflict(('C',),evidence)
        self.assertTrue(old.validates_conflict(c,evidence))
        receipt = transport_conflict(old,new,identity_transition(p,q),c,evidence,evidence,tuple(zip(evidence,evidence)))
        self.assertEqual(receipt.status,'not_applicable')
        self.assertEqual(receipt.reason,'target_contradiction_not_established')
        self.assertIsNone(receipt.certificate)


class PresentationContracts(unittest.TestCase):
    def run_cli(self, json_output):
        correspondence,cases = cli.scale_correspondence_suite()
        correspondence = replace(correspondence,scenario_projection=unavailable)
        out=io.StringIO()
        with patch.object(cli,'scale_correspondence_suite',return_value=(correspondence,cases)), \
             patch('sys.argv',['bidirectional-modeling','demo']+(['--json'] if json_output else [])), redirect_stdout(out):
            cli.main()
        return out.getvalue()

    def test_json_preserves_unknown_and_does_not_present_errors_as_refutations(self):
        report=json.loads(self.run_cli(True))
        for case in report['correspondence']['cases']:
            self.assertEqual(case['status'],'undecided')
            self.assertFalse(case['passed'] or case['counterexamples'])
            self.assertIsNone(case['commutes'])
            self.assertTrue(case['diagnostics'])

    def test_human_output_explicitly_labels_undecided_cases(self):
        self.assertIn('未决',self.run_cli(False))
