"""Distinguish necessary proof work from full-result and setup overhead."""
import copy
import pickle
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from bidirectional_modeling import (
    ConstraintQuery, DescriptionLength, ExperimentHypothesisSearch,
    LowerSubstituteQuery, MacroAlternativeQuery, ResponseConstraint,
    SearchExperiment, SearchHypothesis, SearchObservation, SearchProtocol,
    SearchWorkBudget,
)
from bidirectional_modeling.context_network import (
    ContextChange, ContextTransition, ModelingContext, validate_context_transition,
)
from bidirectional_modeling.search import SearchBudgetExceeded
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter
from bidirectional_modeling.search_index import ResponseIndex, WorldMask
from bidirectional_modeling.search_lazy import LazyExecutableSearch
from bidirectional_modeling.search_queries import verify_query_result
from test_search_integration import adapter_args


def problem(size=1000, backend='scan'):
    protocol = SearchProtocol('scope', 'code', (SearchExperiment('a', 'read'),),
        tuple((str(i),) for i in range(size)),
        (ResponseConstraint('all', tuple(range(size))), ResponseConstraint('first', (0,))))
    hypotheses = (SearchHypothesis('zero', 0, 'yes', DescriptionLength()),
                  SearchHypothesis('one', 1, 'no', DescriptionLength()))
    return ExperimentHypothesisSearch(protocol, hypotheses, 'out', backend=backend)


class BooleanWorkBoundaries(unittest.TestCase):
    def test_consistent_conflict_stops_at_existing_witness(self):
        p = problem()
        budget = SearchWorkBudget()
        self.assertIsNone(p.learn_conflict(('all',), (SearchObservation('a', '0', 'lab'),),
                                          budget=budget))
        self.assertLessEqual(budget.work.total, 20)

    def test_conflict_verification_exhausts_only_absence_claim(self):
        p = problem()
        data = (SearchObservation('a', '1', 'lab'),)
        certificate = p.learn_conflict(('first',), data)
        self.assertIsNotNone(certificate)
        budget = SearchWorkBudget()
        self.assertTrue(p.validates_conflict(certificate, data, budget=budget))
        self.assertLessEqual(budget.work.total, 2020)

    def test_existence_and_every_cutoff_match_declared_rows(self):
        p = problem(4)
        for commitments in ((), ('all',), ('first',)):
            for value in ('0', '1'):
                data = (SearchObservation('a', value, 'lab'),)
                allowed = set(range(4)) if 'first' not in commitments else {0}
                expected = any(i in allowed and row == (value,)
                               for i, row in enumerate(p.protocol.worlds))
                full = SearchWorkBudget()
                self.assertIs(p._has_world(commitments, data, budget=full), expected)
                for limit in range(full.work.total):
                    budget = SearchWorkBudget(limit)
                    with self.assertRaises(SearchBudgetExceeded):
                        p._has_world(commitments, data, budget=budget)
                    self.assertLessEqual(budget.work.total, limit)
        with self.assertRaises(SearchBudgetExceeded):
            p._has_world(budget=SearchWorkBudget(cancelled=lambda: True))
        data = (SearchObservation('a', '1', 'lab'),)
        certificate = p.learn_conflict(('first',), data)
        for operation in (lambda budget: p.learn_conflict(('first',), data, budget=budget),
                          lambda budget: p.validates_conflict(certificate, data, budget=budget)):
            full = SearchWorkBudget()
            operation(full)
            for limit in range(full.work.total):
                with self.assertRaises(SearchBudgetExceeded):
                    operation(SearchWorkBudget(limit))


class AbsenceWorkBoundaries(unittest.TestCase):
    def test_absence_replay_needs_no_new_catalogue(self):
        for backend in ('scan', 'indexed'):
            p = problem(4, backend)
            for query in (
                ConstraintQuery(('first',), (SearchObservation('a', '1', 'lab'),)),
                MacroAlternativeQuery('yes', (SearchObservation('a', '0', 'lab'),)),
                LowerSubstituteQuery('zero', ('one',)),
            ):
                receipt = p.query(query)
                original = ExperimentHypothesisSearch.__init__
                with patch.object(ExperimentHypothesisSearch, '__init__', autospec=True,
                                  side_effect=original) as calls:
                    verified = verify_query_result(p, query, receipt)
                self.assertEqual(verified.status, 'valid')
                self.assertEqual(calls.call_count, 0)

    def test_absence_replay_ignores_faulty_index(self):
        p = problem(4, 'indexed')
        query = ConstraintQuery(evidence=(SearchObservation('a', '0', 'lab'),))
        with patch.object(ResponseIndex, 'filter', return_value=WorldMask(0)):
            receipt = p.query(query)
            self.assertEqual(verify_query_result(p, query, receipt).status, 'invalid')
        query = ConstraintQuery(('first',), (SearchObservation('a', '1', 'lab'),))
        receipt = p.query(query)
        with patch.object(ResponseIndex, 'allows', return_value=False):
            try:
                verified = verify_query_result(p, query, receipt)
            except ValueError as error:
                self.fail('absence replay trusted indexed evidence validation: ' + str(error))
        self.assertEqual(verified.status, 'valid')


class PromotionWorkBoundaries(unittest.TestCase):
    def test_screening_consistency_stops_at_first_response_witness(self):
        protocol, candidates, cases = adapter_args()
        protocol = replace(protocol, worlds=tuple((str(i),) for i in range(1000)))
        lazy = LazyExecutableSearch(protocol, candidates[:1], cases, target='out',
                                    world_answers=('yes',) * 1000)
        budget = SearchWorkBudget(20)
        report = lazy.screen_evidence((SearchObservation('read', '0', 'lab'),), budget=budget)
        self.assertEqual(report.matching_evidence, ('zero',))
        self.assertEqual(report.undecided, ())
        self.assertEqual(report.reason, 'completed')

    def test_single_candidate_uses_only_input_and_output_catalogues(self):
        protocol, candidates, cases = adapter_args()
        original = ExperimentHypothesisSearch.__init__
        with patch.object(ExperimentHypothesisSearch, '__init__', autospec=True,
                          side_effect=original) as calls:
            result = ExecutableSearchAdapter().prepare(protocol, candidates[:1], cases,
                target='out', world_answers=('yes', 'no'))
        self.assertEqual(result.search.hypotheses[0].world, 0)
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(calls.call_count, 2)
        restricted = replace(protocol, constraints=(ResponseConstraint('one', (1,)),))
        candidate = replace(candidates[0], commitments=('one',))
        rejected = ExecutableSearchAdapter().prepare(restricted, (candidate,), cases,
                                                    target='out', world_answers=('yes', 'no'))
        self.assertIsNone(rejected.search.hypotheses[0].world)
        self.assertIn(('zero', 'ValueError', 'prediction violates a declared commitment'),
                      rejected.diagnostics)


class TranslationDeclarationBoundaries(unittest.TestCase):
    def test_translation_copy_and_replacement_follow_declaration(self):
        source = SearchProtocol('old', 'code', (SearchExperiment('a', 'a'),), (('0',), ('1',)))
        target = SearchProtocol('new', 'code', (SearchExperiment('b', 'b'),), (('low',), ('high',)))
        old = ModelingContext('old', source, 'lab', 'exact', ('x',), 'finite', 'out')
        new = ModelingContext('new', target, 'lab', 'exact', ('x',), 'finite', 'out')
        transition = ContextTransition(old, new, ContextChange.RECONSTRUCTION,
            (('a', 'b'),), (('a', 'low', '0'), ('a', 'high', '1')))
        for clone in (transition, copy.deepcopy(transition), pickle.loads(pickle.dumps(transition))):
            with self.assertRaises(TypeError):
                clone._response_lookup['a', 'low'] = '1'
            self.assertEqual(clone.translate_response('a', 'low'), '0')
            self.assertEqual(clone.translate_response('a', 'high'), '1')
            self.assertEqual(clone.fingerprint, transition.fingerprint)
            self.assertEqual(validate_context_transition(clone).status, 'valid')
            with self.assertRaises(ValueError): clone.translate_response('a', 'missing')
        swapped = replace(transition, responses=(('a', 'low', '1'), ('a', 'high', '0')))
        self.assertEqual(swapped.translate_response('a', 'low'), '1')
        self.assertNotEqual(swapped.fingerprint, transition.fingerprint)
        self.assertEqual(set(asdict(transition)),
                         {'source', 'target', 'kind', 'experiments', 'responses', 'commitments'})
