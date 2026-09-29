"""Shrinkable operation sequences for evidence and cached partial predictions."""
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from bidirectional_modeling import SearchObservation
from bidirectional_modeling.search_lazy import LazyExecutableSearch
from bidirectional_modeling.search_partial import verify_candidate_exclusion
from test_search_partial import partial_args


class EvidenceLifecycle(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.protocol, self.candidate, self.cases = partial_args()
        self.lazy = self.new_search()
        self.evidence = ()
        self.proofs = []

    def new_search(self):
        return LazyExecutableSearch(self.protocol, (self.candidate,), self.cases,
            target='out', world_answers=('low', 'mixed', 'mixed', 'high'))

    def verify(self, proof):
        return verify_candidate_exclusion(self.protocol, self.candidate,
            self.cases, proof, self.evidence)

    @rule(response=st.sampled_from(('0', '1')),
          source=st.sampled_from(('lab-v1', 'lab-v2')))
    def replace_evidence(self, response, source):
        self.evidence = (SearchObservation('a', response, source),)

    @rule()
    def withdraw_evidence(self):
        self.evidence = ()

    @rule()
    def screen(self):
        result = self.lazy.screen_evidence(self.evidence)
        assert result.simulations_used <= 2
        assert result.undecided == ()
        assert result.excluded == (('zero',) if self.evidence and self.evidence[0].response == '1' else ())
        if result.certificates:
            proof = result.certificates[0]
            assert self.verify(proof).status == 'valid'
            self.proofs.append(proof)
        assert self.lazy.snapshot.hypotheses[0].world is None

    @rule()
    def interrupt_fresh_screen(self):
        # A new search has no successful prediction in its cache.
        if self.evidence:
            result = self.new_search().screen_evidence(self.evidence, max_simulations=1)
            assert result.excluded == ()
            assert result.undecided == ('zero',)

    @invariant()
    def saved_proofs_depend_on_the_exact_live_observation(self):
        for proof in self.proofs[-3:]:
            expected = 'valid' if proof.observation in self.evidence else 'invalid'
            assert self.verify(proof).status == expected


TestEvidenceLifecycle = EvidenceLifecycle.TestCase
TestEvidenceLifecycle.settings = settings(max_examples=25, stateful_step_count=14,
                                         deadline=None)
