"""Set-valued interpretation must not silently turn ignorance into belief."""
import unittest
from dataclasses import replace
from itertools import combinations, product

from bidirectional_modeling import (
    BidirectionalModelingEngine, CandidateEvaluation, Context, Experiment,
    InterpretationObservation, PurposeHypothesis, PurposeLevel, ResourceBudget,
    VerificationMeasures, pareto_partition,
)
from bidirectional_modeling.examples import organization_interpretation_scenario
from bidirectional_modeling.interpretation import Interpreter
from test_regressions import TupleTraceModel, x_spec, scenario_context
from bidirectional_modeling import Trace


class SetInterpretationTests(unittest.TestCase):
    def setUp(self):
        self.model = TupleTraceModel('m', (Trace('m', 's', 'baseline', ({'x': 0}, {'x': 1})),))
        self.context = scenario_context(('s', 'baseline'))
        self.experiment = Experiment('e', 'result?', ('0', '1'))
        self.interpreter = Interpreter()

    def hypothesis(self, name, outcomes=None):
        return PurposeHypothesis(name, PurposeLevel.FUNCTION, x_spec(name),
            allowed_outcomes={} if outcomes is None else {'e': outcomes})

    def run_hypotheses(self, hypotheses, observations=(), **kwargs):
        return self.interpreter.interpret(self.model, self.context, hypotheses,
            experiments=(self.experiment,), observations=observations, **kwargs)

    def test_observation_filters_commitments_but_preserves_unknown_and_retraction(self):
        hypotheses = (self.hypothesis('A', ('0',)), self.hypothesis('B', ('1',)),
                      self.hypothesis('C'))
        observation = InterpretationObservation('e', '0', 'lab-v1')
        r = self.run_hypotheses(hypotheses, (observation,))
        self.assertEqual(tuple(c.hypothesis.name for c in r.candidates), ('A', 'C'))
        self.assertEqual(r.excluded, (('B', observation),))
        self.assertEqual(r.observations, (observation,))
        self.assertIsNone(r.discriminating_query)
        self.assertTrue(r.non_identifiable)
        restored = self.run_hypotheses(hypotheses)
        self.assertEqual(len(restored.candidates), 3)
        self.assertEqual(restored.excluded, ())
        self.assertEqual(restored.discriminating_query.allowed_outcomes['C'], ('0', '1'))

    def test_unknown_does_not_mean_equivalent_or_uniform(self):
        r = self.run_hypotheses((self.hypothesis('A'), self.hypothesis('B')))
        self.assertEqual(r.equivalent_explanations, ())
        self.assertIsNone(r.discriminating_query)
        self.assertTrue(r.non_identifiable)
        r = self.run_hypotheses((self.hypothesis('A', ('0',)), self.hypothesis('B')))
        self.assertIsNone(r.discriminating_query)  # Outcome 0 may retain both.

    def test_exhaustive_set_selection_agrees_with_worst_case_elimination(self):
        possible_sets = (('0',), ('1',), ('0', '1'))
        for declarations in product(possible_sets, repeat=3):
            hs = tuple(self.hypothesis(str(i), values) for i, values in enumerate(declarations))
            r = self.run_hypotheses(hs)
            signatures = set(frozenset(values) for values in declarations)
            outcomes = set().union(*signatures)
            guaranteed = min(sum(o not in s for s in signatures) for o in outcomes)
            if not guaranteed:
                self.assertIsNone(r.discriminating_query)
            else:
                self.assertEqual(r.discriminating_query.guaranteed_class_eliminations, guaranteed)
                pairs = sum(not a.intersection(b) for a, b in combinations(signatures, 2))
                self.assertEqual(r.discriminating_query.separated_class_pairs, pairs)

    def test_duplicate_responses_and_input_order_do_not_inflate_selection(self):
        hs = (self.hypothesis('A', ('0',)), self.hypothesis('B', ('1',)))
        before = self.run_hypotheses(hs).discriminating_query
        after = self.run_hypotheses((self.hypothesis('C', ('0',)),) + hs[::-1]).discriminating_query
        self.assertEqual(before.selection_score, after.selection_score)
        self.assertEqual(before.guaranteed_class_eliminations, after.guaranteed_class_eliminations)

    def test_multivalued_experiments_respect_cost_and_observed_instances(self):
        experiments = (Experiment('expensive', 'result?', ('0', '1', '2'), cost=5),
                       Experiment('cheap', 'result?', ('0', '1', '2'), cost=0))
        hs = tuple(PurposeHypothesis(str(i), PurposeLevel.FUNCTION, x_spec(str(i)),
                   allowed_outcomes={e.name: (str(i),) for e in experiments}) for i in range(3))
        r = self.interpreter.interpret(self.model, self.context, hs, experiments=experiments)
        self.assertEqual(r.discriminating_query.experiment.name, 'cheap')
        self.assertEqual(r.discriminating_query.guaranteed_class_eliminations, 2)
        observed = InterpretationObservation('cheap', '1', 'lab')
        r = self.interpreter.interpret(self.model, self.context, hs, experiments=experiments,
                                       observations=(observed,))
        self.assertEqual(tuple(c.hypothesis.name for c in r.candidates), ('1',))
        self.assertFalse(r.non_identifiable)
        self.assertIsNone(r.discriminating_query)

    def test_invalid_observations_and_declarations_are_rejected(self):
        hs = (self.hypothesis('A'),)
        for observation in (InterpretationObservation('absent', '0', 'lab'),
                            InterpretationObservation('e', 'outside', 'lab')):
            with self.assertRaises(ValueError):
                self.run_hypotheses(hs, (observation,))
        with self.assertRaises(ValueError):
            self.run_hypotheses(hs, (InterpretationObservation('e', '0', 'lab'),
                                    InterpretationObservation('e', '1', 'lab')))
        for values in ((), ('outside',), ('0', '0'), '0', (0.5,)):
            with self.assertRaises(ValueError):
                self.run_hypotheses((self.hypothesis('A', values),))
        with self.assertRaises(ValueError):
            self.run_hypotheses(hs + hs)
        with self.assertRaises(TypeError):
            PurposeHypothesis('A', PurposeLevel.FUNCTION, x_spec(), prior=0.5)

    def test_empty_or_truncated_result_never_claims_identification(self):
        self.assertTrue(self.run_hypotheses(()).non_identifiable)
        hs = (self.hypothesis('A'), self.hypothesis('B'))
        r = self.run_hypotheses(hs, budget=ResourceBudget(max_candidates=1))
        self.assertTrue(r.truncated)
        self.assertTrue(r.non_identifiable)

    def test_declarations_are_copied_and_experiments_require_domains(self):
        values = ['0']
        h = self.hypothesis('A', values)
        values.append('1')
        self.assertEqual(h.allowed_outcomes['e'], ('0',))
        with self.assertRaises(TypeError):
            h.allowed_outcomes['e'] = ('1',)
        with self.assertRaises(TypeError):
            Experiment('e', 'question')
        with self.assertRaises(ValueError):
            Experiment('e', 'question', ('0', '0'))

    def test_pareto_selection_ignores_subjective_metadata_and_diagnostic_scores(self):
        certificate = self.run_hypotheses((self.hypothesis('A'),)).candidates[0].certificate
        _, model, _, _, _ = organization_interpretation_scenario()
        left = replace(model, name='left')
        right = replace(model, name='right')
        left.prior_reliability = 0.01
        right.prior_reliability = 0.99
        a = CandidateEvaluation(left, replace(certificate, verification=VerificationMeasures(1, 0.2)))
        b = CandidateEvaluation(right, replace(certificate, verification=VerificationMeasures(1, 0.9)))
        frontier, dominated = pareto_partition((b, a))
        self.assertEqual(tuple(c.model.name for c in frontier), ('left', 'right'))
        self.assertEqual(dominated, ())
        self.assertFalse(hasattr(a, 'verification_score'))

    def test_intent_evidence_is_displayed_without_numeric_truth_claim(self):
        context, model, hypotheses, experiments, evidence = organization_interpretation_scenario()
        r = BidirectionalModelingEngine().interpret(model, context, hypotheses, evidence, experiments)
        self.assertEqual(tuple(c.hypothesis.name for c in r.candidates),
                         tuple(sorted(h.name for h in hypotheses)))
        self.assertTrue(all(not hasattr(c, 'ranking_score') for c in r.candidates))
        intent = next(c for c in r.candidates if c.hypothesis.level is PurposeLevel.INTENTION)
        self.assertTrue(intent.caveats)
        self.assertFalse(intent.direct_intent_evidence)
