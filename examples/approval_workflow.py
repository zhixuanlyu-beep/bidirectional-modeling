"""Bounded approval-workflow evaluation; observations are explicit test fixtures.

Run: PYTHONPATH=src python examples/approval_workflow.py --output-dir /tmp/approval
Replace fixture observations with reviewed operational records for a field study.
"""
import argparse
import json
from itertools import product
from pathlib import Path
from time import perf_counter

from bidirectional_modeling import (Context, DescriptionLength, Intervention, ResponseConstraint,
    ScenarioKey, SearchExperiment, SearchObservation, SearchProtocol)
from bidirectional_modeling.finite_replay import (
    DeclaredFiniteModel, FiniteModelDeclaration, create_replay_bundle, replay_bundle,
)
from bidirectional_modeling.search_adapter import ExecutableSearchAdapter, ModelSearchCandidate, ModelSearchCase
from bidirectional_modeling.search_explanations import explain_conflict, verify_conflict_explanation
from bidirectional_modeling.search_session import SearchSession


def workflow_inputs(copies=1):
    states = ('pending', 'approved', 'released')
    actions = ('noop', 'approve', 'revoke', 'release')
    candidates = []
    definitions = [('guarded', False, False, ()) ,
                   ('shortcut', True, False, ('release-without-approval',))]
    definitions += [('sticky-' + str(i), False, True, ('revocation-ignored',)) for i in range(copies)]
    for index, (name, shortcut, sticky, commitments) in enumerate(definitions):
        transitions = []
        for state, action in product(states, actions):
            successor = state
            if action == 'approve' and state == 'pending':
                successor = 'approved'
            elif action == 'revoke' and state == 'approved' and not sticky:
                successor = 'pending'
            elif action == 'release' and (state == 'approved' or shortcut):
                successor = 'released'
            transitions.append((state, action, successor))
        declaration = FiniteModelDeclaration(name, states, ('pending',), actions, transitions,
            tuple((state, (('status', state),)) for state in states), (1, len(transitions), 0))
        candidates.append(ModelSearchCandidate(DeclaredFiniteModel(declaration),
            DescriptionLength(relations=index + 1), commitments))
    experiments = (
        ('without-approval', ('release',)),
        ('with-approval', ('approve', 'release')),
        ('after-revocation', ('approve', 'revoke', 'release')),
    )
    worlds = tuple(product(('pending', 'released'), repeat=3))
    protocol = SearchProtocol('approval-workflow-v1', 'explicit-table-v1',
        tuple(SearchExperiment(name, 'final status after ' + ','.join(path)) for name, path in experiments),
        worlds, (
            ResponseConstraint('release-without-approval', tuple(i for i, row in enumerate(worlds) if row[0] == 'released')),
            ResponseConstraint('revocation-ignored', tuple(i for i, row in enumerate(worlds) if row[2] == 'released')),
        ))
    cases = tuple(ModelSearchCase(name, Context(include_baseline=False,
        interventions=(Intervention(name, path, repeat_last=False),),
        scenario_manifest=(ScenarioKey('pending', name),)),
        ScenarioKey('pending', name), 'status', len(path)) for name, path in experiments)
    answers = tuple('controlled-release' if row == ('pending', 'released', 'pending') else 'unsafe-release'
                    for row in worlds)
    # Independently stipulated fixture records; never copied from model predictions.
    evidence = tuple(SearchObservation(name, response, 'fixture:approval-v1')
        for (name, _), response in zip(experiments, ('pending', 'released', 'pending')))
    return protocol, tuple(candidates), cases, answers, evidence


def run_case():
    started = perf_counter()
    protocol, candidates, cases, answers, evidence = workflow_inputs()
    construction = perf_counter() - started
    started = perf_counter()
    prepared = ExecutableSearchAdapter().prepare(protocol, candidates, cases,
        target='controlled approval and effective revocation', world_answers=answers)
    preparation = perf_counter() - started
    session = SearchSession.from_prepared(prepared)
    rows = []
    def record(phase):
        started = perf_counter()
        report = session.run()
        rows.append(dict(phase=phase, compatible=report.compatible, rejected=report.rejected,
            pruned=report.pruned, undecided=report.undecided, determined=report.determined,
            reason=report.stop_reason, seconds=perf_counter()-started, operations=report.work.total,
            active_conflicts=len(session.certificates)))
        return report
    record('purpose-declared-and-candidates-generated')
    session.replace_evidence(evidence)
    initial = record('evidence-collected-and-excluded')
    explanations = []
    started = perf_counter()
    for certificate in session.certificates:
        explanation = explain_conflict(session.search, certificate, evidence)
        assert verify_conflict_explanation(explanation, session.search, certificate, evidence) == 'valid'
        explanations.append(dict(commitments=certificate.commitments,
            evidence=[dict(experiment=o.experiment, actual=o.response, source=o.source) for o in certificate.evidence],
            excluded=[h.name for h in session.search.hypotheses
                      if set(certificate.commitments) <= set(h.commitments)],
            status=explanation.status))
    review = perf_counter() - started
    revoked = session.replace_evidence(evidence[:2])
    withdrawn = record('revocation-record-withdrawn')
    replacement = evidence[:2] + (SearchObservation('after-revocation', 'pending', 'fixture:approval-v2'),)
    session.replace_evidence(replacement)
    final = record('replacement-record-revalidated')
    bundle = create_replay_bundle(protocol, candidates, cases,
        target='controlled approval and effective revocation', world_answers=answers, evidence=replacement)
    replay = replay_bundle(bundle)
    assert initial.compatible == final.compatible == ('guarded',)
    assert set(withdrawn.compatible) == {'guarded', 'sticky-0'}
    assert revoked and replay.status == 'verified'
    # Same literal counterexample can be shown without interpreting a proof graph.
    counterexamples = [dict(candidate=h.name, experiment=e.name, predicted=protocol.worlds[h.world][i],
        actual=evidence[i].response, action_path=cases[i].context.interventions[0].actions)
        for h in prepared.search.hypotheses if h.world is not None
        for i, e in enumerate(protocol.experiments) if protocol.worlds[h.world][i] != evidence[i].response]
    return dict(scope='3 states, 4 actions, 3 fixed action paths, 3 candidates, 8 response rows',
        evidence_kind='controlled fixture; no operational deployment or participant study',
        purpose='release requires approval; revocation prevents subsequent release; approved release remains possible',
        engineering_cost=dict(construction_seconds=construction, preparation_seconds=preparation,
                              review_seconds=review, simulations=prepared.simulations_used),
        human_evaluation=dict(modeling_minutes=None, review_minutes=None, counterexample_understood=None,
                              reason='requires actual operator records and participant review'),
        phases=rows, audit_events=session.events, revoked_certificates=len(revoked), explanations=explanations,
        counterexamples=counterexamples, replay_status=replay.status), bundle


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir')
    args = parser.parse_args()
    report, bundle = run_case()
    if args.output_dir:
        destination = Path(args.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'replay.json').write_text(bundle, encoding='utf-8')
        (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
