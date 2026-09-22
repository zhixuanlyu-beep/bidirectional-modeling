import unittest
from dataclasses import replace
from itertools import combinations, product

from bidirectional_modeling import (
    DescriptionLength, ExperimentHypothesisSearch, ReconstructionRule,
    ResponseConstraint, SearchBudgetExceeded, SearchExperiment, SearchHypothesis,
    SearchObservation, SearchProtocol, SearchSession, SearchWorkBudget, benchmark_search,
)
from bidirectional_modeling.search_examples import conflict_search_scenario


def powerset(values):
    for n in range(len(values)+1):
        yield from combinations(values,n)


class IndexTests(unittest.TestCase):
    def test_exhaustive_small_universe_matches_scan(self):
        worlds=tuple(product(('0','1'),repeat=2))
        experiments=(SearchExperiment('a','a'),SearchExperiment('b','b'))
        for mask in range(16):
            allowed=tuple(i for i in range(4) if mask & (1<<i))
            p=SearchProtocol('scope','code',experiments,worlds,(ResponseConstraint('u',allowed),))
            hs=tuple(SearchHypothesis(str(i),i,'yes' if i in (0,3) else 'no',
                    DescriptionLength(relations=i),('u',) if i in allowed else ()) for i in range(4))
            scan=ExperimentHypothesisSearch(p,hs,'parity')
            indexed=ExperimentHypothesisSearch(p,hs,'parity',backend='indexed')
            self.assertEqual(scan.fingerprint,indexed.fingerprint)
            for world in worlds:
                data=tuple(SearchObservation(e.name,y,'lab') for e,y in zip(experiments,world))
                for evidence in powerset(data):
                    left,right=scan.search(evidence),indexed.search(evidence)
                    for field in ('compatible','pruned','rejected','undecided','answers','determined',
                                  'full_quotient','observed_quotient','conflicts','stop_reason'):
                        self.assertEqual(getattr(left,field),getattr(right,field))
                    self.assertEqual(scan.compress_evidence(evidence),indexed.compress_evidence(evidence))
                    self.assertEqual(scan.next_experiment(evidence),indexed.next_experiment(evidence))
                    for core in ((),('u',)):
                        a=scan._worlds(core,evidence,budget=SearchWorkBudget())
                        b=indexed._worlds(core,evidence,budget=SearchWorkBudget())
                        self.assertEqual(a,set(b))
            contradictory=(SearchObservation('a','0','lab'),SearchObservation('a','1','lab'))
            self.assertEqual(indexed.search(contradictory).compatible,())

    def test_interrupted_build_is_not_cached(self):
        scan,data=conflict_search_scenario()
        indexed=ExperimentHypothesisSearch(scan.protocol,scan.hypotheses,scan.target,backend='indexed')
        report=indexed.search(data,budget=SearchWorkBudget(2))
        self.assertEqual(report.stop_reason,'work_budget_exhausted')
        self.assertIsNone(indexed._response_index)
        self.assertEqual(indexed.search(data).compatible,('xz',))
        old=indexed._response_index
        warm=SearchWorkBudget()
        indexed.search(data,budget=warm)
        self.assertEqual(warm.work.index_entries,0)
        self.assertIs(indexed._response_index,old)
        # Same semantic response domain with changed protocol identity cannot reuse an index.
        indexed.protocol=replace(indexed.protocol,scope='new scope')
        indexed.search(data)
        self.assertIsNot(indexed._response_index,old)
        with self.assertRaises(ValueError): indexed.search((SearchObservation('00','wrong','lab'),))
        with self.assertRaises(ValueError): ExperimentHypothesisSearch(scan.protocol,scan.hypotheses,scan.target,backend='missing')

    def test_unknowns_and_cancelled_index_queries_remain_undecided(self):
        scan,data=conflict_search_scenario()
        unknown=SearchHypothesis('unknown',None,'other',DescriptionLength())
        indexed=ExperimentHypothesisSearch(scan.protocol,scan.hypotheses+(unknown,),scan.target,backend='indexed')
        self.assertFalse(indexed.search(data).determined)
        report=indexed.search(data,budget=SearchWorkBudget(cancelled=lambda:True))
        self.assertEqual(report.stop_reason,'cancelled')
        self.assertFalse(report.determined)
        c=scan.compress_evidence(data)
        plain=ExperimentHypothesisSearch(scan.protocol,scan.hypotheses,scan.target,backend='indexed')
        self.assertTrue(plain.verify_macro(c,data).valid)

    def test_benchmark_includes_cold_index_build(self):
        search,data=conflict_search_scenario()
        report=benchmark_search(search,data,rounds=2)
        row=report['results'][-1]
        self.assertEqual(row['strategy'],'indexed_conflict_reuse')
        self.assertGreater(row['work']['index_entries'],0)
        self.assertTrue(row['agrees_with_reference'])
        self.assertEqual(row['false_pruned'],[])


class ReconstructionTests(unittest.TestCase):
    def test_failed_materials_can_be_reconstructed_without_inheriting_conflict(self):
        search,data=conflict_search_scenario()
        indexed=ExperimentHypothesisSearch(search.protocol,search.hypotheses,search.target,backend='indexed')
        session=SearchSession(indexed,data)
        session.run()
        rule=ReconstructionRule('joint action',withdraw=('additive',))
        child=session.reconstruct(rule,'x',name='rebuilt',world=1,macro_answer='interaction',
                                  description=DescriptionLength(concepts=1,relations=2))
        self.assertEqual(child.materials,('x','z'))
        self.assertEqual(child.commitments,())
        self.assertIn('rebuilt',session.run().compatible)
        self.assertEqual(session.events[-1]['rule'],'joint action')
        self.assertEqual(session.events[-1]['withdrawn'],['additive'])
        restored=SearchSession.from_json(session.to_json())
        self.assertEqual(restored.search.backend,'indexed')
        self.assertEqual(restored.run().compatible,session.run().compatible)
        # Constraint-preserving descendants remain pruneable.
        rule=ReconstructionRule('retain commitments')
        session.reconstruct(rule,'x',name='still-additive',world=search.hypotheses[0].world,
                            macro_answer='additive',description=DescriptionLength(relations=2))
        self.assertIn('still-additive',session.run().pruned)

    def test_invalid_reconstruction_is_transactional(self):
        search,data=conflict_search_scenario()
        session=SearchSession(search,data)
        before=session.to_json()
        with self.assertRaises(ValueError):
            session.reconstruct(ReconstructionRule('bad'),'x',name='bad',world=1,
                                macro_answer='interaction',description=DescriptionLength())
        self.assertEqual(session.to_json(),before)
        with self.assertRaises(ValueError): session.reconstruct(ReconstructionRule('bad'),'missing')
        with self.assertRaises(ValueError): ReconstructionRule('bad',('a',),('a',))
        with self.assertRaises(ValueError): ReconstructionRule('bad',('a','a'))
        with self.assertRaises(ValueError):
            ReconstructionRule('bad',('missing',)).apply(search.hypotheses[0],name='bad',world=0,
                                macro_answer='other',description=DescriptionLength())
        changed=ReconstructionRule('add',add=('additive',)).apply(search.hypotheses[0],name='new',
                    world=search.hypotheses[0].world,macro_answer='same',
                    description=DescriptionLength(),materials=('replacement',))
        self.assertEqual(changed.commitments,('additive',))
        self.assertEqual(changed.materials,('replacement',))
