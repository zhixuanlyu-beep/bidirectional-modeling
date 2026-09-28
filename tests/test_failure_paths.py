"""Public-path regressions selected from uncovered proof failure branches."""
import unittest
from dataclasses import replace

from bidirectional_modeling import (
    BidirectionalModelingEngine, CheckResult, Context, CustomRequirement,
    ExperimentHypothesisSearch, ProbeOutcome, Realizer, RequirementCategory,
    ResourceBudget, SatisfactionEvaluator, SearchObservation, SearchWorkBudget,
    Trace, VerificationIssue, transport_conflict, verify_transported_conflict,
)
from test_context_network import additive_protocol, identity_transition
from test_interpretation_status import hypothesis, model, spec
from test_regressions import TupleTraceModel, scenario_context


class EvidenceFailureTests(unittest.TestCase):
    def test_malformed_trace_domains_remain_undecided(self):
        valid = Trace('source', 's', 'baseline', ({'x': 0}, {'x': 1}))
        cases = (
            ((object(),), 'not a Trace'),
            ((replace(valid, snapshots=({'x': 0},)),), 'snapshots'),
            ((replace(valid, model_name='other'),), 'names model'),
            ((valid, valid), 'duplicate'),
            ((valid, replace(valid, initial_state='extra')), 'unexpected scenarios'),
            ((replace(valid, snapshots=({'x': object()}, {'x': 1})),), 'could not be fingerprinted'),
        )
        for traces, reason in cases:
            with self.subTest(reason=reason):
                source = TupleTraceModel('source', traces)
                result = Realizer().realize(spec(), scenario_context(('s','baseline')), (source,))
                self.assertFalse(result.candidates or result.rejected)
                self.assertEqual(len(result.undecided), 1)
                certificate = result.undecided[0].certificate
                self.assertFalse(certificate.complete or certificate.satisfied)
                self.assertTrue(any(reason in b for b in certificate.failure_boundaries),
                                certificate.failure_boundaries)

    def test_requirement_mutating_spec_cannot_reuse_the_starting_binding(self):
        target = spec()
        def mutate(m, traces, context):
            target.ambiguous_terms['changed'] = ('new meaning',)
            return CheckResult('mutation', RequirementCategory.OBJECTIVE, True, 1, '1')
        target = replace(target, objectives=(CustomRequirement('mutation', RequirementCategory.OBJECTIVE,
                           mutate, semantic_id='spec-drift-test'),))
        certificate = SatisfactionEvaluator().evaluate(model(), target, Context())
        self.assertFalse(certificate.complete or certificate.satisfied)
        self.assertTrue(any('specification changed' in b for b in certificate.failure_boundaries),
                        certificate.failure_boundaries)

    def test_requirement_mutating_model_invalidates_batch_after_evaluation(self):
        def mutate(m, traces, context):
            m.metrics = replace(m.metrics, cost=m.metrics.cost+1)
            return CheckResult('mutation', RequirementCategory.OBJECTIVE, True, 1, '1')
        target = replace(spec(), objectives=(CustomRequirement('mutation', RequirementCategory.OBJECTIVE,
                           mutate, semantic_id='model-drift-test'),))
        certificate = SatisfactionEvaluator().evaluate(model(), target, Context())
        self.assertFalse(certificate.complete or certificate.satisfied)
        self.assertTrue(any('after evaluation' in b for b in certificate.failure_boundaries))

    def test_batch_replay_rejects_unfingerprintable_context(self):
        m, context, evaluator = model(), Context(), SatisfactionEvaluator()
        batch = evaluator.collect(m, context, 1)
        bad_context = replace(context, environment={'opaque': object()})
        self.assertFalse(batch.binds(m, bad_context, 1))
        certificate = evaluator.evaluate_batch(m, spec(), bad_context, batch, ResourceBudget())
        self.assertFalse(certificate.complete or certificate.satisfied)
        self.assertTrue(any('context' in b for b in certificate.failure_boundaries))

    def test_changed_resource_metrics_require_fresh_evidence(self):
        for key in ('cost', 'complexity', 'risk'):
            with self.subTest(metric=key):
                m, evaluator, context = model(), SatisfactionEvaluator(), Context()
                old = evaluator.collect(m, context, 1)
                m.metrics = replace(m.metrics, **{key: getattr(m.metrics, key)+1})
                self.assertFalse(old.binds(m, context, 1))
                stale = evaluator.evaluate_batch(m, spec(), context, old, ResourceBudget())
                self.assertFalse(stale.complete or stale.satisfied)
                self.assertTrue(evaluator.evaluate(m, spec(), context).satisfied)

    def test_trace_batch_metadata_cannot_claim_more_work_than_was_allowed(self):
        batch = SatisfactionEvaluator().collect(model(), Context(), 1)
        for changes in ({'traces': list(batch.traces)}, {'horizon': True}, {'horizon': 0},
                        {'simulation_limit': True}, {'simulation_limit': -1}, {'simulation_limit': 0},
                        {'coverage': float('nan')}, {'coverage': .5}, {'coverage_authority': ''}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                replace(batch, **changes)


class BudgetAndUnknownTests(unittest.TestCase):
    def test_micro_round_trip_budget_exhausted_between_phases(self):
        r = BidirectionalModelingEngine().micro_round_trip(model(), Context(),
            (hypothesis('only'),), (model('copy'),), budget=ResourceBudget(max_simulations=1))
        self.assertFalse(r.passed)
        self.assertTrue(r.truncated)
        self.assertEqual(r.realization.searched_candidates, 0)
        self.assertEqual(r.simulations_used, 1)

    def test_explicit_identity_reuses_evidence_without_an_extra_simulation(self):
        original = model()
        r = BidirectionalModelingEngine().micro_round_trip(original, Context(),
            (hypothesis('only'),), (original,), allow_identity=True,
            budget=ResourceBudget(max_simulations=3))
        self.assertTrue(r.passed)
        self.assertEqual(r.simulations_used, 3)
        self.assertEqual(r.behaviorally_equivalent_models, ('original',))

    def test_probe_diagnostic_without_certificate_cannot_admit_candidate(self):
        class UnknownProbe:
            def probe(self, *args):
                return ProbeOutcome(None, diagnostics=(VerificationIssue('external', 'unavailable'),))
        r = Realizer(probes=(UnknownProbe(),)).realize(spec(), Context(), (model(),))
        self.assertFalse(r.candidates or r.rejected)
        self.assertEqual(r.undecided[0].diagnostics[0].reason, 'unavailable')
        self.assertEqual(r.simulations_used, 1)

    def test_no_compatible_hypothesis_cannot_seed_round_trip(self):
        with self.assertRaisesRegex(ValueError, 'no compatible'):
            BidirectionalModelingEngine().micro_round_trip(model(), Context(), (), (model('copy'),))


class TransportFailureTests(unittest.TestCase):
    def setUp(self):
        p, q = additive_protocol(), additive_protocol(scope='new')
        self.old = ExperimentHypothesisSearch(p, (), 'interaction')
        self.new = ExperimentHypothesisSearch(q, (), 'interaction')
        self.data = tuple(SearchObservation(e, r, 'old') for e, r in
                          (('10', '0'), ('01', '0'), ('11', '1')))
        self.target = tuple(replace(o, source='new') for o in self.data)
        self.certificate = self.old.learn_conflict(('additive',), self.data)
        self.transition = identity_transition(p, q)
        self.links = tuple(zip(self.data, self.target))

    def transport(self, budget=None, evidence=None, links=None):
        return transport_conflict(self.old, self.new, self.transition, self.certificate,
            self.data, self.target if evidence is None else evidence,
            self.links if links is None else links, budget=budget)

    def test_budget_cut_at_each_operation_preserves_unknown_and_eventual_verification(self):
        counter = SearchWorkBudget()
        verified = self.transport(counter)
        self.assertEqual(verified.status, 'verified')
        # Cross the preparation/proof boundary, rather than testing only a zero budget.
        for limit in range(counter.work.total):
            with self.subTest(limit=limit):
                r = self.transport(SearchWorkBudget(limit))
                self.assertEqual(r.status, 'undecided')
                self.assertIsNone(r.certificate)
        self.assertEqual(self.transport(SearchWorkBudget(counter.work.total)).status, 'verified')
        self.assertEqual(verify_transported_conflict(verified, self.old, self.new,
            self.transition, self.certificate, self.data, self.target,
            budget=SearchWorkBudget(0)), 'undecided')

    def test_malformed_links_and_out_of_domain_target_cannot_carry_proof(self):
        bad = self.transport(links=((self.data[0],),))
        self.assertEqual(bad.reason, 'malformed_evidence_links')
        evidence = (replace(self.target[0], response='outside'),) + self.target[1:]
        bad = self.transport(evidence=evidence)
        self.assertEqual(bad.status, 'not_applicable')
        self.assertIsNone(bad.certificate)
