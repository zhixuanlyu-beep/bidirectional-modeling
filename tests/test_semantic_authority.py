"""Separate current evidence, derived verdicts and explicit human judgments."""
from dataclasses import replace
from unittest.mock import Mock
import unittest

from bidirectional_modeling import Context, Interpreter, Realizer, ResourceBudget, SatisfactionEvaluator
from bidirectional_modeling.core import Concept, Counterexample
from bidirectional_modeling.engine import BidirectionalModelingEngine, behaviorally_equivalent
from bidirectional_modeling.extensions.concepts import ConceptLibrary
from test_interpretation_status import hypothesis, model, spec


class VerdictAuthority(unittest.TestCase):
    def test_truth_flags_are_explicit_booleans(self):
        cert = SatisfactionEvaluator().evaluate(model(), spec(), Context())
        batch = SatisfactionEvaluator().collect(model(), Context(), 1)
        for value in ('false', 0, 1, None):
            for result, field in ((cert.checks[0], 'passed'), (cert, 'complete'), (batch, 'complete')):
                with self.subTest(value=value, field=field):
                    with self.assertRaises(TypeError):
                        replace(result, **{field: value})

    def test_check_results_are_the_only_source_of_satisfaction(self):
        good = SatisfactionEvaluator().evaluate(model(), spec(), Context())
        bad_check = replace(good.checks[0], passed=False)
        failed = replace(good, checks=(bad_check,))
        self.assertFalse(failed.requirements_passed)
        self.assertFalse(failed.satisfied)
        self.assertTrue(failed.complete)
        empty = replace(good, verified_scenarios=0)
        self.assertFalse(empty.requirements_passed or empty.satisfied)
        pending = replace(good, complete=False)
        self.assertTrue(pending.requirements_passed)
        self.assertFalse(pending.satisfied)
        for name in ('satisfied', 'requirements_passed'):
            with self.assertRaises(TypeError):
                replace(good, **{name: True})

    def test_foreign_complete_verdicts_cannot_accept_or_reject_current_candidate(self):
        m, goal, ctx = model(), spec(), Context()
        for passed in (True, False):
            for change in ('context', 'spec', 'model', 'cost'):
                with self.subTest(passed=passed, change=change):
                    foreign_model = model('other') if change == 'model' else m
                    foreign_spec = replace(goal, name='other') if change == 'spec' else goal
                    foreign_context = replace(ctx, observer='other') if change == 'context' else ctx
                    foreign_budget = ResourceBudget(max_cost=999) if change == 'cost' else ResourceBudget(max_cost=10)
                    cert = SatisfactionEvaluator().evaluate(foreign_model, foreign_spec, foreign_context, foreign_budget)
                    if not passed:
                        cert = replace(cert, checks=(replace(cert.checks[0], passed=False),))
                    evaluator = Mock()
                    evaluator.evaluate.return_value = cert
                    r = Realizer(evaluator).realize(goal, ctx, (m,), ResourceBudget(max_cost=10, max_simulations=7))
                    self.assertFalse(r.candidates or r.rejected)
                    self.assertTrue(r.undecided and r.truncated)
                    self.assertEqual(r.simulations_used, 7)

    def test_interpretation_rejects_a_certificate_from_another_context_or_batch(self):
        m, goal, ctx = model(), spec(), Context()
        base = SatisfactionEvaluator()
        foreign_context = replace(ctx, observer='other')
        other_model = replace(m, readout=lambda s,c: {'x': 1, 'y': 2})
        for cert in (base.evaluate(m, goal, foreign_context), base.evaluate(other_model, goal, ctx)):
            evaluator = Mock()
            evaluator.collect.side_effect = base.collect
            evaluator.evaluate_batch.return_value = cert
            r = Interpreter(evaluator).interpret(m, ctx, (hypothesis('one'),), budget=ResourceBudget(max_simulations=7))
            self.assertFalse(r.candidates or r.rejected)
            self.assertEqual(r.identification_status, 'undecided')
            self.assertEqual(r.simulations_used, 7)

    def test_probe_cannot_relabel_a_foreign_task_certificate(self):
        from bidirectional_modeling.probes import HorizonExtensionProbe
        class ReusesBase(SatisfactionEvaluator):
            def evaluate(self, m, goal, ctx, budget):
                return super().evaluate(m, spec(), ctx, budget)
        r = Realizer(ReusesBase(), probes=(HorizonExtensionProbe(blocking=True),)).realize(
            spec(), Context(), (model(),), ResourceBudget(max_simulations=7))
        self.assertFalse(r.candidates or r.rejected)
        self.assertTrue(r.undecided and r.truncated)
        self.assertEqual(r.simulations_used, 7)

    def test_interpretation_does_not_evaluate_a_foreign_collected_batch(self):
        evaluator = Mock()
        evaluator.collect.return_value = SatisfactionEvaluator().collect(model(),
            replace(Context(), observer='foreign'), 1)
        r = Interpreter(evaluator).interpret(model(), Context(), (hypothesis('one'),),
            budget=ResourceBudget(max_simulations=7))
        self.assertEqual(r.identification_status, 'undecided')
        self.assertEqual(r.simulations_used, 7)
        self.assertFalse(r.candidates or r.rejected)
        evaluator.evaluate_batch.assert_not_called()

    def test_certificate_owns_its_check_sequence(self):
        cert = SatisfactionEvaluator().evaluate(model(), spec(), Context())
        checks = list(cert.checks)
        snapshot = replace(cert, checks=checks)
        checks.clear()
        self.assertTrue(snapshot.satisfied)
        with self.assertRaises(TypeError):
            replace(cert, checks=(None,))


class RoundTripBoundaries(unittest.TestCase):
    def test_micro_rechecks_the_original_satisfaction_evidence(self):
        original = model()
        class Source:
            def generate(self, *args):
                original.readout = lambda s,c: {'x': 1, 'y': 2}
                return (replace(model('other'), readout=original.readout),)
        report = BidirectionalModelingEngine().micro_round_trip(original, Context(),
            (hypothesis('one'),), Source())
        self.assertFalse(report.passed or report.behaviorally_equivalent_models)
        self.assertTrue(report.truncated and report.diagnostics)
        self.assertTrue(report.realization.candidates)

    def test_macro_recovery_cannot_ignore_changed_model_evidence(self):
        class Hypotheses:
            def generate(self, m, context):
                m.readout = lambda s,c: {'x': 1, 'y': 2}
                return (hypothesis('one'),)
        report = BidirectionalModelingEngine().macro_round_trip(spec(), Context(),
            (model(),), Hypotheses())
        self.assertFalse(report.compatibility_passed)
        self.assertEqual(report.semantic_preservation, (False,))
        self.assertEqual(report.interpretations[0].identification_status, 'undecided')

    def test_bad_collection_outputs_never_start_the_second_comparison(self):
        m = model()
        base = SatisfactionEvaluator()
        for output in (None, base.collect(m, Context(), 1, ResourceBudget(max_simulations=2))):
            evaluator = Mock()
            evaluator.collect.return_value = output
            result = behaviorally_equivalent(m, model('other'), spec(), Context(),
                budget=ResourceBudget(max_simulations=1), evaluator=evaluator)
            self.assertIsNone(result)
            self.assertEqual(evaluator.collect.call_count, 1)

    def test_callback_failure_remains_unknown(self):
        evaluator = Mock()
        evaluator.collect.side_effect = RuntimeError('work before failure')
        self.assertIsNone(behaviorally_equivalent(model(), model('other'), spec(), Context(), evaluator=evaluator))
        self.assertEqual(evaluator.collect.call_count, 1)

    def test_micro_round_trip_reserves_unknown_comparison_work(self):
        class BrokenComparison(SatisfactionEvaluator):
            calls = 0
            def collect(self, *args):
                self.calls += 1
                if self.calls >= 3:
                    return None
                return super().collect(*args)
        evaluator = BrokenComparison()
        engine = BidirectionalModelingEngine(realizer=Realizer(evaluator))
        report = engine.micro_round_trip(model(), Context(), (hypothesis('one'),), (model('other'),),
            budget=ResourceBudget(max_simulations=9))
        self.assertFalse(report.passed or report.behaviorally_equivalent_models)
        self.assertTrue(report.truncated)
        self.assertEqual(report.simulations_used, 9)
        self.assertEqual(evaluator.calls, 3)
        self.assertTrue(report.diagnostics)

    def test_macro_round_trip_reuses_the_same_declared_evidence(self):
        from bidirectional_modeling import Evidence, PurposeLevel
        evidence = Evidence('actor choice', 'one', 'choice', 'actor')
        h = replace(hypothesis('one'), level=PurposeLevel.INTENTION)
        report = BidirectionalModelingEngine().macro_round_trip(spec(), Context(),
            (model('a'), model('b')), (h,), evidence=iter((evidence,)))
        self.assertTrue(report.compatibility_passed)
        self.assertEqual(len(report.interpretations), 2)
        for result in report.interpretations:
            self.assertEqual(result.candidates[0].direct_intent_evidence, (evidence,))


class JudgmentBoundaries(unittest.TestCase):
    def test_equal_structural_witnesses_do_not_create_new_concept_versions(self):
        lib = ConceptLibrary((Concept('c', 'definition'),))
        values = ({'x': {'b': 2, 'a': 1}, 'y': {'z', 'a'}},
                  {'y': {'a', 'z'}, 'x': {'a': 1, 'b': 2}})
        for value in values:
            lib.refine_from_counterexample('c', Counterexample('failure', 'scope', value, ('claim',)),
                source='reviewer', reason='explicit relation', applicability='this scope')
        self.assertEqual(lib.get('c').version, 2)
        self.assertEqual(len(lib.get('c').negative_examples), 1)
        self.assertEqual(len(lib.history), 2)

    def test_judgment_must_be_an_explicit_boolean(self):
        lib = ConceptLibrary((Concept('c', 'definition'),))
        for value in ('false', 0, 1, None):
            with self.assertRaises(TypeError):
                lib.record_judgment('c', 'example', value, source='reviewer')
        self.assertFalse(lib.history)
        self.assertEqual(lib.get('c').version, 1)
        lib.record_judgment('c', 'example', False, source='reviewer')
        self.assertEqual(lib.get('c').negative_examples, ('example',))

    def test_initial_catalogue_cannot_silently_overwrite_a_concept(self):
        with self.assertRaises(ValueError):
            ConceptLibrary((Concept('c', 'one'), Concept('c', 'two')))


class ActionSupportBoundaries(unittest.TestCase):
    def test_unknown_support_cannot_become_a_proven_undefined_transition(self):
        from bidirectional_modeling.refinement import ClosureAnalyzer
        from bidirectional_modeling.residual import ResidualQuotientAnalyzer
        for value in ('false', 0, 1, None):
            m = replace(model(), applicable=lambda s,a,c: value)
            with self.subTest(value=value):
                quotient = ResidualQuotientAnalyzer().analyze(m, spec().equivalence, Context())
                closure = ClosureAnalyzer().analyze(m, spec(), Context())
                self.assertFalse(quotient.complete or quotient.minimal)
                self.assertFalse(closure.complete or closure.closed)
                self.assertTrue(quotient.boundaries and closure.diagnostics)
                self.assertFalse(closure.counterexamples)
        for value in (False, True):
            m = replace(model(), applicable=lambda s,a,c: value)
            quotient = ResidualQuotientAnalyzer().analyze(m, spec().equivalence, Context())
            self.assertTrue(quotient.complete and quotient.minimal)


if __name__ == '__main__':
    unittest.main()
