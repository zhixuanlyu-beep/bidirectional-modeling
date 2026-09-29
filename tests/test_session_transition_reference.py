"""A finite transition model for live evidence and transactional migration.

Each short operation sequence is checked against an independent abstract
state update. This is explicit finite-state testing, not a TLC invocation.
"""
from dataclasses import dataclass, replace
from itertools import product
import unittest

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search import SearchBudgetExceeded
from bidirectional_modeling.search_session import SearchSession
from test_context_network import identity_transition


@dataclass(frozen=True)
class AbstractSession:
    live: str = ''
    proved: str = ''


def abstract_step(state, action):
    if action in ('v1', 'v2'):
        return AbstractSession(action, state.proved if state.proved == action else '')
    if action == 'withdraw':
        return AbstractSession()
    if action == 'discover':
        return AbstractSession(state.live, state.live)
    return state  # migration and interrupted operations do not change source


class SessionTransitionReference(unittest.TestCase):
    def setUp(self):
        protocol = SearchProtocol('source', 'code',
            (SearchExperiment('a', 'read'),), (('0',), ('1',)),
            (ResponseConstraint('C', (0,)),))
        hypotheses = (
            SearchHypothesis('zero', 0, 'low', DescriptionLength(), ('C',)),
            SearchHypothesis('one', 1, 'high', DescriptionLength()),
        )
        self.source = ExperimentHypothesisSearch(protocol, hypotheses, 'out')
        self.target = ExperimentHypothesisSearch(replace(protocol, scope='target'),
                                                 hypotheses, 'out')
        self.transition = identity_transition(protocol, self.target.protocol)

    def evidence(self, label):
        return (SearchObservation('a', '1', label),) if label else ()

    def assert_matches(self, session, state):
        self.assertEqual(session.evidence, self.evidence(state.live))
        self.assertEqual(tuple(cert.evidence for cert in session.certificates),
            ((self.evidence(state.proved),) if state.proved else ()))
        for cert in session.certificates:
            self.assertTrue(session.search.validates_conflict(cert, session.evidence))

    def test_all_short_histories_preserve_claim_boundaries(self):
        actions = ('v1', 'v2', 'withdraw', 'discover', 'migrate', 'interrupt')
        visited = 0
        for length in range(4):
            for history in product(actions, repeat=length):
                session = SearchSession(self.source)
                state = AbstractSession()
                for action in history:
                    before = session.to_json()
                    if action in ('v1', 'v2'):
                        session.replace_evidence(self.evidence(action))
                    elif action == 'withdraw':
                        session.replace_evidence(())
                    elif action == 'discover':
                        session.run()
                    elif action == 'interrupt':
                        if state.live:
                            with self.assertRaises(SearchBudgetExceeded):
                                session.replace_evidence(self.evidence('v1'),
                                    budget=SearchWorkBudget(0))
                            self.assertEqual(session.to_json(), before)
                        result = session.migrate_context(self.target, self.transition,
                            (), (), budget=SearchWorkBudget(0))
                        self.assertEqual((result.status, result.session), ('undecided', None))
                        self.assertEqual(session.to_json(), before)
                    else:
                        target_evidence = self.evidence('target') if state.live else ()
                        links = tuple(zip(session.evidence, target_evidence))
                        result = session.migrate_context(self.target, self.transition,
                            target_evidence, links)
                        self.assertEqual(result.status, 'completed')
                        self.assertEqual(result.session.evidence, target_evidence)
                        self.assertEqual(bool(result.session.certificates), bool(state.proved))
                        self.assertEqual(session.to_json(), before)
                    state = abstract_step(state, action)
                    self.assert_matches(session, state)
                    # Persistence is part of the observable session behavior.
                    session = SearchSession.from_json(session.to_json())
                    self.assert_matches(session, state)
                visited += 1
        self.assertEqual(visited, sum(len(actions) ** n for n in range(4)))

    def test_two_proofs_after_migration_withdrawal_and_second_migration(self):
        protocol = replace(self.source.protocol, constraints=(
            ResponseConstraint('C', (0,)), ResponseConstraint('D', (0,))))
        candidates = tuple(SearchHypothesis(name, 0, 'low', DescriptionLength(), (name,))
                           for name in ('C', 'D'))
        source = ExperimentHypothesisSearch(protocol, candidates, 'out')
        middle = ExperimentHypothesisSearch(replace(protocol, scope='middle'), candidates, 'out')
        final = ExperimentHypothesisSearch(replace(protocol, scope='final'), candidates, 'out')
        original = self.evidence('v1')
        session = SearchSession(source, original,
            tuple(source.learn_conflict((name,), original) for name in ('C', 'D')))
        middle_evidence = self.evidence('middle-v1')
        first = session.migrate_context(middle, identity_transition(protocol, middle.protocol),
            middle_evidence, tuple(zip(original, middle_evidence)))
        self.assertEqual((first.status, len(first.session.certificates)), ('completed', 2))
        for action in ('withdraw', 'replace', 'replace_then_discover'):
            with self.subTest(action=action):
                current = SearchSession.from_json(first.session.to_json())
                if action == 'withdraw':
                    current.replace_evidence(())
                    expected_evidence, expected_proofs = (), 0
                else:
                    current.replace_evidence(self.evidence('middle-v2'))
                    if action == 'replace_then_discover':
                        current.run()
                    expected_evidence = self.evidence('middle-v2')
                    expected_proofs = 2 if action == 'replace_then_discover' else 0
                current = SearchSession.from_json(current.to_json())
                self.assertEqual(len(current.certificates), expected_proofs)
                target_evidence = self.evidence('final') if expected_evidence else ()
                transition = identity_transition(middle.protocol, final.protocol)
                links = tuple(zip(expected_evidence, target_evidence))
                before = current.to_json()
                work = SearchWorkBudget()
                result = current.migrate_context(final, transition, target_evidence,
                    links, budget=work)
                self.assertEqual((result.status, len(result.session.certificates)),
                                 ('completed', expected_proofs))
                for limit in range(work.work.total):
                    pending = current.migrate_context(final, transition,
                        target_evidence, links, budget=SearchWorkBudget(limit))
                    self.assertEqual((pending.status, pending.session), ('undecided', None))
                    self.assertEqual(current.to_json(), before)
                restored = SearchSession.from_json(result.session.to_json())
                self.assertEqual(restored.evidence, target_evidence)
                self.assertEqual(len(restored.certificates), expected_proofs)
