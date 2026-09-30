"""Invalid callback results reserve allowance; generators cannot certify themselves."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from bidirectional_modeling import Context, Interpreter, ObservedEffectGenerator, Realizer, ResourceBudget, SearchWorkBudget
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter
from bidirectional_modeling.search_partial import collect_partial_prediction, verify_partial_prediction, verify_candidate_exclusion
from bidirectional_modeling.certificate_transport import transport_conflict, verify_transported_conflict
from bidirectional_modeling.residual import ResidualQuotientAnalyzer, verify_distinguishing_context
from bidirectional_modeling.extensions.boolean import BooleanExpression, BooleanLanguage, find_boolean_substitute, verify_boolean_substitute
from bidirectional_modeling.extensions.implications import check_implication, Implication, explore_implications, verify_implication_assessment
from bidirectional_modeling.search_repairs import verify_consistency_repairs
from test_interpretation_status import model, spec, hypothesis
from test_context_network import identity_transition
from bidirectional_modeling import ExperimentHypothesisSearch, ResponseConstraint
from bidirectional_modeling.search import ConflictCertificate, SearchObservation
from bidirectional_modeling.structural import fingerprint_value
from bidirectional_modeling.certificate_transport import TransportedConflict
from dataclasses import asdict
from test_search_partial import partial_args
import test_evidence_lifecycle as migration_fixtures
from test_implication_exploration import context
from bidirectional_modeling.examples import residual_quotient_scenario
from bidirectional_modeling.search_examples import conflict_search_scenario


class ExecutionOutputBoundaries(unittest.TestCase):
    def test_malformed_batches_reserve_execution_allowance_across_entries(self):
        class Broken(SatisfactionEvaluator):
            calls = 0
            def collect(self, *args):
                self.calls += 1
                return None
        for source in (ObservedEffectGenerator(), (hypothesis('one'), hypothesis('two'))):
            evaluator = Broken()
            result = Interpreter(evaluator).interpret(model(), Context(), source,
                budget=ResourceBudget(max_simulations=7))
            self.assertEqual(result.identification_status, 'undecided')
            self.assertEqual(result.simulations_used, 7)
            self.assertEqual(evaluator.calls, 1)
            self.assertTrue(result.truncated)
        p, candidate, cases = partial_args()
        evaluator = Broken()
        prepared = ExecutableSearchAdapter(evaluator).prepare(p, (candidate, replace(candidate,
            model=replace(candidate.model, name='other'))), cases,
            target='out', world_answers=('low', 'mixed', 'mixed', 'high'), max_simulations=7)
        self.assertEqual(prepared.simulations_used, 7)
        self.assertEqual(evaluator.calls, 1)
        self.assertTrue(all(h.world is None for h in prepared.search.hypotheses))
        prediction = collect_partial_prediction(p, candidate, cases, ('a',)).prediction
        checked = verify_partial_prediction(p, candidate, cases, prediction,
                                             max_simulations=7, evaluator=Broken())
        self.assertEqual((checked.status, checked.simulations_used), ('undecided', 7))

    def test_malformed_evaluation_and_probe_returns_do_not_approve_or_crash(self):
        class BrokenEvaluation(SatisfactionEvaluator):
            def evaluate(self, *args): return None
            def evaluate_batch(self, *args): return None
            def failure_certificate(self, *args): raise AssertionError('must use default failure handling')
        evaluator = BrokenEvaluation()
        result = Realizer(evaluator).realize(spec(), Context(), (model(),),
            ResourceBudget(max_simulations=7))
        self.assertEqual(result.simulations_used, 7)
        self.assertFalse(result.candidates or result.rejected)
        self.assertTrue(result.undecided)
        interpreted = Interpreter(evaluator).interpret(model(), Context(), (hypothesis('one'),),
            budget=ResourceBudget(max_simulations=7))
        self.assertEqual(interpreted.simulations_used, 7)
        self.assertEqual(interpreted.identification_status, 'undecided')
        class BadProbe:
            blocking = True
            def probe(self, *args): return None
        probed = Realizer(probes=(BadProbe(),)).realize(spec(), Context(), (model(),),
            ResourceBudget(max_simulations=7))
        self.assertEqual(probed.simulations_used, 7)
        self.assertFalse(probed.candidates or probed.rejected)
        self.assertTrue(probed.undecided)

    def test_overreported_consumption_is_not_allowed_to_make_budget_negative(self):
        class Overspend(SatisfactionEvaluator):
            def evaluate(self, *args):
                return replace(super().evaluate(*args), charged_simulations=100)
        result = Realizer(Overspend()).realize(spec(), Context(), (model(), model('second')),
            ResourceBudget(max_simulations=7))
        self.assertEqual(result.simulations_used, 7)
        self.assertEqual(result.searched_candidates, 1)
        self.assertFalse(result.candidates)
        for horizon in (True, 1.5, '1', 0):
            with self.assertRaises(ValueError): ObservedEffectGenerator(horizon)
            class Generator:
                def generate_from_traces(self, traces, complete): return ()
            generator = Generator()
            generator.horizon = horizon
            with self.assertRaises(ValueError):
                Interpreter().interpret(model(), Context(), generator)


class IndependentTransportBoundaries(unittest.TestCase):
    def test_generator_defect_cannot_validate_a_forged_target_proof(self):
        source, target, transition, evidence, links = migration_fixtures.MigrationLifecycleTests().fixture()
        certificate = source.certificates[0]
        receipt = transport_conflict(source.search, target, transition, certificate,
            source.evidence, evidence, links)
        forged = replace(receipt, certificate=replace(receipt.certificate, commitments=()))
        with patch('bidirectional_modeling.certificate_transport.transport_conflict', return_value=forged):
            self.assertEqual(verify_transported_conflict(forged, source.search, target, transition,
                certificate, source.evidence, evidence), 'invalid')
            self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
                certificate, source.evidence, evidence), 'valid')
        budget = SearchWorkBudget()
        self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
            certificate, source.evidence, evidence, budget=budget), 'valid')
        for limit in range(budget.work.total):
            self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
                certificate, source.evidence, evidence, budget=SearchWorkBudget(limit)), 'undecided')
        for changed in (replace(receipt, source_fingerprint='wrong'),
                        replace(receipt, transition_fingerprint='wrong'),
                        replace(receipt, evidence_links=()),
                        replace(receipt, certificate=replace(receipt.certificate, evidence=())),
                        replace(receipt, certificate=replace(receipt.certificate, protocol_fingerprint='changed'))):
            self.assertEqual(verify_transported_conflict(changed, source.search, target, transition,
                certificate, source.evidence, evidence), 'invalid')
        self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
            certificate, (), evidence), 'invalid')
        self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
            certificate, source.evidence, (SearchObservation('outside', '1', 'lab'),)), 'invalid')
        unmapped = replace(transition, commitments=())
        self.assertEqual(verify_transported_conflict(replace(receipt,
            transition_fingerprint=unmapped.fingerprint), source.search, target, unmapped,
            certificate, source.evidence, evidence), 'invalid')
        expanded = ExperimentHypothesisSearch(replace(target.protocol, constraints=tuple(
            ResponseConstraint(c.name, (0, 1)) for c in target.protocol.constraints)), (), 'out')
        changed_transition = identity_transition(source.search.protocol, expanded.protocol)
        changed_receipt = replace(receipt, transition_fingerprint=changed_transition.fingerprint,
            certificate=replace(receipt.certificate, protocol_fingerprint=expanded.protocol.fingerprint))
        self.assertEqual(verify_transported_conflict(changed_receipt, source.search, expanded,
            changed_transition, certificate, source.evidence, evidence), 'invalid')

    def test_target_self_inconsistency_is_not_an_empirical_conflict(self):
        source, target, _, evidence, links = migration_fixtures.MigrationLifecycleTests().fixture()
        protocol = replace(target.protocol, constraints=tuple(
            ResponseConstraint(c.name, ()) for c in target.protocol.constraints))
        target = ExperimentHypothesisSearch(protocol, (), 'out')
        transition = identity_transition(source.search.protocol, protocol)
        certificate = source.certificates[0]
        forged = TransportedConflict('verified', 'target_conflict_reproved',
            fingerprint_value(asdict(certificate)), transition.fingerprint,
            ConflictCertificate(protocol.fingerprint, certificate.commitments, evidence), links)
        self.assertEqual(verify_transported_conflict(forged, source.search, target, transition,
            certificate, source.evidence, evidence), 'invalid')


class RemainingVerifierBoundaries(unittest.TestCase):
    def test_missing_proofs_are_invalid_before_any_replay(self):
        search, evidence = conflict_search_scenario()
        self.assertFalse(search.validates_conflict(None, evidence, budget=SearchWorkBudget(0)))
        self.assertEqual(search.verify_macro(None, evidence, budget=SearchWorkBudget(0)).sufficiency, 'invalid')
        self.assertEqual(verify_consistency_repairs(search, None, evidence, budget=SearchWorkBudget(0)), 'invalid')
        p, candidate, cases = partial_args()
        self.assertEqual(verify_partial_prediction(p, candidate, cases, None,
            max_simulations=0, budget=SearchWorkBudget(0)).status, 'invalid')
        self.assertEqual(verify_candidate_exclusion(p, candidate, cases, None, evidence,
            max_simulations=0, budget=SearchWorkBudget(0)).status, 'invalid')
        target, language, inputs = BooleanExpression(('var', 'x')), BooleanLanguage(('x',)), ({'x': True},)
        self.assertEqual(verify_boolean_substitute(target, language, inputs, None,
            budget=SearchWorkBudget(0)), 'invalid')
        equivalence, ctx, candidate_model = residual_quotient_scenario()
        report = ResidualQuotientAnalyzer().analyze(candidate_model, equivalence, ctx)
        self.assertEqual(verify_distinguishing_context(report, None, candidate_model,
            equivalence, ctx, max_operations=0).status, 'invalid')
        witness = report.distinguishing_contexts[0]
        states = list(report.quotient.states)
        states[witness.left_state] = replace(states[witness.left_state], micro_state=None)
        malformed = replace(report, quotient=replace(report.quotient, states=tuple(states)))
        self.assertEqual(verify_distinguishing_context(malformed, witness, candidate_model,
            equivalence, ctx, max_operations=0).reason, 'malformed_source_state')
        source, target, transition, evidence, links = migration_fixtures.MigrationLifecycleTests().fixture()
        self.assertEqual(verify_transported_conflict(None, source.search, target, transition,
            source.certificates[0], source.evidence, evidence, budget=SearchWorkBudget(0)), 'invalid')
        receipt = transport_conflict(source.search, target, transition, source.certificates[0],
            source.evidence, evidence, links)
        forged = replace(receipt, certificate=replace(receipt.certificate, protocol_fingerprint='wrong'))
        self.assertEqual(verify_transported_conflict(forged, source.search, target, transition,
            source.certificates[0], source.evidence, evidence, budget=SearchWorkBudget(0)), 'invalid')

    def test_pending_implication_is_not_mislabeled_invalid_and_limits_are_checked(self):
        ctx = context()
        pending = check_implication(ctx, Implication(('a',), 'b'), max_object_checks=1)
        self.assertEqual(verify_implication_assessment(ctx, pending), 'undecided')
        self.assertEqual(verify_implication_assessment(ctx,
            replace(pending, status='invented'), max_object_checks=0), 'invalid')
        with self.assertRaises(ValueError):
            explore_implications(ctx, max_candidates=0, max_object_checks=-1)

        with self.assertRaises(TypeError):
            explore_implications(None, max_candidates=0)
