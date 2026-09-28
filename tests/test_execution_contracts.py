"""Distinguish claim meaning, interrupted work, and failed prediction promotion."""
from dataclasses import replace
import unittest

from bidirectional_modeling import (Aggregation, Context, Interpreter, MacroAlternativeQuery,
    ObservedEffectGenerator, Realizer, ResourceBudget, ResponseConstraint, SatisfactionEvaluator,
    SearchObservation, Trace)
from bidirectional_modeling.engine import BidirectionalModelingEngine
from bidirectional_modeling.interpretation import CatalogHypothesisGenerator
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter
from bidirectional_modeling.search_lazy import LazyExecutableSearch
from test_interpretation_status import model, spec, hypothesis
from test_search_partial import partial_args


class EffectMeaningTests(unittest.TestCase):
    def test_maintain_rejects_an_intermediate_dip(self):
        h = next(h for h in ObservedEffectGenerator(2).generate(model(), Context())
                 if h.spec.observables == ('x',))
        self.assertEqual(h.spec.objectives[0].aggregation, Aggregation.EACH)
        self.assertTrue(SatisfactionEvaluator().evaluate(model(), h.spec, Context()).satisfied)
        dip = replace(model(), transition=lambda s, a, c: {'x': 1-s['x'], 'y': 1})
        cert = SatisfactionEvaluator().evaluate(dip, h.spec, Context())
        self.assertTrue(cert.complete)
        self.assertFalse(cert.satisfied)

    def test_nonconstant_effects_claim_only_the_declared_terminal_value(self):
        for initial, final in ((0, 1), (2, 1), (False, True)):
            traces = (Trace('sample', 's', 'baseline', ({'x': initial}, {'x': final})),)
            h = ObservedEffectGenerator().generate_from_traces(traces)[0]
            self.assertTrue(h.name.startswith('at horizon 1,'))
            self.assertEqual(h.spec.objectives[0].aggregation, Aggregation.FINAL)
            self.assertNotIn('increase', h.name)
            self.assertNotIn('decrease', h.name)


class FailureBudgetTests(unittest.TestCase):
    def test_late_evaluator_exception_cannot_spend_the_budget_again(self):
        class LateFailure(SatisfactionEvaluator):
            actual = 0
            def evaluate(self, *args, **kwargs):
                certificate = super().evaluate(*args, **kwargs)
                self.actual += certificate.verified_scenarios
                raise RuntimeError('after collection')
        evaluator = LateFailure()
        report = Realizer(evaluator).realize(spec(), Context(),
            tuple(model(str(i)) for i in range(3)), ResourceBudget(max_simulations=1, max_cost=7))
        self.assertEqual(evaluator.actual, 1)
        self.assertEqual(report.simulations_used, 1)
        self.assertEqual(report.searched_candidates, 1)
        self.assertTrue(report.truncated)
        self.assertFalse(report.rejected or report.candidates)
        self.assertEqual(report.undecided[0].certificate.max_cost, 7)
        self.assertEqual(report.undecided[0].diagnostics[0].phase, 'base-budget')

    def test_failure_text_is_not_part_of_protocol_identity(self):
        evaluator, m, goal, context = SatisfactionEvaluator(), model(), spec(), Context()
        left = evaluator.failure_certificate(m, goal, context, 'English', ResourceBudget(max_simulations=2))
        right = evaluator.failure_certificate(m, goal, context, '中文', ResourceBudget(max_simulations=2))
        changed = evaluator.failure_certificate(m, goal, context, 'English', ResourceBudget(max_simulations=3))
        self.assertEqual(left.protocol_fingerprint, right.protocol_fingerprint)
        self.assertNotEqual(left.failure_boundaries, right.failure_boundaries)
        self.assertNotEqual(left.protocol_fingerprint, changed.protocol_fingerprint)
        self.assertFalse(left.complete or left.satisfied)


class GenerationBoundaryTests(unittest.TestCase):
    def test_both_directions_do_not_pull_an_extra_candidate(self):
        for interpret in (False, True):
            produced = []
            def source():
                for name in ('one', 'two'):
                    produced.append(name)
                    yield hypothesis(name) if interpret else model(name)
            if interpret:
                report = Interpreter().interpret(model(), Context(), source(), budget=ResourceBudget(max_candidates=1))
                self.assertEqual(report.identification_status, 'undecided')
            else:
                report = Realizer().realize(spec(), Context(), source(), ResourceBudget(max_candidates=1))
            self.assertEqual(produced, ['one'])
            self.assertEqual(len(report.candidates), 1)
            self.assertTrue(report.truncated)

    def test_concrete_catalogues_complete_at_the_exact_candidate_limit(self):
        for source in ((hypothesis('only'),), CatalogHypothesisGenerator((hypothesis('only'),))):
            report = Interpreter().interpret(model(), Context(), source, budget=ResourceBudget(max_candidates=1))
            self.assertFalse(report.truncated)
            self.assertEqual(report.identification_status, 'unique')
        report = Realizer().realize(spec(), Context(), (model(),), ResourceBudget(max_candidates=1))
        self.assertFalse(report.truncated)

    def test_unknown_length_at_exact_limit_does_not_claim_exhaustion(self):
        report = Interpreter().interpret(model(), Context(), iter((hypothesis('only'),)),
                                         budget=ResourceBudget(max_candidates=1))
        self.assertTrue(report.truncated)
        self.assertEqual(report.identification_status, 'undecided')

    def test_midstream_failure_keeps_the_verified_prefix_and_diagnostic(self):
        for interpret in (False, True):
            def source():
                yield hypothesis('first') if interpret else model('first')
                raise RuntimeError('catalogue unavailable')
            report = (Interpreter().interpret(model(), Context(), source()) if interpret else
                      Realizer().realize(spec(), Context(), source()))
            self.assertTrue(report.truncated)
            self.assertEqual(len(report.candidates), 1)
            self.assertFalse(report.rejected)
            self.assertEqual(report.diagnostics[0].phase, 'candidate-generation')
            self.assertIn('catalogue unavailable', report.diagnostics[0].reason)
            if interpret:
                self.assertEqual(report.identification_status, 'undecided')
                self.assertTrue(report.to_dict()['diagnostics'])

    def test_factory_and_iterator_initialization_errors_become_diagnostics(self):
        class FactoryFailure:
            def generate(self, *args): raise RuntimeError('factory unavailable')
        class IteratorFailure:
            def __iter__(self): raise RuntimeError('iterator unavailable')
        for source in (FactoryFailure(), IteratorFailure()):
            for interpret in (False, True):
                report = (Interpreter().interpret(model(), Context(), source) if interpret else
                          Realizer().realize(spec(), Context(), source))
                self.assertTrue(report.truncated)
                self.assertFalse(report.candidates or report.rejected)
                self.assertTrue(report.diagnostics)

    def test_empty_catalogues_are_complete(self):
        self.assertFalse(Realizer().realize(spec(), Context(), ()).truncated)
        report = Interpreter().interpret(model(), Context(), ())
        self.assertFalse(report.truncated)
        self.assertEqual(report.identification_status, 'empty_catalogue')

    def test_round_trip_does_not_materialize_an_unbounded_hypothesis_source(self):
        produced = []
        def source():
            produced.append('first')
            yield hypothesis('first')
            raise AssertionError('must not pull the next candidate')
        report = BidirectionalModelingEngine().macro_round_trip(spec(), Context(),
            (model(),), source(), budget=ResourceBudget(max_candidates=1))
        self.assertEqual(produced, ['first'])
        self.assertTrue(report.truncated)
        self.assertFalse(report.compatibility_passed)


class PromotionBoundaryTests(unittest.TestCase):
    def args(self):
        p, c, cases = partial_args()
        p = replace(p, constraints=(ResponseConstraint('requires-high', (3,)),))
        bad = replace(c, commitments=('requires-high',))
        good = replace(c, model=replace(c.model, name='good'))
        return p, bad, good, cases

    def lazy(self):
        p, bad, good, cases = self.args()
        return LazyExecutableSearch(p, (bad, good), cases, target='out',
                                    world_answers=('low', 'mixed', 'mixed', 'high'))

    def test_failed_promotion_preserves_partial_cache_and_finite_snapshot(self):
        lazy = self.lazy()
        first = lazy.predict_experiments('zero', ('a',))
        before = lazy.snapshot.fingerprint
        failed = lazy.predict_experiments('zero', ('b',))
        self.assertIsNone(failed.prediction)
        self.assertEqual(failed.reason, 'prediction_declaration_invalid')
        self.assertEqual(lazy.snapshot.fingerprint, before)
        self.assertEqual(lazy.predict_experiments('zero', ('a',), max_simulations=0).prediction, first.prediction)

    def test_screening_keeps_other_candidates_and_does_not_invent_a_counterexample(self):
        report = self.lazy().screen_evidence((SearchObservation('a', '0', 'lab'),
                                              SearchObservation('b', '0', 'lab')))
        self.assertEqual(report.undecided, ('zero',))
        self.assertEqual(report.matching_evidence, ('good',))
        self.assertFalse(report.excluded or report.certificates)
        self.assertIn('commitment', report.diagnostics[0][2])

    def test_complete_and_promoted_predictions_report_the_same_failure(self):
        p, bad, _, cases = self.args()
        full = ExecutableSearchAdapter().prepare(p, (bad,), cases, target='out',
            world_answers=('low', 'mixed', 'mixed', 'high'))
        partial = self.lazy().predict_experiments('zero', ('a', 'b'))
        self.assertIsNone(full.search.hypotheses[0].world)
        self.assertIsNone(partial.prediction)
        self.assertEqual(full.diagnostics, partial.diagnostics)
        result = self.lazy().execute(MacroAlternativeQuery('high'))
        self.assertEqual(result.receipt.witness_candidate, 'good')


if __name__ == '__main__':
    unittest.main()
