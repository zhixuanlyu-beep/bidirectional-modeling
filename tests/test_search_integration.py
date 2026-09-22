import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from bidirectional_modeling import (
    BidirectionalModelingEngine, Context, DescriptionLength, ExecutableSearchAdapter,
    FiniteStateModel, ModelMetrics, ModelSearchCandidate, ModelSearchCase, ScenarioKey,
    SearchBudgetExceeded, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchSession, SearchWorkBudget, benchmark_search,
)
from bidirectional_modeling.search_examples import conflict_search_scenario
from bidirectional_modeling.structural import fingerprint_value


def model(name='zero', output='0'):
    return FiniteStateModel(name, {'s':{'y':output}}, ('s',), ('noop',),
                            lambda state, action, context: state,
                            lambda state, context: dict(state), ModelMetrics(1,1,0))


def adapter_args():
    protocol = SearchProtocol('scope','code',(SearchExperiment('read','read y'),), (('0',),('1',)))
    case = ModelSearchCase('read',Context(),ScenarioKey('s','baseline'),'y')
    candidates = tuple(ModelSearchCandidate(model(name,y),DescriptionLength(relations=1))
                       for name,y in (('zero','0'),('one','1')))
    return protocol,candidates,(case,)


class AdapterTests(unittest.TestCase):
    def test_engine_collects_bound_predictions_without_creating_observations(self):
        result = BidirectionalModelingEngine().prepare_hypothesis_search(
            *adapter_args(),target='output',world_answers=('low','high'))
        self.assertEqual(result.simulations_used,4)
        self.assertEqual(result.diagnostics,())
        self.assertEqual(len(result.batch_bindings),2)
        self.assertEqual(tuple(h.world for h in result.search.hypotheses),(0,1))
        self.assertFalse(result.search.search().determined)
        data = (SearchObservation('read','1','external lab'),)
        self.assertEqual(result.search.search(data).compatible,('one',))

    def test_budget_and_out_of_universe_are_unknown(self):
        result = ExecutableSearchAdapter().prepare(*adapter_args(),target='out',world_answers=('a','b'),max_simulations=1)
        self.assertEqual(result.simulations_used,1)
        self.assertTrue(all(h.world is None for h in result.search.hypotheses))
        self.assertFalse(result.search.search().determined)
        p,c,cases = adapter_args()
        result = ExecutableSearchAdapter().prepare(p,(replace(c[0],model=model(output='2')),),cases,
                                                   target='out',world_answers=('a','b'))
        self.assertIsNone(result.search.hypotheses[0].world)

    def test_adapter_binds_readout_context_and_target_table(self):
        p,c,cases = adapter_args()
        a = ExecutableSearchAdapter().prepare(p,c,cases,target='out',world_answers=('a','b'))
        b = ExecutableSearchAdapter().prepare(p,c,cases,target='out',world_answers=('b','a'))
        self.assertNotEqual(a.search.fingerprint,b.search.fingerprint)
        b = ExecutableSearchAdapter().prepare(p,c,(replace(cases[0],context=Context(observer='new')),),
                                              target='out',world_answers=('a','b'))
        self.assertNotEqual(a.search.protocol.fingerprint,b.search.protocol.fingerprint)
        with self.assertRaises(ValueError): ExecutableSearchAdapter().prepare(p,c,(),target='out',world_answers=('a','b'))
        with self.assertRaises(ValueError): ExecutableSearchAdapter().prepare(p,c,cases,target='out',world_answers=('a',))
        with self.assertRaises(ValueError): replace(cases[0],horizon=0)

    def test_non_determinism_and_collector_error_fail_closed(self):
        from bidirectional_modeling.evaluation import SatisfactionEvaluator
        class Alternating:
            def __init__(self): self.calls=0
            def collect(self,m,ctx,horizon,budget):
                self.calls+=1
                m.states['s']['y'] = str(self.calls%2)
                return SatisfactionEvaluator().collect(m,ctx,horizon,budget)
        p,c,cases = adapter_args()
        result = ExecutableSearchAdapter(Alternating()).prepare(p,c[:1],cases,target='out',world_answers=('a','b'))
        self.assertIsNone(result.search.hypotheses[0].world)
        self.assertIn('non_deterministic_response',result.diagnostics[0][2])
        class Broken:
            def collect(self,*args): raise RuntimeError('broken')
        result = ExecutableSearchAdapter(Broken()).prepare(p,c,cases,target='out',world_answers=('a','b'),max_simulations=5)
        self.assertEqual(result.simulations_used,5)
        self.assertEqual(len(result.diagnostics),2)
        result = ExecutableSearchAdapter().prepare(p,c[:1],(replace(cases[0],scenario=ScenarioKey('missing','baseline')),),target='out',world_answers=('a','b'))
        self.assertIsNone(result.search.hypotheses[0].world)


class SessionTests(unittest.TestCase):
    def setUp(self):
        search,data = conflict_search_scenario()
        self.session = SearchSession(search,data)
        self.session.run()

    def test_roundtrip_and_atomic_file(self):
        with tempfile.TemporaryDirectory(dir='.') as directory:
            path=Path(directory)/'session.json'
            self.session.save(path)
            restored=SearchSession.load(path)
            self.assertEqual(restored.search.fingerprint,self.session.search.fingerprint)
            self.assertEqual(restored.run().compatible,('xz',))
            with self.assertRaises(ValueError): SearchSession.load(path,max_bytes=2)
            with self.assertRaises(ValueError): SearchSession.load(path,max_bytes=-1)
        document=json.loads(self.session.to_json())
        document['payload']['target']='changed'
        with self.assertRaises(ValueError): SearchSession.from_json(json.dumps(document))

    def test_revocation_and_reconstruction_history(self):
        self.assertEqual(len(self.session.certificates),1)
        revoked=self.session.replace_evidence(self.session.evidence[:1])
        self.assertEqual(len(revoked),1)
        self.assertEqual(self.session.certificates,())
        self.assertEqual(len(self.session.run().compatible),3)
        h=SearchHypothesis('rebuilt',1,'interaction',DescriptionLength(),(),('x','z'))
        self.session.add_hypothesis(h,parent='x')
        self.assertEqual(self.session.events[-1]['withdrawn'],['additive'])
        restored=SearchSession.from_json(self.session.to_json())
        self.assertEqual(restored.revision,2)
        self.assertEqual(restored.events,self.session.events)
        with self.assertRaises(ValueError): self.session.add_hypothesis(h,parent='missing')

    def test_interruptions_do_not_destroy_state(self):
        before=self.session.to_json()
        with self.assertRaises(SearchBudgetExceeded):
            self.session.replace_evidence((),budget=SearchWorkBudget(0))
        self.assertEqual(self.session.to_json(),before)
        self.session.run(budget=SearchWorkBudget(0))
        self.assertEqual(self.session.to_json(),before)
        with self.assertRaises(SearchBudgetExceeded):
            SearchSession.from_json(before,budget=SearchWorkBudget(0))

    def test_checksums_do_not_replace_proof_verification(self):
        document=json.loads(self.session.to_json())
        p=document['payload']
        p['certificates'][0]['commitments']=['imaginary']
        document['checksum']=fingerprint_value(p)
        with self.assertRaises(ValueError): SearchSession.from_json(json.dumps(document))
        for key,value in [('schema_version',99),('revision',-1),('events',{}),('problem_fingerprint','bad')]:
            d=json.loads(self.session.to_json())
            d['payload'][key]=value
            d['checksum']=fingerprint_value(d['payload'])
            with self.assertRaises(ValueError): SearchSession.from_json(json.dumps(d))


class BenchmarkTests(unittest.TestCase):
    def test_strategies_agree_without_assuming_speedup(self):
        search,data=conflict_search_scenario()
        report=benchmark_search(search,data,rounds=3)
        self.assertFalse(report['prediction_preparation_included'])
        for r in report['results']:
            self.assertTrue(r['agrees_with_reference'])
            self.assertEqual(r['false_pruned'],[])
            self.assertEqual(r['rounds_completed'],3)
            self.assertGreater(r['peak_bytes'],0)
        self.assertLess(report['results'][1]['replay_checks'],report['results'][0]['replay_checks'])
        report=benchmark_search(search,data,rounds=3,max_operations=0)
        self.assertTrue(all(r['rounds_completed']==0 for r in report['results']))
        with self.assertRaises(ValueError): benchmark_search(search,data,rounds=0)
        unknown=replace(search.hypotheses[0],world=None)
        from bidirectional_modeling import ExperimentHypothesisSearch
        with self.assertRaises(ValueError): benchmark_search(ExperimentHypothesisSearch(search.protocol,(unknown,),search.target),data)
