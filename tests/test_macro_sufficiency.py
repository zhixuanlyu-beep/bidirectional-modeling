import unittest
from dataclasses import replace

from bidirectional_modeling import (certify_macro_sufficiency, verify_macro_sufficiency,
                                    SearchWorkBudget, SearchObservation)
from bidirectional_modeling.search_examples import conflict_search_scenario


class MacroSufficiencyTests(unittest.TestCase):
    def test_compact_certificate_requires_only_retained_live_evidence(self):
        p, data = conflict_search_scenario()
        result = certify_macro_sufficiency(p, data)
        self.assertEqual(result.status, 'verified')
        c = result.certificate
        self.assertLess(len(c.evidence), len(data))
        self.assertEqual(result.minimality, 'not_claimed')
        self.assertEqual(verify_macro_sufficiency(p, c, c.evidence), 'valid')
        self.assertEqual(verify_macro_sufficiency(p, c, ()), 'invalid')
        self.assertEqual(verify_macro_sufficiency(p, replace(c, exclusions=()), c.evidence), 'invalid')
        self.assertEqual(verify_macro_sufficiency(p, replace(c, answer='forged'), c.evidence), 'invalid')
        self.assertEqual(verify_macro_sufficiency(p, replace(c, witness_candidate='absent'), c.evidence), 'invalid')
        self.assertEqual(verify_macro_sufficiency(p, c, c.evidence, budget=SearchWorkBudget(0)), 'undecided')
        changed = p.with_hypotheses(p.hypotheses[:-1])
        self.assertEqual(verify_macro_sufficiency(changed, c, c.evidence), 'invalid')

    def test_unknown_empty_and_multiple_answers_do_not_prove_sufficiency(self):
        p, data = conflict_search_scenario()
        unknown = p.with_hypotheses((replace(p.hypotheses[0], world=None),))
        self.assertEqual(certify_macro_sufficiency(unknown, data).reason, 'unknown_prediction')
        self.assertEqual(certify_macro_sufficiency(p.with_hypotheses(()), data).reason, 'empty_version_space')
        self.assertEqual(certify_macro_sufficiency(p, ()).reason, 'multiple_answers')
        self.assertEqual(certify_macro_sufficiency(p, data, budget=SearchWorkBudget(0)).status, 'undecided')

    def test_weighted_cover_is_sufficient_without_an_optimality_claim(self):
        p, data = conflict_search_scenario()
        costs = {o: i+1 for i, o in enumerate(data)}
        r = certify_macro_sufficiency(p, data, read_costs=costs)
        self.assertEqual(r.retained_cost, sum(costs[o] for o in r.certificate.evidence))
        self.assertEqual(verify_macro_sufficiency(p, r.certificate, r.certificate.evidence), 'valid')
        with self.assertRaises(ValueError):
            certify_macro_sufficiency(p, data, read_costs={data[0]: 0})
        with self.assertRaises(ValueError):
            certify_macro_sufficiency(p, data, read_costs={SearchObservation('absent', '0', 'lab'): 1})

    def test_evidence_provenance_is_bound(self):
        p, data = conflict_search_scenario()
        c = certify_macro_sufficiency(p, data).certificate
        changed = tuple(replace(o, source='different') for o in c.evidence)
        self.assertEqual(verify_macro_sufficiency(p, c, changed), 'invalid')
        for cap in range(50):
            r = certify_macro_sufficiency(p, data, budget=SearchWorkBudget(cap))
            if r.status == 'verified':
                self.assertEqual(verify_macro_sufficiency(p, r.certificate, r.certificate.evidence), 'valid')
            else:
                self.assertIsNone(r.certificate)
