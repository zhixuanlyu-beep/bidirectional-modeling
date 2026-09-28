"""Cross-module experiments: live evidence, local scope and interrupted proofs."""
from dataclasses import replace
import unittest

from bidirectional_modeling import (Context, DescriptionLength, EquivalenceSpec, Experiment,
    ExperimentHypothesisSearch, FieldRequirement, InterpretationObservation, Interpreter,
    MacroSpec, PurposeHypothesis, PurposeLevel, ResponseConstraint, SearchExperiment,
    SearchHypothesis, SearchObservation, SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.certificate_transport import verify_transported_conflict
from bidirectional_modeling.search_lazy import LazyExecutableSearch
from bidirectional_modeling.search_partial import verify_candidate_exclusion
from bidirectional_modeling.search_session import SearchSession
from test_context_network import additive_protocol, identity_transition
from test_search_partial import partial_args


class ScreeningLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.protocol, self.candidate, self.cases = partial_args()
        self.lazy = LazyExecutableSearch(self.protocol, (self.candidate,), self.cases,
            target='out', world_answers=('low', 'mixed', 'mixed', 'high'))
        self.evidence = (SearchObservation('a', '1', 'lab-v1'),)

    def verify(self, certificate, evidence, **kwargs):
        return verify_candidate_exclusion(self.protocol, self.candidate, self.cases,
                                           certificate, evidence, **kwargs)

    def test_interpretation_and_screening_agree_without_conflating_receipts(self):
        goal = MacroSpec('zero response', ('y',), (FieldRequirement('zero', 'y', 'eq', '0'),),
                         EquivalenceSpec(('y',)))
        hypothesis = PurposeHypothesis('zero', PurposeLevel.EFFECT, goal,
                                        allowed_outcomes={'a': ('0',)})
        experiment = Experiment('a', 'read y', ('0', '1'))
        for response, excluded in (('0', False), ('1', True)):
            observation = InterpretationObservation('a', response, 'lab-v1')
            interpreted = Interpreter().interpret(self.candidate.model, self.cases[0].context,
                (hypothesis,), experiments=(experiment,), observations=(observation,))
            evidence = (SearchObservation('a', response, 'lab-v1'),)
            screened = self.lazy.screen_evidence(evidence)
            self.assertEqual(bool(interpreted.excluded), excluded)
            self.assertEqual(bool(screened.excluded), excluded)
            self.assertIsNone(self.lazy.snapshot.hypotheses[0].world)
            if excluded:
                record, proof = interpreted.excluded[0], screened.certificates[0]
                self.assertEqual(record.allowed_outcomes, proof.prediction.responses)
                self.assertEqual(record.observation.source, proof.observation.source)
                self.assertEqual(self.verify(proof, evidence).status, 'valid')
            else:
                self.assertEqual(interpreted.identification_status, 'unique')
                self.assertEqual(screened.matching_evidence, ('zero',))

    def test_cached_prediction_cannot_preserve_a_withdrawn_observation(self):
        first = self.lazy.screen_evidence(self.evidence)
        proof = first.certificates[0]
        withdrawn = self.lazy.screen_evidence(())
        self.assertFalse(withdrawn.excluded or withdrawn.certificates)
        self.assertEqual(self.verify(proof, ()).reason, 'evidence_dependency_missing')
        rebound = tuple(replace(o, source='lab-v2') for o in self.evidence)
        second = self.lazy.screen_evidence(rebound)
        self.assertEqual(second.simulations_used, 0)  # Prediction reuse, not evidence reuse.
        self.assertEqual(second.excluded, ('zero',))
        self.assertEqual(self.verify(proof, rebound).status, 'invalid')
        self.assertEqual(self.verify(second.certificates[0], rebound).status, 'valid')
        self.assertIsNone(self.lazy.snapshot.hypotheses[0].world)

    def test_budget_and_cancellation_stay_undecided_until_independent_replay(self):
        initial = self.lazy.screen_evidence(self.evidence, max_simulations=1)
        self.assertFalse(initial.certificates or initial.excluded)
        self.assertEqual(initial.undecided, ('zero',))
        completed = self.lazy.screen_evidence(self.evidence)
        proof = completed.certificates[0]
        for kwargs in ({'max_simulations': 1}, {'budget': SearchWorkBudget(0)},
                       {'budget': SearchWorkBudget(cancelled=lambda: True)}):
            self.assertEqual(self.verify(proof, self.evidence, **kwargs).status, 'undecided')
        self.assertEqual(self.verify(proof, self.evidence, max_simulations=2).status, 'valid')
        self.assertIsNone(self.lazy.snapshot.hypotheses[0].world)

    def test_replay_binds_even_the_unexecuted_case_context(self):
        proof = self.lazy.screen_evidence(self.evidence).certificates[0]
        changed = (self.cases[0], replace(self.cases[1], context=Context(observer='new observer')))
        result = verify_candidate_exclusion(self.protocol, self.candidate, changed, proof, self.evidence)
        self.assertEqual((result.status, result.reason, result.simulations_used),
                         ('invalid', 'binding_mismatch', 0))
        other = replace(self.candidate, model=replace(self.candidate.model, name='descendant'))
        self.assertEqual(verify_candidate_exclusion(self.protocol, other, self.cases,
                                                    proof, self.evidence).status, 'invalid')


class MigrationLifecycleTests(unittest.TestCase):
    def fixture(self):
        protocol = SearchProtocol('original', 'code', (SearchExperiment('a', 'read'),),
            (('0',), ('1',)), (ResponseConstraint('C', (0,)), ResponseConstraint('D', (0,))))
        hypotheses = (SearchHypothesis('zero', 0, 'low', DescriptionLength(), ('C', 'D')),
                      SearchHypothesis('one', 1, 'high', DescriptionLength()))
        source = ExperimentHypothesisSearch(protocol, hypotheses, 'out')
        target = ExperimentHypothesisSearch(replace(protocol, scope='recalibrated'), hypotheses, 'out')
        evidence = (SearchObservation('a', '1', 'old lab'),)
        new_evidence = (replace(evidence[0], source='new lab'),)
        certificates = tuple(source.learn_conflict((name,), evidence) for name in ('C', 'D'))
        session = SearchSession(source, evidence, certificates)
        return session, target, identity_transition(protocol, target.protocol), new_evidence, tuple(zip(evidence, new_evidence))

    def test_migration_serialization_verification_and_retraction_preserve_scope(self):
        source, target, transition, evidence, links = self.fixture()
        original = source.to_json()
        migrated = source.migrate_context(target, transition, evidence, links)
        self.assertEqual(migrated.status, 'completed')
        self.assertEqual(source.to_json(), original)
        restored = SearchSession.from_json(migrated.session.to_json())
        self.assertEqual(restored.run().pruned, ('zero',))
        receipt = migrated.transports[0]
        self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
            source.certificates[0], source.evidence, evidence), 'valid')
        self.assertFalse(target.validates_conflict(source.certificates[0], evidence))
        restored.replace_evidence(())
        self.assertFalse(restored.certificates)
        after = restored.run()
        self.assertFalse(after.pruned)
        self.assertEqual(set(after.compatible), {'zero', 'one'})
        self.assertEqual(verify_transported_conflict(receipt, source.search, target, transition,
            source.certificates[0], source.evidence, ()), 'invalid')

    def test_every_work_budget_boundary_is_transactional(self):
        source, target, transition, evidence, links = self.fixture()
        baseline = source.to_json()
        budget = SearchWorkBudget()
        success = source.migrate_context(target, transition, evidence, links, budget=budget)
        self.assertEqual(success.status, 'completed')
        partial_proof_seen = False
        for limit in range(budget.work.total):
            result = source.migrate_context(target, transition, evidence, links,
                                             budget=SearchWorkBudget(limit))
            self.assertEqual(result.status, 'undecided', limit)
            self.assertIsNone(result.session, limit)
            partial_proof_seen |= any(r.status == 'verified' for r in result.transports)
            self.assertEqual(source.to_json(), baseline)
        self.assertTrue(partial_proof_seen)
        result = source.migrate_context(target, transition, evidence, links,
                                         budget=SearchWorkBudget(budget.work.total))
        self.assertEqual(result.status, 'completed')
        cancelled = source.migrate_context(target, transition, evidence, links,
                                             budget=SearchWorkBudget(cancelled=lambda: True))
        self.assertEqual(cancelled.status, 'undecided')
        self.assertIsNone(cancelled.session)
        self.assertEqual(source.to_json(), baseline)

    def test_expanded_response_domain_drops_inapplicable_conflict_after_restore(self):
        p, q = additive_protocol(), additive_protocol(('-1','0','1'), 'expanded')
        old, new = ExperimentHypothesisSearch(p, (), 'out'), ExperimentHypothesisSearch(q, (), 'out')
        evidence = tuple(SearchObservation(e, r, 'lab') for e,r in (('10','0'),('01','0'),('11','1')))
        certificate = old.learn_conflict(('additive',), evidence)
        source = SearchSession(old, evidence, (certificate,))
        result = source.migrate_context(new, identity_transition(p,q), evidence, tuple(zip(evidence,evidence)))
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.transports[0].status, 'not_applicable')
        self.assertFalse(result.session.certificates)
        restored = SearchSession.from_json(result.session.to_json())
        self.assertFalse(restored.certificates)
        self.assertIsNone(restored.search.learn_conflict(('additive',), evidence))
        self.assertEqual(source.certificates, (certificate,))
