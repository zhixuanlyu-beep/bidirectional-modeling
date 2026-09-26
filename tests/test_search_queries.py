import unittest
from dataclasses import replace
from itertools import combinations, product

from bidirectional_modeling import (
    ConstraintQuery, DescriptionLength, ExperimentHypothesisSearch,
    FiniteSearchQueryBackend, LowerSubstituteQuery, MacroAlternativeQuery,
    QueryStatus, ResponseConstraint, SearchExperiment, SearchHypothesis,
    SearchObservation, SearchProtocol, SearchWorkBudget, query_fingerprint,
    verify_query_result,
)


def problem(backend='scan', allowed=(0, 1, 2, 3)):
    protocol = SearchProtocol('scope', 'coding',
        (SearchExperiment('a', 'a'), SearchExperiment('b', 'b')),
        tuple(product(('0', '1'), repeat=2)), (ResponseConstraint('c', allowed),))
    candidates = tuple(SearchHypothesis(str(i), i, 'yes' if i == 0 else 'no',
        DescriptionLength(relations=i)) for i in range(4))
    return ExperimentHypothesisSearch(protocol, candidates, 'target', backend=backend)


class QueryTests(unittest.TestCase):
    def test_exhaustive_queries_match_independent_truth_table(self):
        for mask in range(16):
            allowed = tuple(i for i in range(4) if mask & (1 << i))
            scan, indexed = problem(allowed=allowed), problem('indexed', allowed)
            for row in scan.protocol.worlds:
                data = tuple(SearchObservation(e, r, 'lab') for e, r in zip(('a', 'b'), row))
                for size in range(3):
                    for evidence in combinations(data, size):
                        fits = {i for i, w in enumerate(scan.protocol.worlds)
                                if all(w[('a', 'b').index(o.experiment)] == o.response for o in evidence)}
                        for query, expected in (
                            (ConstraintQuery(('c',), evidence), bool(fits & set(allowed))),
                            (MacroAlternativeQuery('yes', evidence), bool(fits - {0})),
                        ):
                            results = [p.query(query) for p in (scan, indexed)]
                            for p, receipt in zip((scan, indexed), results):
                                self.assertEqual(receipt.status, QueryStatus.FOUND if expected else QueryStatus.ABSENT)
                                self.assertEqual(verify_query_result(p, query, receipt).status, 'valid')
                            self.assertEqual(replace(results[0], work=results[1].work), results[1])

    def test_macro_absence_distinguishes_empty_catalogue(self):
        p = problem()
        evidence = (SearchObservation('a', '0', 'lab'), SearchObservation('b', '0', 'lab'))
        q = MacroAlternativeQuery('yes', evidence)
        receipt = p.query(q)
        self.assertIs(receipt.status, QueryStatus.ABSENT)
        self.assertIs(receipt.compatible_catalogue_nonempty, True)
        q = MacroAlternativeQuery('yes', evidence + (SearchObservation('a', '1', 'lab'),))
        receipt = p.query(q)
        self.assertIs(receipt.status, QueryStatus.ABSENT)
        self.assertIs(receipt.compatible_catalogue_nonempty, False)
        self.assertEqual(verify_query_result(p, q, receipt).status, 'valid')
        self.assertEqual(verify_query_result(p, q, replace(receipt, compatible_catalogue_nonempty=0)).status, 'invalid')

    def test_unknown_prediction_does_not_hide_later_witness(self):
        p = problem()
        unknown = SearchHypothesis('u', None, 'yes', DescriptionLength())
        p = p.with_hypotheses((unknown,) + p.hypotheses)
        self.assertIs(p.query(MacroAlternativeQuery('yes')).status, QueryStatus.FOUND)
        evidence = (SearchObservation('a', '0', 'lab'), SearchObservation('b', '0', 'lab'))
        self.assertIs(p.query(MacroAlternativeQuery('yes', evidence)).status, QueryStatus.UNKNOWN)
        self.assertIs(p.query(LowerSubstituteQuery('0', ('u', '0'))).status, QueryStatus.FOUND)
        self.assertIs(p.query(LowerSubstituteQuery('0', ('u',))).status, QueryStatus.UNKNOWN)
        self.assertIs(p.query(LowerSubstituteQuery('u', ('0',))).status, QueryStatus.UNKNOWN)

    def test_lower_catalogue_is_explicit_and_finite(self):
        p = problem()
        alias = replace(p.hypotheses[0], name='alias')
        p = p.with_hypotheses(p.hypotheses + (alias,))
        for names, expected in ((('alias',), QueryStatus.FOUND), (('1', '2'), QueryStatus.ABSENT), ((), QueryStatus.UNKNOWN)):
            q = LowerSubstituteQuery('0', names)
            r = p.query(q)
            self.assertIs(r.status, expected)
            self.assertEqual(verify_query_result(p, q, r).status, 'undecided' if expected is QueryStatus.UNKNOWN else 'valid')

    def test_budget_and_cancellation_never_prove_absence(self):
        for backend in ('scan', 'indexed'):
            p = problem(backend)
            for q in (ConstraintQuery(), MacroAlternativeQuery('yes'), LowerSubstituteQuery('0', ('1',))):
                for budget in (SearchWorkBudget(0), SearchWorkBudget(cancelled=lambda: True)):
                    r = p.query(q, budget=budget)
                    self.assertIs(r.status, QueryStatus.UNKNOWN)
                    self.assertEqual(verify_query_result(p, q, r).status, 'undecided')
                r = p.query(q)
                self.assertEqual(verify_query_result(p, q, r, budget=SearchWorkBudget(0)).status, 'undecided')

    def test_partial_work_at_every_cutoff_is_conservative(self):
        for backend in ('scan', 'indexed'):
            q = MacroAlternativeQuery('no', (SearchObservation('a', '1', 'lab'),))
            full = problem(backend).query(q)
            for limit in range(full.work.total + 1):
                p = problem(backend)
                r = p.query(q, budget=SearchWorkBudget(limit))
                self.assertLessEqual(r.work.total, limit)
                self.assertIn(r.status, (QueryStatus.UNKNOWN, full.status))
                if r.status is not QueryStatus.UNKNOWN:
                    self.assertEqual(verify_query_result(p, q, r).status, 'valid')

    def test_receipts_bind_query_and_problem(self):
        p = problem()
        q = ConstraintQuery(evidence=(SearchObservation('a', '0', 'lab'),))
        r = p.query(q)
        changed = ConstraintQuery(evidence=(SearchObservation('a', '0', 'other lab'),))
        self.assertEqual(verify_query_result(p, changed, r).reason, 'binding_mismatch')
        p2 = p.with_hypotheses(p.hypotheses[:-1])
        self.assertEqual(verify_query_result(p2, q, r).reason, 'binding_mismatch')
        for forged in (replace(r, scope='all_models'), replace(r, status='found'),
                       replace(r, witness_world=True), replace(r, witness_world=9),
                       replace(r, witness_world=3), replace(r, witness_candidate='0'),
                       replace(r, compatible_catalogue_nonempty=True)):
            self.assertEqual(verify_query_result(p, q, forged).status, 'invalid')
        # Any valid witness is accepted, not just the backend's first witness.
        self.assertEqual(verify_query_result(p, q, replace(r, witness_world=1)).status, 'valid')

    def test_external_absence_and_forged_candidates_are_rejected(self):
        p = problem()
        q = MacroAlternativeQuery('yes')
        r = FiniteSearchQueryBackend().execute(p, q)
        for forged in (replace(r, status=QueryStatus.ABSENT),
                       replace(r, status=QueryStatus.ABSENT, witness_world=None, witness_candidate=None),
                       replace(r, witness_candidate='missing'),
                       replace(r, witness_world=0, witness_candidate='0'),
                       replace(r, compatible_catalogue_nonempty=False)):
            self.assertEqual(verify_query_result(p, q, forged).status, 'invalid')
        q = LowerSubstituteQuery('0', ('1',))
        r = p.query(q)
        forged = replace(r, status=QueryStatus.FOUND, witness_candidate='1', witness_world=1)
        self.assertEqual(verify_query_result(p, q, forged).status, 'invalid')

    def test_input_validation(self):
        p = problem()
        for q in (ConstraintQuery(('missing',)), LowerSubstituteQuery('missing', ('0',)), LowerSubstituteQuery('0', ('missing',))):
            with self.assertRaises(ValueError): p.query(q)
            r = replace(p.query(ConstraintQuery()), query_fingerprint=query_fingerprint(p, q))
            with self.assertRaises(ValueError): verify_query_result(p, q, r)
        with self.assertRaises(ValueError): LowerSubstituteQuery('0', ('1', '1'))
        with self.assertRaises(TypeError): p.query(object())
        for e in (SearchObservation('missing', '0', 'lab'), SearchObservation('a', 'outside', 'lab')):
            with self.assertRaises(ValueError): p.query(ConstraintQuery(evidence=(e,)))
