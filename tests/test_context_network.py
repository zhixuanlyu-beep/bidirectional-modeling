import unittest
from dataclasses import replace
from itertools import product

from bidirectional_modeling import (
    ModelingContext, ContextChange, ContextTransition, ContextNetwork, validate_context_transition,
    SearchExperiment, SearchProtocol, ResponseConstraint, ExperimentHypothesisSearch,
    SearchObservation, SearchWorkBudget, transport_conflict, verify_transported_conflict,
    SearchSession,
)


def context(name, protocol):
    return ModelingContext(name, protocol, 'laboratory', 'exact', ('x', 'z'), 'finite', 'interaction')


def identity_transition(source, target, kind=ContextChange.RECONSTRUCTION):
    return ContextTransition(context('old', source), context('new', target), kind,
        tuple((e.name, e.name) for e in source.experiments),
        tuple((e.name, v, v) for i, e in enumerate(source.experiments)
              for v in sorted({w[i] for w in target.worlds})),
        tuple((c.name, c.name) for c in source.constraints))


def additive_protocol(values=('0', '1'), scope='binary'):
    worlds = tuple(product(values, repeat=4))
    return SearchProtocol(scope, 'code', tuple(SearchExperiment(n, n) for n in ('00', '10', '01', '11')),
        worlds, (ResponseConstraint('additive', tuple(i for i, w in enumerate(worlds)
            if int(w[3])-int(w[1])-int(w[2])+int(w[0]) == 0)),))


class ContextTests(unittest.TestCase):
    def test_extension_splits_response_classes_without_eliminating_old_worlds(self):
        source = SearchProtocol('old', 'code', (SearchExperiment('a', 'a'),), (('0',), ('1',)))
        target = SearchProtocol('new', 'code', source.experiments+(SearchExperiment('b', 'b'),),
                                tuple(product(('0', '1'), repeat=2)))
        transition = identity_transition(source, target, ContextChange.EXTENSION)
        result = validate_context_transition(transition)
        self.assertEqual(result.status, 'valid')
        self.assertEqual(result.split_source_worlds, 2)
        self.assertEqual(result.unrepresented_source_worlds, 0)
        network = ContextNetwork()
        self.assertEqual(network.add_transition(transition), result)
        self.assertEqual(len(network.transitions), 1)
        with self.assertRaises(ValueError):
            network.add_context(replace(transition.source, resolution='coarser'))
        self.assertEqual(len(network.contexts), 2)

    def test_restriction_cannot_silently_delete_behavior(self):
        p = SearchProtocol('old', 'code', (SearchExperiment('a', 'a'),), (('0',), ('1',)))
        q = replace(p, scope='new', worlds=(('0',),))
        t = identity_transition(p, q, ContextChange.RESTRICTION)
        self.assertEqual(validate_context_transition(t).reason, 'restriction_loses_source_behavior')
        self.assertEqual(validate_context_transition(replace(t, kind=ContextChange.REFINEMENT)).status, 'valid')
        self.assertEqual(validate_context_transition(replace(t, experiments=(), responses=())).status, 'invalid')

    def test_binding_and_missing_translation_fail_closed(self):
        p = additive_protocol()
        t = identity_transition(p, p)
        self.assertEqual(validate_context_transition(replace(t, responses=())).status, 'invalid')
        self.assertEqual(validate_context_transition(t, budget=SearchWorkBudget(0)).status, 'undecided')
        self.assertEqual(validate_context_transition(t, budget=SearchWorkBudget(cancelled=lambda: True)).status, 'undecided')
        with self.assertRaises(ValueError):
            replace(t, experiments=(('unknown', '00'),))
        with self.assertRaises(ValueError):
            replace(t, responses=t.responses+t.responses[:1])
        with self.assertRaises(ValueError):
            replace(t, commitments=(('unknown', 'additive'),))
        with self.assertRaises(TypeError):
            replace(t, kind='extension')
        with self.assertRaises(ValueError):
            replace(t.source, objects=('x', 'x'))

    def test_enlarged_universe_does_not_inherit_binary_additivity_conflict(self):
        p, q = additive_protocol(), additive_protocol(('-1', '0', '1'), 'expanded')
        old, new = ExperimentHypothesisSearch(p, (), 'interaction'), ExperimentHypothesisSearch(q, (), 'interaction')
        data = tuple(SearchObservation(e, r, 'lab-v1') for e, r in (('10', '0'), ('01', '0'), ('11', '1')))
        certificate = old.learn_conflict(('additive',), data)
        self.assertIsNotNone(certificate)
        self.assertIsNone(new.learn_conflict(('additive',), data))
        t = identity_transition(p, q)
        report = validate_context_transition(t)
        self.assertEqual(report.status, 'valid')
        self.assertGreater(report.unmatched_target_worlds, 0)
        receipt = transport_conflict(old, new, t, certificate, data, data, tuple(zip(data, data)))
        self.assertEqual(receipt.status, 'not_applicable')
        self.assertIsNone(receipt.certificate)

    def test_transport_reproves_and_binds_new_provenance(self):
        p, q = additive_protocol(), additive_protocol(scope='recalibrated')
        old, new = ExperimentHypothesisSearch(p, (), 'interaction'), ExperimentHypothesisSearch(q, (), 'interaction')
        data = tuple(SearchObservation(e, r, 'v1') for e, r in (('10', '0'), ('01', '0'), ('11', '1')))
        evidence = tuple(replace(o, source='v2') for o in data)
        c = old.learn_conflict(('additive',), data)
        t = identity_transition(p, q)
        links = tuple(zip(data, evidence))
        receipt = transport_conflict(old, new, t, c, data, evidence, links)
        self.assertEqual(receipt.status, 'verified')
        self.assertEqual(receipt.certificate.protocol_fingerprint, q.fingerprint)
        self.assertNotEqual(c.protocol_fingerprint, q.fingerprint)
        self.assertEqual(verify_transported_conflict(receipt, old, new, t, c, data, evidence), 'valid')
        self.assertEqual(verify_transported_conflict(replace(receipt, reason='forged'), old, new, t, c, data, evidence), 'invalid')
        self.assertEqual(transport_conflict(old, new, t, c, data, evidence, ()).status, 'not_applicable')
        self.assertEqual(transport_conflict(old, new, t, c, data, evidence, links,
                                          budget=SearchWorkBudget(0)).status, 'undecided')
        self.assertEqual(transport_conflict(new, old, t, c, data, evidence, links).status, 'not_applicable')
        self.assertEqual(transport_conflict(old, new, t, c, (), evidence, links).status, 'not_applicable')
        self.assertEqual(transport_conflict(old, new, replace(t, commitments=()), c, data, evidence, links).status, 'not_applicable')
        session = SearchSession(old, data, (c,))
        migration = session.migrate_context(new, t, evidence, links)
        self.assertEqual(migration.status, 'completed')
        self.assertEqual(session.search.protocol, p)
        self.assertEqual(session.evidence, data)
        self.assertEqual(migration.session.search.protocol, q)
        self.assertEqual(SearchSession.from_json(migration.session.to_json()).certificates, (receipt.certificate,))
        self.assertEqual(session.migrate_context(new, t, evidence, links, budget=SearchWorkBudget(0)).status, 'undecided')

    def test_failed_transition_does_not_publish_nodes(self):
        p = additive_protocol()
        t = identity_transition(p, p)
        network = ContextNetwork()
        network.add_transition(replace(t, responses=()))
        self.assertFalse(network.contexts)
        network.add_context(replace(t.target, resolution='changed'))
        with self.assertRaises(ValueError):
            network.add_transition(t)
        self.assertNotIn('old', network.contexts)
