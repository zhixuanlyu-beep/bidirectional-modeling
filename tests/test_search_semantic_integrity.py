import json
import unittest
from dataclasses import replace

from bidirectional_modeling import (
    DescriptionLength, ExecutableSearchAdapter, ExperimentHypothesisSearch,
    ReconstructionRule, SearchHypothesis, SearchObservation, SearchProtocol,
    SearchExperiment, SearchSession, benchmark_search,
)
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.search_examples import conflict_search_scenario
from bidirectional_modeling.structural import fingerprint_value
from test_search_integration import adapter_args


class SemanticIntegrityTests(unittest.TestCase):
    def prepared(self):
        p,c,cases=adapter_args()
        return ExecutableSearchAdapter().prepare(p,c[:1],cases,target='out',world_answers=('low','high'))

    def test_authoritative_target_rejects_inconsistent_add_and_reconstruction(self):
        prepared=self.prepared()
        for backend in ('scan','indexed'):
            search=ExperimentHypothesisSearch(prepared.search.protocol,prepared.search.hypotheses,
                    prepared.search.target,backend=backend,world_answers=prepared.search.world_answers)
            session=SearchSession(search)
            before=session.to_json()
            wrong=SearchHypothesis('new',1,'low',DescriptionLength())
            with self.assertRaisesRegex(ValueError,'target mapping'): session.add_hypothesis(wrong)
            with self.assertRaisesRegex(ValueError,'target mapping'):
                session.reconstruct(ReconstructionRule('change'),'zero',name='new',world=1,
                                    macro_answer='low',description=DescriptionLength())
            self.assertEqual(session.to_json(),before)
            session.add_hypothesis(replace(wrong,macro_answer='high'))
            session.replace_evidence((SearchObservation('read','1','lab'),))
            report=session.run()
            self.assertTrue(report.determined)
            self.assertEqual(report.answers,('high',))
            self.assertTrue(all(r['agrees_with_reference'] for r in benchmark_search(session.search,session.evidence,rounds=2)['results']))

    def test_public_snapshot_configuration_is_read_only(self):
        search=self.prepared().search
        for field,value in (('protocol',search.protocol),('hypotheses',()),('target','new'),
                            ('world_answers',('high','low')),('backend','indexed')):
            with self.assertRaises(AttributeError): setattr(search,field,value)
        with self.assertRaises(ValueError): search.with_hypotheses((SearchHypothesis('wrong',1,'low',DescriptionLength()),))
        self.assertEqual(search.with_hypotheses(search.hypotheses).fingerprint,search.fingerprint)
        with self.assertRaises(ValueError): ExperimentHypothesisSearch(search.protocol,(),search.target,world_answers=('low',))

    def test_mapping_and_provenance_survive_save_restore_and_cannot_be_downgraded(self):
        prepared=self.prepared()
        session=SearchSession.from_prepared(prepared)
        restored=SearchSession.from_json(session.to_json())
        self.assertEqual(restored.search.world_answers,('low','high'))
        self.assertEqual(restored.model_bindings,prepared.batch_bindings)
        self.assertEqual(restored.prepared_problem_fingerprint,prepared.search.fingerprint)
        with self.assertRaises(ValueError): restored.add_hypothesis(SearchHypothesis('bad',1,'low',DescriptionLength()))
        doc=json.loads(session.to_json())
        doc['payload']['schema_version']=1
        doc['checksum']=fingerprint_value(doc['payload'])
        with self.assertRaisesRegex(ValueError,'binding'): SearchSession.from_json(json.dumps(doc))
        doc=json.loads(session.to_json())
        doc['payload']['world_answers']=['high','low']
        doc['checksum']=fingerprint_value(doc['payload'])
        with self.assertRaises(ValueError): SearchSession.from_json(json.dumps(doc))
        with self.assertRaises(ValueError): SearchSession(prepared.search,model_bindings=(('bad',),))

    def test_legacy_unmapped_session_remains_explicitly_unmapped(self):
        search,data=conflict_search_scenario()
        doc=json.loads(SearchSession(search,data).to_json())
        doc['payload']['schema_version']=1
        del doc['payload']['world_answers']
        doc['checksum']=fingerprint_value(doc['payload'])
        restored=SearchSession.from_json(json.dumps(doc))
        self.assertIsNone(restored.search.world_answers)
        self.assertEqual(restored.search.fingerprint,search.fingerprint)

    def test_cross_case_configuration_drift_is_rejected_and_caller_unmodified(self):
        p,c,cases=adapter_args()
        p=SearchProtocol('same conditions','code',
            (SearchExperiment('a','read y'),SearchExperiment('b','read y')),
            (('0','0'),('0','1'),('1','1')))
        cases=(replace(cases[0],experiment='a'),replace(cases[0],experiment='b'))
        class Drifting:
            def __init__(self): self.calls=0
            def collect(self,m,ctx,horizon,budget):
                m.states['s']['y']='0' if self.calls<2 else '1'
                self.calls+=1
                return SatisfactionEvaluator().collect(m,ctx,horizon,budget)
        result=ExecutableSearchAdapter(Drifting()).prepare(p,c[:1],cases,target='g',world_answers=('a','hybrid','b'))
        self.assertIsNone(result.search.hypotheses[0].world)
        self.assertIn('model_declaration_changed',result.diagnostics[0][2])
        self.assertEqual(c[0].model.states['s']['y'],'0')
        self.assertEqual(result.batch_bindings,())
        self.assertFalse(result.search.search().determined)

    def test_matrix_replay_detects_unreported_external_drift(self):
        p,c,cases=adapter_args()
        p=SearchProtocol('same conditions','code',
            (SearchExperiment('a','read y'),SearchExperiment('b','read y')),
            (('0','0'),('0','1'),('1','1')))
        cases=(replace(cases[0],experiment='a'),replace(cases[0],experiment='b'))
        from bidirectional_modeling import Context, ScenarioKey, Trace
        cases=tuple(replace(case,context=Context(scenario_manifest=(ScenarioKey('s','baseline'),))) for case in cases)
        class ExternalModel:
            name='external'
            def __init__(self): self.calls=0
            def search_signature(self): return {'version':'fixed'}
            def simulate(self,context,horizon):
                output='0' if self.calls<2 else '1'
                self.calls+=1
                return (Trace(self.name,'s','baseline',tuple({'y':output} for _ in range(horizon+1))),)
        result=ExecutableSearchAdapter().prepare(p,(replace(c[0],model=ExternalModel()),),cases,
                     target='g',world_answers=('a','hybrid','b'))
        self.assertIsNone(result.search.hypotheses[0].world)
        self.assertIn('non_deterministic_response',result.diagnostics[0][2])
        class Opaque:
            name='opaque'
        result=ExecutableSearchAdapter().prepare(p,(replace(c[0],model=Opaque()),),cases,
                     target='g',world_answers=('a','hybrid','b'))
        self.assertIsNone(result.search.hypotheses[0].world)
        self.assertIn('search_signature',result.diagnostics[0][2])
