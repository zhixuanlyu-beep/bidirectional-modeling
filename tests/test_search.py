import unittest
from dataclasses import replace
from itertools import product

from bidirectional_modeling import (
    ConflictCertificate, DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol,
)


def scenario():
    worlds = tuple(product(('0', '1'), repeat=4))
    experiments = tuple(SearchExperiment(x, 'do(x,z)=' + x) for x in ('00', '10', '01', '11'))
    additive = tuple(i for i, w in enumerate(worlds)
                     if int(w[3]) - int(w[1]) - int(w[2]) + int(w[0]) == 0)
    p = SearchProtocol('binary interventions v1', 'shared code v1', experiments, worlds,
                       (ResponseConstraint('additive', additive),))
    models = (
        SearchHypothesis('x', worlds.index(('0','1','0','1')), 'additive',
                         DescriptionLength(relations=1), ('additive',), ('x','z')),
        SearchHypothesis('z', worlds.index(('0','0','1','1')), 'additive',
                         DescriptionLength(relations=2), ('additive',), ('x','z')),
        SearchHypothesis('xz', worlds.index(('0','0','0','1')), 'interaction',
                         DescriptionLength(concepts=1, relations=2), (), ('x','z')),
    )
    evidence = tuple(SearchObservation(e.name, y, 'lab-v1')
                     for e, y in zip(experiments, ('0','0','0','1')))
    return ExperimentHypothesisSearch(p, models, 'nonadditive response'), evidence


class SearchTests(unittest.TestCase):
    def test_conflict_prunes_commitments_not_materials(self):
        search, evidence = scenario()
        report = search.search(evidence)
        self.assertEqual(report.rejected, ('x',))
        self.assertEqual(report.pruned, ('z',))
        self.assertEqual(report.compatible, ('xz',))
        self.assertEqual(report.replay_checks, 2)
        self.assertTrue(report.determined)
        c, = report.conflicts
        self.assertEqual(c.commitments, ('additive',))
        self.assertEqual(c.evidence, evidence[1:])  # binary universe needs only three
        self.assertTrue(search.validates_conflict(c, evidence))
        self.assertEqual(search.search(evidence, (c,)).pruned, ('x','z'))

    def test_joint_core_does_not_blame_individual_rules(self):
        # Under a=0: b=0 and b=1 are incompatible together, but each fits alone.
        worlds = tuple(product(('0','1'), repeat=2))
        p = SearchProtocol('scope','code', (SearchExperiment('a','a'), SearchExperiment('b','b')),
                           worlds, (ResponseConstraint('u', (0,2,3)),
                                    ResponseConstraint('v', (1,2,3))))
        hs = tuple(SearchHypothesis(n, w, 'same', DescriptionLength(), cs)
                   for n,w,cs in [('u',0,('u',)), ('v',1,('v',)), ('uv',2,('u','v'))])
        search = ExperimentHypothesisSearch(p, hs, 'g')
        evidence = (SearchObservation('a','0','lab'),)
        c = search.learn_conflict(('u','v'), evidence)
        self.assertEqual(set(c.commitments), {'u','v'})
        r = search.search(evidence, (c,))
        self.assertEqual(set(r.compatible), {'u','v'})
        self.assertEqual(r.pruned, ('uv',))

    def test_revoke_or_rebind_conflict(self):
        search, evidence = scenario()
        c = search.learn_conflict(('additive',), evidence)
        for data in (evidence[:-1], tuple(replace(o,source='recalibrated') for o in evidence)):
            self.assertFalse(search.validates_conflict(c,data))
            self.assertNotIn(c, search.search(data,(c,)).conflicts)
        other = ExperimentHypothesisSearch(replace(search.protocol, scope='new scope'),
                                           search.hypotheses, search.target)
        self.assertFalse(other.validates_conflict(c, evidence))
        self.assertFalse(search.validates_conflict(replace(c, commitments=('invented',)),evidence))
        self.assertFalse(search.validates_conflict(replace(c, commitments=()),evidence))
        self.assertFalse(search.validates_conflict(replace(c, evidence=evidence[:1]),evidence))
        self.assertIsNone(search.learn_conflict((),evidence))
        self.assertIsNone(search.learn_conflict(('additive',),evidence[:1]))
        impossible = evidence + (SearchObservation('00','1','other'),)
        self.assertIsNone(search.learn_conflict(('additive',), impossible))

    def test_quotient_retains_reconstruction_representatives(self):
        search, evidence = scenario()
        h = replace(search.hypotheses[-1], name='different-structure',
                    description=DescriptionLength(relations=9), materials=('new',))
        search = ExperimentHypothesisSearch(search.protocol, search.hypotheses+(h,), search.target)
        self.assertEqual(search.partition(('00',)), (('x','z','xz','different-structure'),))
        self.assertEqual(search.partition()[-1], ('xz','different-structure'))
        self.assertTrue(search.macro_identifiable())
        contrary = replace(h, name='contrary', macro_answer='different answer')
        other = ExperimentHypothesisSearch(search.protocol, search.hypotheses+(contrary,), search.target)
        self.assertFalse(other.macro_identifiable())
        self.assertFalse(other.search(evidence).determined)
        self.assertIsNone(other.next_experiment(evidence))

    def test_macro_basis_is_checked_against_original_catalogue(self):
        search, evidence = scenario()
        c = search.compress_evidence(evidence)
        self.assertTrue(c.determined)
        self.assertTrue(c.minimum_cardinality)
        self.assertEqual(len(c.retained_evidence), 2)
        self.assertTrue(search.validates_macro(c,evidence))
        self.assertFalse(search.validates_macro(replace(c,retained_evidence=()),evidence))
        self.assertFalse(search.validates_macro(replace(c,answers=('wrong',)),evidence))
        self.assertFalse(search.validates_macro(replace(c,determined=False),evidence))
        self.assertFalse(search.validates_macro(replace(c,retained_evidence=evidence),evidence))
        self.assertFalse(search.validates_macro(c,evidence[:-1]))
        # Expanded catalogue invalidates old macro basis, while conflict proof is reusable.
        new = SearchHypothesis('zero',0,'additive',DescriptionLength())
        other = ExperimentHypothesisSearch(search.protocol,search.hypotheses+(new,),search.target)
        self.assertFalse(other.validates_macro(c,evidence))
        conflict = search.learn_conflict(('additive',), evidence)
        self.assertTrue(other.validates_conflict(conflict,evidence))

    def test_budget_and_unknown_are_not_falsification(self):
        search, evidence = scenario()
        r = search.search(evidence,max_replays=0)
        self.assertEqual(set(r.undecided), {'x','z','xz'})
        self.assertFalse(r.determined)
        c = search.compress_evidence(evidence,max_subsets=0)
        self.assertEqual(c.retained_evidence, evidence)
        self.assertFalse(c.minimum_cardinality)
        self.assertTrue(search.validates_macro(c,evidence))
        h = SearchHypothesis('timeout',None,'other',DescriptionLength())
        other = ExperimentHypothesisSearch(search.protocol,search.hypotheses+(h,),search.target)
        self.assertFalse(other.search(evidence).determined)
        self.assertFalse(other.macro_identifiable())
        self.assertIn(('timeout',),other.partition())
        with self.assertRaises(ValueError):
            other.compress_evidence(evidence)
        self.assertFalse(other.validates_macro(c,evidence))

    def test_empty_version_space_never_proves_answer(self):
        search, _ = scenario()
        data = (SearchObservation('00','1','lab'),)
        self.assertFalse(search.search(data).determined)
        with self.assertRaises(ValueError):
            search.compress_evidence(data)
        empty = ExperimentHypothesisSearch(search.protocol,(),search.target)
        self.assertFalse(empty.macro_identifiable())
        self.assertFalse(empty.search().determined)

    def test_targeted_experiment_and_cost(self):
        search, _ = scenario()
        self.assertEqual(search.next_experiment().name, '10')
        experiments = tuple(replace(e,cost=5) if e.name=='10' else e
                            for e in search.protocol.experiments)
        other = ExperimentHypothesisSearch(replace(search.protocol,experiments=experiments),
                                           search.hypotheses,search.target)
        self.assertEqual(other.next_experiment().name, '01')
        # Many structural copies with the same answer require no distinguishing experiment.
        h = replace(search.hypotheses[-1],name='copy')
        same = ExperimentHypothesisSearch(search.protocol,(h,search.hypotheses[-1]),search.target)
        self.assertIsNone(same.next_experiment())

    def test_anomaly_absence_requires_every_position(self):
        n = 7
        worlds = (('0',)*n,) + tuple(tuple('1' if j==i else '0' for j in range(n)) for i in range(n))
        p = SearchProtocol('positions','code',tuple(SearchExperiment(str(i),str(i)) for i in range(n)),worlds)
        hs = tuple(SearchHypothesis(str(i),i,'absent' if i==0 else 'present',DescriptionLength()) for i in range(n+1))
        search = ExperimentHypothesisSearch(p,hs,'any anomaly')
        evidence = tuple(SearchObservation(str(i),'0','lab') for i in range(n))
        self.assertEqual(search.compress_evidence(evidence).retained_evidence,evidence)
        for i in range(n):
            self.assertFalse(search.search(evidence[:i]+evidence[i+1:]).determined)

    def test_irreducibility_is_relative_to_declared_lower_class(self):
        search, _ = scenario()
        self.assertTrue(search.irreducible_against('xz', ('x','z')))
        self.assertFalse(search.irreducible_against('x', ('x',)))
        self.assertIsNone(search.irreducible_against('xz', ()))
        h = SearchHypothesis('unknown',None,'other',DescriptionLength())
        other = ExperimentHypothesisSearch(search.protocol,search.hypotheses+(h,),search.target)
        self.assertIsNone(other.irreducible_against('xz', ('unknown',)))
        self.assertIsNone(other.irreducible_against('unknown', ('xz',)))
        with self.assertRaises(ValueError): search.irreducible_against('xz', ('missing',))

    def test_compression_preserves_multiple_answers_without_claiming_success(self):
        search, evidence = scenario()
        basis = search.compress_evidence(evidence[:1])
        self.assertFalse(basis.determined)
        self.assertEqual(basis.retained_evidence, ())
        self.assertTrue(search.validates_macro(basis, evidence[:1]))

    def test_joint_core_removes_redundancy_and_unknowns_can_inherit_conflicts(self):
        search, evidence = scenario()
        p = replace(search.protocol, constraints=search.protocol.constraints + (
            ResponseConstraint('tautology', tuple(range(len(search.protocol.worlds)))),))
        unknown = SearchHypothesis('uncomputed', None, 'additive', DescriptionLength(), ('additive',))
        other = ExperimentHypothesisSearch(p, search.hypotheses+(unknown,), search.target)
        c = other.learn_conflict(('tautology','additive'), evidence)
        self.assertEqual(c.commitments, ('additive',))
        self.assertIn('uncomputed', other.search(evidence, (c,), max_replays=0).pruned)
        # Forged evidence cannot pass by retaining an unrelated observation.
        macro = search.compress_evidence(evidence)
        bad = replace(macro, retained_evidence=(SearchObservation('00','1','fake'),))
        self.assertFalse(search.validates_macro(bad,evidence))

    def test_demo_integration(self):
        from bidirectional_modeling.search_examples import build_search_demo_report
        report = build_search_demo_report()
        self.assertTrue(report['certificate_valid'])
        self.assertEqual(report['retained_macro_evidence'], ['10','01'])

    def test_input_validation(self):
        search,evidence = scenario()
        for value in (-1, float('nan'), True, 1.5):
            with self.assertRaises(ValueError): DescriptionLength(parameters=value)
        with self.assertRaises(ValueError): SearchExperiment('x','x',0)
        with self.assertRaises(ValueError): SearchObservation('','0','lab')
        with self.assertRaises(ValueError): ResponseConstraint('x',(0,0))
        with self.assertRaises(ValueError): replace(search.protocol,worlds=())
        with self.assertRaises(ValueError): replace(search.protocol,worlds=(('0',),))
        with self.assertRaises(ValueError): replace(search.protocol,worlds=(search.protocol.worlds[0],)*2)
        with self.assertRaises(ValueError): replace(search.protocol,experiments=(search.protocol.experiments[0],)*4)
        with self.assertRaises(ValueError): replace(search.protocol,constraints=(ResponseConstraint('x',(99,)),))
        h = search.hypotheses[0]
        for bad in (replace(h,world=99),replace(h,commitments=('unknown',)),replace(h,world=1)):
            with self.assertRaises(ValueError): ExperimentHypothesisSearch(search.protocol,(bad,),search.target)
        with self.assertRaises(TypeError): ExperimentHypothesisSearch(search.protocol,(replace(h,description=1),),search.target)
        with self.assertRaises(ValueError): ExperimentHypothesisSearch(search.protocol,(h,h),search.target)
        with self.assertRaises(ValueError): search.partition(('unknown',))
        with self.assertRaises(ValueError): search.search((SearchObservation('unknown','0','lab'),))
        with self.assertRaises(ValueError): search.search((SearchObservation('00','9','lab'),))
        with self.assertRaises(ValueError): search.learn_conflict(('unknown',),evidence)


if __name__ == '__main__':
    unittest.main()
