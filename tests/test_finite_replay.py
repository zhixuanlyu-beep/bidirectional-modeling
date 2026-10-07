"""C-REPLAY/C-UNDECIDED: table oracle, durable data and bounded revalidation."""
import json
import os
from pathlib import Path
from itertools import product
from dataclasses import replace
import subprocess
import runpy
import sys
import unittest

from bidirectional_modeling import Context, Intervention, ResourceBudget, ScenarioKey, SearchWorkBudget
from bidirectional_modeling.core import UndefinedTransition
from bidirectional_modeling.evaluation import SatisfactionEvaluator
from bidirectional_modeling.finite_replay import (
    DeclaredFiniteModel, FiniteModelDeclaration, create_replay_bundle, replay_bundle,
)
from bidirectional_modeling.search import SearchBudgetExceeded
from bidirectional_modeling.structural import fingerprint_value
from examples.approval_workflow import run_case, workflow_inputs


def reseal(document):
    document['checksum'] = fingerprint_value(document['payload'])
    return json.dumps(document)


class FiniteReplayContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report, cls.bundle = run_case()

    def test_all_small_partial_tables_match_plain_state_machine(self):
        states, actions = ('0', '1'), ('noop', 'flip')
        pairs = tuple(product(states, actions))
        paths = tuple(path for length in range(4) for path in product(actions, repeat=length))
        for successors in product((None,) + states, repeat=len(pairs)):
            declaration = FiniteModelDeclaration('table', states, ('0',), actions,
                tuple((s, a, target) for (s, a), target in zip(pairs, successors)),
                tuple((state, (('y', state),)) for state in states))
            model = DeclaredFiniteModel(declaration)
            for path in paths:
                state, expected, undefined = '0', ['0'], False
                for action in path:
                    state = successors[pairs.index((state, action))]
                    if state is None:
                        undefined = True
                        break
                    expected.append(state)
                context = Context(include_baseline=False,
                    interventions=(Intervention('path', path, repeat_last=False),))
                if undefined:
                    with self.assertRaises(UndefinedTransition): tuple(model.simulate(context, len(path)))
                else:
                    trace, = model.simulate(context, len(path))
                    self.assertEqual([snapshot['y'] for snapshot in trace.snapshots], expected)

    def test_table_inputs_are_snapshotted_and_invalid_domains_fail(self):
        _, candidates, _, _, _ = workflow_inputs()
        declaration = candidates[0].model.declaration
        raw = declaration.to_dict()
        restored = FiniteModelDeclaration(**raw)
        raw['transitions'][0][2] = 'released'
        self.assertEqual(restored, declaration)
        self.assertEqual(restored.fingerprint, declaration.fingerprint)
        invalid = (
            dict(schema_version=True), dict(schema_version=99), dict(states=()), dict(states=('s', 's')),
            dict(states=frozenset(('s',))), dict(initial_states=('missing',)), dict(actions=('release',)),
            dict(transitions=declaration.transitions[:-1]), dict(transitions=(('pending', 'noop'),)),
            dict(transitions=declaration.transitions + (declaration.transitions[0],)),
            dict(transitions=(('pending', 'bad', 'pending'),) + declaration.transitions[1:]),
            dict(transitions=(('pending', 'noop', 'bad'),) + declaration.transitions[1:]),
            dict(readouts=declaration.readouts[:-1]), dict(readouts=(('pending', ()),) + declaration.readouts[1:]),
            dict(readouts=(('pending', (('x', 1), ('x', 2))),) + declaration.readouts[1:]),
            dict(readouts=(('pending', (('x', 1.0),)),) + declaration.readouts[1:]),
            dict(resources=(1, 2)), dict(resources=(True, 2, 3)), dict(name=''),
        )
        for change in invalid:
            with self.subTest(change=change), self.assertRaises((TypeError, ValueError)):
                replace(declaration, **change)

    def test_manifest_and_undefined_transitions_preserve_unknown(self):
        _, candidates, cases, _, _ = workflow_inputs()
        model = candidates[0].model
        evaluator = SatisfactionEvaluator()
        unbound = evaluator.collect(model, Context(), 1, ResourceBudget())
        self.assertFalse(unbound.complete)
        case = cases[0]
        complete = evaluator.collect(model, case.context, case.horizon, ResourceBudget())
        self.assertTrue(complete.complete)
        declaration = replace(model.declaration, transitions=tuple(
            (s, a, None if a == 'release' else target) for s, a, target in model.declaration.transitions))
        undefined = evaluator.collect(DeclaredFiniteModel(declaration), case.context, 1, ResourceBudget())
        self.assertFalse(undefined.complete)
        self.assertTrue(undefined.diagnostics)
        for context in (Context(), Context(include_baseline=False)):
            trace, = model.simulate(context, 1)
            self.assertEqual(trace.snapshots[-1]['status'], 'pending')

    def test_lifecycle_restores_candidate_after_withdrawal(self):
        phases = self.report['phases']
        self.assertTrue(phases[1]['determined'])
        self.assertFalse(phases[2]['determined'])
        self.assertEqual(set(phases[2]['compatible']), {'guarded', 'sticky-0'})
        self.assertEqual(phases[3]['compatible'], ('guarded',))
        self.assertEqual(self.report['revoked_certificates'], 1)
        self.assertIsNone(self.report['human_evaluation']['counterexample_understood'])

    def test_replay_in_new_process_with_different_hash_seeds(self):
        script = 'import sys,json; from bidirectional_modeling.finite_replay import replay_bundle; r=replay_bundle(sys.stdin.read()); print(json.dumps([r.status,r.session.search.fingerprint,r.report.compatible,r.simulations_used]))'
        expected = None
        for seed in ('1', '7', '123'):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run([sys.executable, '-c', script], input=self.bundle,
                text=True, capture_output=True, check=True, env=env)
            current = json.loads(result.stdout)
            self.assertEqual(current[0], 'verified')
            if expected is not None: self.assertEqual(current, expected)
            expected = current

    def test_versioned_fixture_and_package_version_is_advisory(self):
        fixture = Path(__file__).parent / 'fixtures' / 'approval_replay_v1.json'
        replay = replay_bundle(fixture.read_text(encoding='utf-8'))
        self.assertEqual(replay.status, 'verified')
        self.assertEqual(replay.report.compatible, ('guarded',))
        document = json.loads(self.bundle)
        document['payload']['producer_version'] = 'future-reader-compatibility-test'
        self.assertEqual(replay_bundle(reseal(document)).status, 'verified')

    def test_corruption_and_unknown_versions_require_regeneration(self):
        document = json.loads(self.bundle)
        document['payload']['target'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'checksum'): replay_bundle(json.dumps(document))
        for key, value in (('schema_version', 99), ('schema_version', True),
                           ('session_schema', 1), ('identity_domains', {})):
            document = json.loads(self.bundle)
            document['payload'][key] = value
            with self.assertRaisesRegex(ValueError, 'unsupported replay'): replay_bundle(reseal(document))
        with self.assertRaisesRegex(ValueError, 'input limit'): replay_bundle(self.bundle, max_bytes=1)

    def test_declaration_and_provenance_tampering_is_not_accepted(self):
        for mutation in ('readout', 'declaration', 'preparation', 'prepared_binding'):
            document = json.loads(self.bundle)
            p = document['payload']
            if mutation == 'readout':
                p['candidates'][0]['model']['readouts'][0][1][0][1] = 'released'
            elif mutation == 'declaration':
                p['candidates'][0]['model']['resources'][0] = 50
            elif mutation == 'preparation':
                p['preparation']['simulations_used'] = 0
            else:
                p['session']['payload']['prepared_problem_fingerprint'] = '0' * 64
                p['session']['checksum'] = fingerprint_value(p['session']['payload'])
            self.assertEqual(replay_bundle(reseal(document)).status, 'invalid')

    def test_bounded_replay_does_not_promote_unknown(self):
        protocol, candidates, cases, answers, evidence = workflow_inputs()
        bundle = create_replay_bundle(protocol, candidates, cases, target='safety',
            world_answers=answers, evidence=evidence, max_simulations=0)
        replay = replay_bundle(bundle)
        self.assertEqual(replay.status, 'undecided')
        self.assertTrue(replay.report.undecided)
        document = json.loads(self.bundle)
        document['payload']['budgets']['max_operations'] = 0
        self.assertEqual(replay_bundle(reseal(document)).status, 'undecided')
        with self.assertRaises(SearchBudgetExceeded):
            create_replay_bundle(protocol, candidates, cases, target='safety',
                world_answers=answers, evidence=evidence, max_operations=0)

    def test_external_callbacks_and_contexts_stay_outside_v1(self):
        from bidirectional_modeling.extensions.boolean import BooleanExpression
        protocol, candidates, cases, answers, evidence = workflow_inputs()
        for candidate in (replace(candidates[0], model=BooleanExpression(('false',)).to_model('callback')),):
            with self.assertRaisesRegex(TypeError, 'DeclaredFiniteModel'):
                create_replay_bundle(protocol, (candidate,), cases, target='safety', world_answers=answers)
        for context in (replace(cases[0].context, environment={'external': 'config'}),
                        replace(cases[0].context, scenario_manifest=()),
                        replace(cases[0].context, include_baseline=1),
                        replace(cases[0].context, interventions=(Intervention('path', ('release',), repeat_last=1),))):
            with self.assertRaises(ValueError):
                create_replay_bundle(protocol, candidates, (replace(cases[0], context=context),) + cases[1:],
                    target='safety', world_answers=answers, evidence=evidence)

    def test_lifecycle_costs_record_partial_updates_as_undecided(self):
        module = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'benchmarks/lifecycle_costs.py'))
        for limit in (0, 40, 130, 270):
            result = module['run'](copies=(1,), worlds=(8,), max_operations=limit)
            for row in result['results']:
                if row['kind'] == 'workflow-lifecycle' and 'updates' in row:
                    interrupted = any(item['undecided'] or item['stop_reason'] == 'work_budget_exhausted'
                                      for item in row['updates'])
                    if interrupted:
                        self.assertEqual(row['status'], 'undecided')
                if limit == 0:
                    self.assertIn(row['status'], ('unknown', 'undecided'))
