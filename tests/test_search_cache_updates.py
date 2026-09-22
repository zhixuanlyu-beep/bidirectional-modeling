import json
import unittest
from dataclasses import asdict, replace, FrozenInstanceError
from unittest.mock import patch

from bidirectional_modeling import (
    ExperimentHypothesisSearch, SearchBenchmarkStep, SearchBudgetExceeded,
    SearchSession, SearchWorkBudget, benchmark_search_updates,
)
from bidirectional_modeling.search_examples import conflict_search_scenario, dynamic_search_scenario
from bidirectional_modeling.structural import fingerprint_value


class CacheTests(unittest.TestCase):
    def test_fingerprints_compute_once_without_changing_serialization(self):
        original,data=conflict_search_scenario()
        protocol=replace(original.protocol)
        search=ExperimentHypothesisSearch(protocol,original.hypotheses,original.target)
        with patch('bidirectional_modeling.search.fingerprint_value',wraps=fingerprint_value) as counted:
            first=search.fingerprint
            self.assertEqual(first,search.fingerprint)
            self.assertEqual(protocol.fingerprint,protocol.fingerprint)
            self.assertEqual(counted.call_count,2)
        self.assertEqual(first,original.fingerprint)
        self.assertNotIn('fingerprint',asdict(protocol))
        changed=replace(protocol,scope='different')
        self.assertNotEqual(changed.fingerprint,protocol.fingerprint)
        self.assertEqual(SearchSession.from_json(SearchSession(search,data).to_json()).search.fingerprint,first)
        with self.assertRaises(FrozenInstanceError): protocol.fingerprint='bad'

    def test_candidate_growth_shares_only_complete_read_only_index(self):
        original,data=conflict_search_scenario()
        indexed=ExperimentHypothesisSearch(original.protocol,original.hypotheses,original.target,backend='indexed')
        indexed.search(data)
        child=indexed.with_hypotheses(indexed.hypotheses+(replace(indexed.hypotheses[-1],name='copy'),))
        self.assertIs(child._response_index,indexed._response_index)
        with self.assertRaises(TypeError): child._response_index.responses['bad']=1
        with self.assertRaises(TypeError): child._response_index.constraints['bad']=1
        with self.assertRaises(FrozenInstanceError): child._response_index.all_worlds=0
        budget=SearchWorkBudget()
        result=child.search(data,budget=budget)
        self.assertEqual(budget.work.index_entries,0)
        self.assertIn('copy',result.compatible)
        self.assertNotEqual(indexed.fingerprint,child.fingerprint)
        separate=ExperimentHypothesisSearch(replace(indexed.protocol,scope='new'),indexed.hypotheses,
                                           indexed.target,backend='indexed')
        self.assertIsNone(separate._response_index)
        separate.search(data)
        self.assertIsNot(separate._response_index,indexed._response_index)
        partial=ExperimentHypothesisSearch(original.protocol,original.hypotheses,original.target,backend='indexed')
        partial.search(data,budget=SearchWorkBudget(2))
        self.assertIsNone(partial.with_hypotheses(partial.hypotheses)._response_index)

    def test_session_add_keeps_index_but_not_old_macro_certificate_binding(self):
        original,data=conflict_search_scenario()
        indexed=ExperimentHypothesisSearch(original.protocol,original.hypotheses,original.target,backend='indexed')
        certificate=indexed.compress_evidence(data)
        session=SearchSession(indexed,data)
        session.add_hypothesis(replace(indexed.hypotheses[-1],name='copy'))
        self.assertIs(indexed._response_index,session.search._response_index)
        self.assertFalse(session.search.validates_macro(certificate,data))
        restored=SearchSession.from_json(session.to_json())
        # Restore may build its own index to validate evidence, but never deserializes ours.
        self.assertIsNot(restored.search._response_index,indexed._response_index)
        self.assertEqual(restored.run().compatible,session.run().compatible)


class DynamicBenchmarkTests(unittest.TestCase):
    def test_growth_retraction_and_recalibration_agree(self):
        search,steps=dynamic_search_scenario(copies=6)
        result=benchmark_search_updates(search,steps,repetitions=2)
        self.assertEqual(len(result['results']),4)
        for row in result['results']:
            self.assertTrue(row['complete'])
            self.assertTrue(row['agrees_with_reference'])
            self.assertEqual(len(row['trials']),2)
            self.assertIsNotNone(row['median_seconds'])
            for trial in row['trials']:
                stages=trial['stages']
                self.assertEqual(trial['completed_steps'],4)
                self.assertTrue(all(s['false_excluded']==[] for s in stages))
                self.assertEqual(stages[1]['candidate_count'],8)
                self.assertEqual(len(stages[2]['compatible']),8)
                self.assertEqual(stages[3]['compatible'],['xz'])
                if row['strategy']=='failed_model_cache':
                    self.assertTrue(stages[2]['cache_reset'])
                if row['strategy'].endswith('conflict_reuse'):
                    self.assertEqual(stages[2]['revoked_certificates'],1)
                if row['strategy']=='indexed_conflict_reuse':
                    self.assertGreater(stages[0]['work']['index_entries'],0)
                    self.assertEqual(stages[0]['work']['index_entries'],stages[-1]['work']['index_entries'])

    def test_incomplete_trials_do_not_report_agreement_or_full_runtime_median(self):
        search,steps=dynamic_search_scenario(copies=2)
        result=benchmark_search_updates(search,steps,repetitions=1,max_operations=0)
        for row in result['results']:
            self.assertFalse(row['complete'])
            self.assertIsNone(row['agrees_with_reference'])
            self.assertIsNone(row['median_seconds'])
            self.assertEqual(row['trials'][0]['completed_steps'],0)
            self.assertEqual(row['trials'][0]['stop_reason'],'work_budget_exhausted')

    def test_invalid_workloads(self):
        search,steps=dynamic_search_scenario()
        with self.assertRaises(ValueError): benchmark_search_updates(search,steps,repetitions=0)
        with self.assertRaises(ValueError): benchmark_search_updates(search,())
        with self.assertRaises(ValueError): SearchBenchmarkStep('',())
        with self.assertRaises(ValueError): dynamic_search_scenario(-1)
        unknown=replace(search.hypotheses[0],name='unknown',world=None)
        with self.assertRaises(ValueError): benchmark_search_updates(search,(SearchBenchmarkStep('unknown',(),(unknown,)),))
        with self.assertRaises(ValueError): benchmark_search_updates(search,(SearchBenchmarkStep('duplicate',(),(search.hypotheses[0],)),))
        import tracemalloc
        tracemalloc.start()
        try:
            with self.assertRaises(RuntimeError): benchmark_search_updates(search,steps)
        finally:
            tracemalloc.stop()
