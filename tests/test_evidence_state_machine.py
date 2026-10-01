"""All transitions of a declared finite evidence/cache/proof state graph."""
from collections import deque
import unittest

from bidirectional_modeling import SearchObservation
from bidirectional_modeling.search_lazy import LazyExecutableSearch
from bidirectional_modeling.search_partial import verify_candidate_exclusion
from test_search_partial import partial_args


OBSERVATIONS = tuple(SearchObservation('a', response, source)
                     for response in ('0', '1') for source in ('lab-v1', 'lab-v2'))
ACTIONS = (0, 1, 2, 3, 'withdraw', 'screen', 'interrupt')
INITIAL = (-1, False, 0)  # live observation index, cached 'a' prediction, proof mask


def abstract_step(state, action):
    live, cached, proofs = state
    if isinstance(action, int): live = action
    elif action == 'withdraw': live = -1
    elif action == 'screen' and live >= 0:
        cached = True
        if live >= 2: proofs |= 1 << (live - 2)
    return live, cached, proofs


def reachable_paths():
    paths = {INITIAL: ()}
    pending = deque((INITIAL,))
    while pending:
        state = pending.popleft()
        for action in ACTIONS:
            next_state = abstract_step(state, action)
            if next_state not in paths:
                paths[next_state] = paths[state] + (action,)
                pending.append(next_state)
    return paths


class TestEvidenceLifecycle(unittest.TestCase):
    def replay(self, actions):
        protocol, candidate, cases = partial_args()
        def new_search():
            return LazyExecutableSearch(protocol, (candidate,), cases,
                target='out', world_answers=('low', 'mixed', 'mixed', 'high'))
        lazy = new_search()
        evidence, proofs, state = (), {}, INITIAL
        for action in actions:
            with self.subTest(prefix=actions, action=action):
                if isinstance(action, int): evidence = (OBSERVATIONS[action],)
                elif action == 'withdraw': evidence = ()
                elif action == 'screen':
                    report = lazy.screen_evidence(evidence)
                    self.assertEqual(report.simulations_used, 2 if evidence and not state[1] else 0)
                    self.assertEqual(report.undecided, ())
                    self.assertEqual(report.excluded, ('zero',) if state[0] >= 2 else ())
                    for proof in report.certificates: proofs[proof.observation] = proof
                elif evidence:
                    report = new_search().screen_evidence(evidence, max_simulations=1)
                    self.assertEqual(report.simulations_used, 1)
                    self.assertEqual(report.excluded, ())
                    self.assertEqual(report.undecided, ('zero',))
                state = abstract_step(state, action)
                self.assertIsNone(lazy.snapshot.hypotheses[0].world)
                expected_proofs = {OBSERVATIONS[2+i] for i in range(2) if state[2] & (1 << i)}
                self.assertEqual(set(proofs), expected_proofs)
                for observation, proof in proofs.items():
                    verified = verify_candidate_exclusion(protocol, candidate, cases, proof, evidence)
                    self.assertEqual(verified.status, 'valid' if observation in evidence else 'invalid')
        return state

    def test_every_reachable_transition_against_independent_state_update(self):
        paths = reachable_paths()
        self.assertEqual(len(paths), 25)
        edges = 0
        for state, path in paths.items():
            for action in ACTIONS:
                self.assertEqual(self.replay(path + (action,)), abstract_step(state, action))
                edges += 1
        self.assertEqual(edges, 175)

    def test_long_repeated_withdrawal_and_source_replacement(self):
        actions = (2, 'screen', 'withdraw', 'screen', 3, 'interrupt', 'screen',
                   0, 'screen', 2, 'screen', 'withdraw', 3, 'screen')
        self.assertEqual(self.replay(actions), (3, True, 3))
