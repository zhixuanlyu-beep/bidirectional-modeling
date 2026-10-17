"""Expanded evidence must retain a witness; reviewers own execution limits."""
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from bidirectional_modeling import computation_search as cs
from bidirectional_modeling.extensions import local_theories as lt
from bidirectional_modeling.macro_certificates import (
    certify_macro_sufficiency, verify_macro_sufficiency,
)
from bidirectional_modeling.search import (
    DescriptionLength, ExperimentHypothesisSearch, SearchExperiment,
    SearchHypothesis, SearchObservation, SearchProtocol, SearchWorkBudget,
)
from bidirectional_modeling.search_examples import conflict_search_scenario
from bidirectional_modeling.versions import CORE_CONTRACT_REVISION


class ReviewHardening(unittest.TestCase):
    def test_new_evidence_cannot_leave_an_empty_space_with_valid_sufficiency(self):
        problem, evidence = conflict_search_scenario()
        certificate = certify_macro_sufficiency(problem, evidence).certificate
        active = certificate.evidence + (SearchObservation('00', '1', 'new-lab'),)
        self.assertEqual(problem.search(active).compatible, ())
        self.assertEqual(verify_macro_sufficiency(problem, certificate, active), 'invalid')
        # A compact-only review remains scoped to precisely those supplied records.
        self.assertEqual(verify_macro_sufficiency(problem, certificate, certificate.evidence), 'valid')
        self.assertEqual(verify_macro_sufficiency(problem, certificate, evidence), 'valid')
        # Every cutoff either finds the real problem or remains undecided.
        for limit in range(70):
            result = verify_macro_sufficiency(problem, certificate, active,
                                             budget=SearchWorkBudget(limit))
            self.assertIn(result, ('invalid', 'undecided'))

    def test_all_small_evidence_expansions_against_witness_reference(self):
        worlds = tuple(product(('0', '1'), repeat=2))
        protocol = SearchProtocol('small evidence fixture', 'finite code fixture',
            (SearchExperiment('x', 'first'), SearchExperiment('y', 'second')), worlds, ())
        hypotheses = tuple(SearchHypothesis('h%d' % i, i, row[0], DescriptionLength(relations=1))
                           for i, row in enumerate(worlds))
        problem = ExperimentHypothesisSearch(protocol, hypotheses, 'first response')
        initial = (SearchObservation('x', '0', 'fixture'),)
        certificate = certify_macro_sufficiency(problem, initial).certificate
        witness = next(h for h in hypotheses if h.name == certificate.witness_candidate)
        for extras in product(('0', '1'), repeat=2):
            active = initial + tuple(SearchObservation(e.name, value, 'new')
                                     for e, value in zip(protocol.experiments, extras))
            reference = all(worlds[witness.world][('x', 'y').index(o.experiment)] == o.response
                            for o in active)
            self.assertEqual(verify_macro_sufficiency(problem, certificate, active) == 'valid', reference)
            # If a different same-answer candidate survives, regenerate its positive witness.
            regenerated = certify_macro_sufficiency(problem, active)
            if regenerated.status == 'verified':
                self.assertEqual(verify_macro_sufficiency(problem, regenerated.certificate, active), 'valid')

    def test_computation_review_owns_the_run_ceiling(self):
        task = cs.FiniteBitTask('nullary runtime fixture', 0, (0,))
        good = cs.search_computations(task, max_cost=4).certificate
        for ceiling in range(6):
            review = cs.verify_computation_certificate(task, (), good, max_cost=ceiling)
            self.assertEqual(review.status, 'valid' if ceiling >= 4 else 'undecided')
        loop = replace(good, program=cs.BitProgram(('again',), (0,)), cost=100)
        review = cs.verify_computation_certificate(task, (), loop, max_trials=1, max_cost=23)
        self.assertEqual((review.status, review.optimality), ('undecided', 'undecided'))
        forged = replace(loop, cost=10 ** 100)
        seen = []
        def limited(program, inputs, *, max_cost):
            seen.append(max_cost)
            return cs.ComputationRun('resource_limit', None, 0, 0, 0)
        with patch.object(cs, 'run_bit_program', side_effect=limited):
            self.assertEqual(cs.verify_computation_certificate(task, (), forged).status, 'undecided')
        self.assertEqual(seen, [cs.DEFAULT_REVIEW_COST])
        # An inflated cost for a terminating program is still invalid, even above the ceiling.
        self.assertEqual(cs.verify_computation_certificate(task, (), replace(good, cost=10 ** 100),
                                                          max_cost=4).status, 'invalid')

    def test_local_review_caps_both_local_and_extracted_programs(self):
        task = cs.FiniteBitTask('nullary local fixture', 0, (0,))
        program = cs.BitProgram(('halt',), (0,))
        local = lt.LocalBitTheory('zero', task.scope, 0, (cs.BitEvidence((), 0, 'fixture'),), program)
        cert = lt.certify_local_cover(task, (local,), program, max_cost=4).certificate
        for ceiling in range(6):
            self.assertEqual(lt.verify_local_cover(task, (local,), cert, max_cost=ceiling),
                             'valid' if ceiling >= 4 else 'undecided')
        looping = replace(local, witness=cs.BitProgram(('again',), (0,)))
        forged = replace(cert, theories_fingerprint=lt._binding((looping,)),
                         local_costs=(('zero', 10 ** 100),))
        self.assertEqual(lt.verify_local_cover(task, (looping,), replace(forged, local_costs=(('zero', 100),)),
            max_cost=23, budget=SearchWorkBudget(1)), 'undecided')
        seen = []
        real_run = cs.run_bit_program
        def limited(program, inputs, *, max_cost):
            seen.append(max_cost)
            if max_cost > 4:
                return cs.ComputationRun('resource_limit', None, 0, 0, 0)
            return real_run(program, inputs, max_cost=max_cost)
        with patch.object(lt, 'run_bit_program', side_effect=limited):
            self.assertEqual(lt.verify_local_cover(task, (looping,), forged), 'undecided')
        self.assertEqual(seen, [cs.DEFAULT_REVIEW_COST])
        seen.clear()
        forged_extraction = replace(cert, program=looping.witness, cost=10 ** 100)
        with patch.object(lt, 'run_bit_program', side_effect=limited):
            self.assertEqual(lt.verify_local_cover(task, (local,), forged_extraction, max_cost=23), 'undecided')
        self.assertEqual(seen, [4, 23])
        self.assertEqual(lt.verify_local_cover(task, (local,), replace(cert, cost=10 ** 100),
                                             max_cost=4), 'invalid')

    def test_review_ceilings_reject_boolean_and_negative_limits(self):
        task = cs.FiniteBitTask('strict ceiling fixture', 0, (0,))
        for value in (True, False, -1, None, 3.5, '10'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    cs.verify_computation_certificate(task, (), None, max_cost=value)
                with self.assertRaises(ValueError):
                    lt.verify_local_cover(task, (), None, max_cost=value)

    def test_replay_limits_are_independent_of_archive_requests(self):
        from bidirectional_modeling.finite_replay import replay_bundle
        from bidirectional_modeling.structural import fingerprint_value
        root = Path(__file__).resolve().parents[1]
        text = (root / 'tests/fixtures/approval_replay_v1.json').read_text()
        original = json.loads(text)
        limits = original['payload']['budgets']
        # Exact requested limits remain replayable; lowering either one is unresolved.
        self.assertEqual(replay_bundle(text, **limits).status, 'verified')
        for key in ('max_simulations', 'max_operations'):
            result = replay_bundle(text, **{key: limits[key] - 1})
            self.assertEqual((result.status, result.reason), ('undecided', 'review_resource_limit'))
        horizon = max(c['horizon'] for c in original['payload']['cases'])
        self.assertEqual(replay_bundle(text, max_horizon=horizon).status, 'verified')
        self.assertEqual(replay_bundle(text, max_horizon=horizon - 1).reason, 'review_horizon_limit')
        changes = (('max_simulations', 'review_resource_limit'),
                   ('max_operations', 'review_resource_limit'), ('horizon', 'review_horizon_limit'))
        for key, reason in changes:
            document = json.loads(text)
            if key == 'horizon':
                document['payload']['cases'][0]['horizon'] = 10 ** 100
            else:
                document['payload']['budgets'][key] = 10 ** 100
            document['checksum'] = fingerprint_value(document['payload'])
            # The guard must run before construction, adaptation or archived execution.
            with patch('bidirectional_modeling.finite_replay.SearchSession.from_json') as restore, \
                 patch('bidirectional_modeling.finite_replay.ExecutableSearchAdapter.prepare') as prepare:
                result = replay_bundle(json.dumps(document))
            self.assertEqual((result.status, result.reason), ('undecided', reason))
            restore.assert_not_called()
            prepare.assert_not_called()

    def test_replay_reviewer_limits_have_strict_types(self):
        from bidirectional_modeling.finite_replay import replay_bundle
        text = (Path(__file__).resolve().parents[1] /
                'tests/fixtures/approval_replay_v1.json').read_text()
        for name in ('max_simulations', 'max_operations', 'max_horizon'):
            for value in (True, -1, None, 2.5, '10'):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    replay_bundle(text, **{name: value})

    def test_contract_metadata_matches_the_reviewed_manifest(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / 'docs/core_contracts.json').read_text())
        self.assertEqual(CORE_CONTRACT_REVISION, manifest['revision'])
