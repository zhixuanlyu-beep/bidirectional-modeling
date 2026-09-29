"""A proof path depends on active evidence, inherited rules and target replay."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.certificate_transport import transport_conflict
from bidirectional_modeling.search import ConflictCertificate
from bidirectional_modeling.search_explanations import (
    explain_conflict, explain_transported_conflict, verify_conflict_explanation,
    verify_transport_explanation,
)
import bidirectional_modeling.search_explanations as explanation_module
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

    def test_conflict_path_records_declared_certificate_dependencies(self):
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

    def test_declared_support_can_include_redundant_observations(self):
        protocol = SearchProtocol('two tests', 'code',
            (SearchExperiment('a', 'read a'), SearchExperiment('b', 'read b')),
            (('0', '0'), ('0', '1'), ('1', '0'), ('1', '1')),
            (ResponseConstraint('C', (0, 1)),))
        search = ExperimentHypothesisSearch(protocol, (), 'out')
        a = SearchObservation('a', '1', 'lab')
        b = SearchObservation('b', '1', 'lab')
        oversized = ConflictCertificate(protocol.fingerprint, ('C',), (a, b))
        self.assertTrue(search.validates_conflict(oversized, (a, b)))
        path = explain_conflict(search, oversized, (a, b))
        self.assertEqual(verify_conflict_explanation(path, search, oversized, (a, b)),
                         'valid')
        self.assertEqual([node.subject for node in path.nodes if node.kind == 'observation'],
                         [a, b])
        self.assertEqual(verify_conflict_explanation(path, search, oversized, (a,)),
                         'invalid')  # exact certificate dependency was removed
        self.assertEqual(search.learn_conflict(('C',), (a, b)).evidence, (a,))

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
        missing = explain_transported_conflict(self.receipt, self.source, self.target,
            self.transition, self.certificate, self.evidence, ())
        self.assertEqual((missing.status, missing.reason),
                         ('invalid', 'missing_evidence_translation'))
        forged = explain_transported_conflict(replace(self.receipt, reason='forged'),
            self.source, self.target, self.transition, self.certificate,
            self.evidence, self.target_evidence)
        self.assertEqual((forged.status, forged.reason), ('invalid', 'receipt_mismatch'))
        exhausted = explain_transported_conflict(self.receipt, self.source, self.target,
            self.transition, self.certificate, self.evidence, self.target_evidence,
            budget=SearchWorkBudget(0))
        self.assertEqual((exhausted.status, exhausted.reason),
                         ('undecided', 'work_budget_exhausted'))

    def test_source_change_cannot_reuse_old_path(self):
        explanation = explain_conflict(self.source, self.certificate, self.evidence)
        changed = replace(self.source.protocol, scope='other')
        problem = ExperimentHypothesisSearch(changed, self.source.hypotheses, 'out')
        self.assertEqual(verify_conflict_explanation(explanation, problem,
            self.certificate, self.evidence), 'invalid')
        altered = replace(explanation, nodes=explanation.nodes[:-1])
        self.assertEqual(verify_conflict_explanation(altered, self.source,
            self.certificate, self.evidence), 'invalid')

    def test_shared_builder_defect_cannot_pass_independent_graph_check(self):
        original = explanation_module._conflict_nodes
        def faulty(prefix, protocol, certificate):
            nodes, root = original(prefix, protocol, certificate)
            return [n for n in nodes if n.kind != 'observation'], root
        with patch.object(explanation_module, '_conflict_nodes', faulty):
            broken = explain_conflict(self.source, self.certificate, self.evidence)
            self.assertEqual(broken.status, 'valid')
            self.assertEqual(verify_conflict_explanation(broken, self.source,
                self.certificate, self.evidence), 'invalid')
            transported = explain_transported_conflict(self.receipt, self.source,
                self.target, self.transition, self.certificate,
                self.evidence, self.target_evidence)
            self.assertEqual(verify_transport_explanation(transported, self.receipt,
                self.source, self.target, self.transition, self.certificate,
                self.evidence, self.target_evidence), 'invalid')
