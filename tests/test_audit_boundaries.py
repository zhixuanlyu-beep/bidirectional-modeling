"""Public claims distinguish missing evidence, refutation and execution failure."""
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from io import StringIO
import unittest
from unittest.mock import patch

from bidirectional_modeling import (Context, Evidence, Interpreter, ObservedEffectGenerator,
    ResourceBudget, SatisfactionEvaluator, SearchWorkBudget)
from bidirectional_modeling.cli import build_demo_report, _print_human
from bidirectional_modeling.examples import scale_correspondence_suite
from bidirectional_modeling.provenance import context_fingerprint
from bidirectional_modeling.search_examples import conflict_search_scenario
from test_interpretation_status import model, hypothesis


class AuditBoundaryTests(unittest.TestCase):
    def test_cli_cannot_label_a_refuted_holdout_as_passed(self):
        correspondence, cases = scale_correspondence_suite()
        bad = replace(cases[1], upper_model=replace(cases[1].upper_model,
                                                   states={'aggregate': {'total': 6}}))
        with patch('bidirectional_modeling.cli.scale_correspondence_suite',
                   return_value=(correspondence, (cases[0], bad))):
            report = build_demo_report()
        self.assertTrue(report['correspondence']['independent_holdout_declared'])
        self.assertEqual(report['correspondence']['independent_holdout_status'], 'refuted')
        output = StringIO()
        with redirect_stdout(output):
            _print_human(report)
        self.assertIn('留出复核：已反驳', output.getvalue())
        self.assertNotIn('留出复核：通过', output.getvalue())

    def test_identifiability_refuter_survives_unknown_candidates(self):
        search, _ = conflict_search_scenario()
        original = search.hypotheses[0]
        unknown = replace(original, name='unknown', world=None)
        opposite = replace(original, name='opposite', macro_answer='opposite answer')
        pending = search.with_hypotheses((unknown, original)).macro_identifiable()
        self.assertEqual((pending.status, pending.reason), ('undecided', 'unknown_prediction'))
        failed = search.with_hypotheses((unknown, original, opposite)).macro_identifiable()
        self.assertEqual(failed.status, 'non_identifiable')
        self.assertEqual(failed.witness_candidates, (original.name, 'opposite'))
        with self.assertRaises(TypeError):
            bool(pending)
        for budget in (SearchWorkBudget(0), SearchWorkBudget(cancelled=lambda: True)):
            self.assertEqual(search.macro_identifiable(budget=budget).status, 'undecided')

    def test_evaluator_failure_keeps_prefix_and_reserves_remaining_allowance(self):
        class Broken(SatisfactionEvaluator):
            calls = 0
            def evaluate_batch(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError('custom evaluator failed')
                return super().evaluate_batch(*args, **kwargs)
        evaluator = Broken()
        result = Interpreter(evaluator).interpret(model(), Context(),
            tuple(hypothesis(n) for n in ('first', 'second', 'third')),
            budget=ResourceBudget(max_simulations=6))
        self.assertEqual(tuple(c.hypothesis.name for c in result.candidates), ('first',))
        self.assertEqual(result.undecided[0][0], 'second')
        self.assertEqual(result.identification_status, 'undecided')
        self.assertEqual(result.simulations_used, 6)
        self.assertEqual(evaluator.calls, 2)
        self.assertTrue(result.diagnostics[0].witness['reserved_simulations'] > 0)
        self.assertFalse(result.rejected)

    def test_collection_failure_is_undecided_for_catalogue_and_effect_generator(self):
        class Broken(SatisfactionEvaluator):
            calls = 0
            def collect(self, *args, **kwargs):
                self.calls += 1
                super().collect(*args, **kwargs)
                raise RuntimeError('late collection failure')
        for source in ((hypothesis('candidate'),), ObservedEffectGenerator()):
            evaluator = Broken()
            result = Interpreter(evaluator).interpret(model(), Context(), source,
                budget=ResourceBudget(max_simulations=6))
            self.assertEqual(result.identification_status, 'undecided')
            self.assertEqual(result.simulations_used, 6)
            self.assertEqual(evaluator.calls, 1)
            self.assertFalse(result.candidates or result.rejected)

    def test_evidence_annotation_is_explicit_text_and_remains_bound(self):
        evidence = Evidence('statement', 'candidate', annotation='caller description')
        self.assertNotIn('strength', asdict(evidence))
        changed = replace(evidence, annotation='changed description')
        self.assertNotEqual(context_fingerprint(Context(history=(evidence,))),
                            context_fingerprint(Context(history=(changed,))))
        with self.assertRaises(TypeError):
            Evidence('statement', 'candidate', annotation=0.8)
