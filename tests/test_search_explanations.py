"""A proof path depends on active evidence, inherited rules and target replay."""
from dataclasses import replace
import unittest

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.certificate_transport import transport_conflict
from bidirectional_modeling.search_explanations import (
    explain_conflict, explain_transported_conflict, verify_conflict_explanation,
    verify_transport_explanation,
)
from test_context_network import identity_transition


class ExplanationContracts(unittest.TestCase):
    def setUp(self):
        protocol = SearchProtocol('original', 'code',
            (SearchExperiment('a', 'read'),), (('0',), ('1',)),
            (ResponseConstraint('C', (0,)),))
        hypotheses = (
            SearchHypothesis('inherits', 0, 'low', DescriptionLength(), ('C',)),
            SearchHypothesis('unconstrained', 1, 'high', DescriptionLength()),
        )
        self.source = ExperimentHypothesisSearch(protocol, hypotheses, 'out')
        self.evidence = (SearchObservation('a', '1', 'lab-v1'),)
        self.certificate = self.source.learn_conflict(('C',), self.evidence)
        self.target = ExperimentHypothesisSearch(replace(protocol, scope='target'),
                                                 hypotheses, 'out')
        self.transition = identity_transition(protocol, self.target.protocol)
        self.target_evidence = (replace(self.evidence[0], source='lab-v2'),)
        self.receipt = transport_conflict(self.source, self.target, self.transition,
            self.certificate, self.evidence, self.target_evidence,
            tuple(zip(self.evidence, self.target_evidence)))

    def test_conflict_path_has_only_necessary_declared_dependencies(self):
        other = SearchObservation('a', '1', 'another source')
        explanation = explain_conflict(self.source, self.certificate,
                                       self.evidence + (other,), candidate='inherits')
        self.assertEqual((explanation.status, explanation.root), ('valid', 'exclusion'))
        self.assertEqual(tuple(n.kind for n in explanation.nodes),
            ('protocol', 'commitment', 'observation', 'empirical_conflict', 'candidate_excluded'))
        self.assertEqual(explanation.nodes[2].subject, self.evidence[0])
        self.assertEqual(verify_conflict_explanation(explanation, self.source,
            self.certificate, self.evidence), 'invalid')  # candidate scope matters
        self.assertEqual(verify_conflict_explanation(explanation, self.source,
            self.certificate, self.evidence, candidate='inherits'), 'valid')
        self.assertEqual(verify_conflict_explanation(explanation, self.source,
            self.certificate, (other,), candidate='inherits'), 'invalid')
        self.assertEqual(explain_conflict(self.source, self.certificate,
            self.evidence, candidate='unconstrained').status, 'invalid')
        self.assertEqual(verify_conflict_explanation(explanation, self.source,
            self.certificate, self.evidence, candidate='inherits',
            budget=SearchWorkBudget(0)), 'undecided')

    def test_transport_path_requires_a_fresh_target_proof(self):
        explanation = explain_transported_conflict(self.receipt, self.source, self.target,
            self.transition, self.certificate, self.evidence, self.target_evidence)
        self.assertEqual((explanation.status, explanation.root), ('valid', 'transport'))
        self.assertEqual({n.kind for n in explanation.nodes if n.key in
            explanation.nodes[-1].depends_on},
            {'context_transition', 'empirical_conflict',
             'evidence_translation', 'commitment_translation'})
        self.assertEqual(verify_transport_explanation(explanation, self.receipt,
            self.source, self.target, self.transition, self.certificate,
            self.evidence, self.target_evidence), 'valid')
        self.assertEqual(verify_transport_explanation(explanation, self.receipt,
            self.source, self.target, self.transition, self.certificate,
            self.evidence, ()), 'invalid')
        self.assertEqual(verify_transport_explanation(explanation, self.receipt,
            self.source, self.target, self.transition, self.certificate,
            self.evidence, self.target_evidence, budget=SearchWorkBudget(0)), 'undecided')

    def test_source_change_cannot_reuse_old_path(self):
        explanation = explain_conflict(self.source, self.certificate, self.evidence)
        changed = replace(self.source.protocol, scope='other')
        problem = ExperimentHypothesisSearch(changed, self.source.hypotheses, 'out')
        self.assertEqual(verify_conflict_explanation(explanation, problem,
            self.certificate, self.evidence), 'invalid')
        altered = replace(explanation, nodes=explanation.nodes[:-1])
        self.assertEqual(verify_conflict_explanation(altered, self.source,
            self.certificate, self.evidence), 'invalid')
