"""Behavioral regressions for the reduced public and proof-producing paths."""
from dataclasses import replace
import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from bidirectional_modeling import Context, EquivalenceSpec, ResourceBudget, SatisfactionEvaluator, SearchWorkBudget
from bidirectional_modeling.composition import CompositionExperiment, CompositionRule, CompositionRuleSelector, CompositionTest
from bidirectional_modeling.correspondence import CorrespondenceIssue, CorrespondenceValidator
from bidirectional_modeling.engine import behaviorally_equivalent
from bidirectional_modeling.examples import scale_correspondence_scenario
from bidirectional_modeling.search_partial import collect_partial_prediction, verify_partial_prediction
from test_interpretation_status import model, spec
from test_regressions import TupleTraceModel, scenario_context
from test_search_partial import partial_args


class DerivedCorrespondenceTests(unittest.TestCase):
    def certificate(self, wrong=False):
        c, lower, upper, lc, uc = scale_correspondence_scenario()
        if wrong:
            c = replace(c, projection=lambda s, c: {'total': s['left'] + s['right'] + 1})
        return CorrespondenceValidator().validate(c, lower, upper, lc, uc, horizon=2)

    def test_relation_claim_cannot_be_set_independently_of_evidence(self):
        good = self.certificate()
        self.assertEqual((good.status, good.commutes, good.passed), ('verified', True, True))
        for value in (False, None):
            with self.assertRaises(TypeError):
                replace(good, commutes=value)
        bad = self.certificate(wrong=True)
        self.assertEqual((bad.status, bad.commutes, bad.passed), ('refuted', False, False))

    def test_local_witness_survives_an_unresolved_global_verdict(self):
        bad = self.certificate(wrong=True)
        mixed = replace(bad, complete=False,
                        diagnostics=(CorrespondenceIssue('incomplete-domain', 'unvisited scenario'),))
        self.assertEqual((mixed.status, mixed.commutes, mixed.passed), ('undecided', False, False))
        self.assertEqual(mixed.counterexamples, bad.counterexamples)


class EquivalentBatchTests(unittest.TestCase):
    def test_empty_observations_are_not_an_equivalence_proof(self):
        self.assertIsNone(behaviorally_equivalent(TupleTraceModel('left', ()),
            TupleTraceModel('right', ()), spec(), scenario_context(('s', 'baseline'))))

    def test_complete_batches_can_prove_equivalence_or_difference(self):
        left = model()
        equal = replace(left, name='other')
        different = replace(left, name='different', readout=lambda s, c: {'x': 2})
        self.assertIs(behaviorally_equivalent(left, equal, spec(), Context()), True)
        self.assertIs(behaviorally_equivalent(left, different, spec(), Context()), False)

    def test_shared_budget_exhaustion_remains_unknown(self):
        self.assertIsNone(behaviorally_equivalent(model(), model(), spec(), Context(),
                                                budget=ResourceBudget(max_simulations=1)))

    def test_complete_but_foreign_batch_is_not_a_proof(self):
        goal, context = spec(), Context()
        batch = SatisfactionEvaluator().collect(replace(model(), name='foreign'), context, goal.horizon)
        evaluator = Mock()
        evaluator.collect.return_value = batch
        self.assertIsNone(behaviorally_equivalent(model(), model(), goal, context, evaluator=evaluator))


class CompositionExecutionTests(unittest.TestCase):
    def experiment(self, readout=lambda s, c: dict(s)):
        return CompositionExperiment('case', {'s': {'x': 0}}, ('s',), ('a',), readout,
            EquivalenceSpec(('x',)), (CompositionTest('zero', 's', ('a',), True, {'x': 0}),))

    def test_unstable_transition_is_not_a_refutation(self):
        count = [0]
        def alternating(s, a, c):
            count[0] += 1
            return {'x': count[0] % 2}
        report = CompositionRuleSelector().select((CompositionRule('unstable', alternating),),
                                                 (self.experiment(),))
        self.assertFalse(report.rejected or report.certified)
        self.assertEqual(len(report.undecided), 1)
        self.assertFalse(report.undecided[0].counterexamples)
        self.assertIn('non-deterministic', report.undecided[0].cases[0].tests[0].evaluation_error)

    def test_unstable_readout_is_not_a_refutation(self):
        count = [0]
        def alternating(s, c):
            count[0] += 1
            return {'x': count[0] % 2}
        report = CompositionRuleSelector().select((CompositionRule('stable', lambda s, a, c: s),),
                                                 (self.experiment(alternating),))
        self.assertFalse(report.rejected or report.certified)
        self.assertEqual(len(report.undecided), 1)
        self.assertFalse(report.undecided[0].counterexamples)

    def test_reliable_refutation_skips_residual_work_unless_requested(self):
        analyzer = Mock()
        analyzer.analyze.side_effect = RuntimeError('diagnostic unavailable')
        selector = CompositionRuleSelector(analyzer)
        rule = CompositionRule('wrong', lambda s, a, c: {'x': 1}, description_length=3)
        report = selector.select((rule,), (self.experiment(),), selection_policy='shortest_description')
        self.assertEqual(len(report.rejected), 1)
        analyzer.analyze.assert_not_called()
        self.assertIsNone(report.rejected[0].total_description_length)
        self.assertIsNone(report.rejected[0].cases[0].residual_report)
        diagnostic = selector.select((rule,), (self.experiment(),), full_diagnostics=True)
        analyzer.analyze.assert_called_once()
        self.assertEqual(len(diagnostic.rejected), 1)
        self.assertEqual(diagnostic.rejected[0].cases[0].analysis_error, 'RuntimeError: diagnostic unavailable')


class MatrixCollectionTests(unittest.TestCase):
    def test_partial_prediction_does_not_construct_a_full_search(self):
        with patch('bidirectional_modeling.search_adapter.ExecutableSearchAdapter.prepare',
                   side_effect=AssertionError('full adapter must not run')):
            result = collect_partial_prediction(*partial_args(), ('a',))
        self.assertIsNotNone(result.prediction)
        self.assertEqual(result.simulations_used, 2)
        self.assertEqual(verify_partial_prediction(*partial_args(), result.prediction).status, 'valid')

    def test_budgeted_domain_membership_does_not_publish_an_unchecked_prediction(self):
        result = collect_partial_prediction(*partial_args(), ('a',), budget=SearchWorkBudget(1))
        self.assertIsNone(result.prediction)
        self.assertEqual(result.reason, 'work_budget_exhausted')

    def test_reworded_collection_diagnostics_do_not_change_certificate_identity(self):
        class Reworded(SatisfactionEvaluator):
            def collect(self, *args):
                batch = super().collect(*args)
                return replace(batch, diagnostics=tuple(replace(d, detail='localized explanation')
                                                       for d in batch.diagnostics))
        args = partial_args()
        prediction = collect_partial_prediction(*args, ('a',)).prediction
        verdict = verify_partial_prediction(*args, prediction, max_simulations=2, evaluator=Reworded())
        self.assertEqual(verdict.status, 'valid')
        batch = SatisfactionEvaluator().collect(model(), Context(), 1, ResourceBudget(max_simulations=1))
        self.assertTrue(batch.diagnostics)
        with self.assertRaises(ValueError):
            replace(batch, diagnostics=tuple(replace(d, code='different_reason') for d in batch.diagnostics))


class EntryPointTests(unittest.TestCase):
    def test_common_import_and_engine_creation_do_not_load_specialized_services(self):
        code = '''import sys, json
import bidirectional_modeling as package
root_modules = sorted(sys.modules)
from bidirectional_modeling.engine import BidirectionalModelingEngine
e = BidirectionalModelingEngine()
print(json.dumps([root_modules, sorted(sys.modules), package.__all__, sorted(vars(e))]))'''
        root, engine, exports, services = json.loads(subprocess.check_output([sys.executable, '-c', code], text=True))
        for suffix in ('composition', 'residual', 'refinement', 'search_adapter',
                       'search_explanations', 'extensions.concepts'):
            self.assertNotIn('bidirectional_modeling.' + suffix, root)
            self.assertNotIn('bidirectional_modeling.' + suffix, engine)
        self.assertNotIn('CorrespondenceCertificate', exports)
        self.assertNotIn('_concepts', services)
        self.assertIn('MacroSpec', exports)


if __name__ == '__main__':
    unittest.main()
