"""An experiment-bounded example of failed materials yielding a valid reconstruction."""
from itertools import product
from dataclasses import asdict

from .search import (
    DescriptionLength, ExperimentHypothesisSearch, ResponseConstraint,
    SearchExperiment, SearchHypothesis, SearchObservation, SearchProtocol,
)


def conflict_search_scenario():
    worlds = tuple(product(('0', '1'), repeat=4))
    experiments = tuple(SearchExperiment(name, 'binary do(x,z)=' + name)
                        for name in ('00', '10', '01', '11'))
    additive = ResponseConstraint('additive', tuple(
        i for i, w in enumerate(worlds)
        if int(w[3]) - int(w[1]) - int(w[2]) + int(w[0]) == 0
    ))
    protocol = SearchProtocol(
        'binary x,z interventions; exact binary output; calibration-v1',
        'demo integer code units v1 (illustrative, not optimal encoding)',
        experiments, worlds, (additive,),
    )
    candidates = (
        SearchHypothesis('x', worlds.index(('0','1','0','1')), 'additive',
                         DescriptionLength(relations=1), ('additive',), ('x','z')),
        SearchHypothesis('z', worlds.index(('0','0','1','1')), 'additive',
                         DescriptionLength(relations=2), ('additive',), ('x','z')),
        # Reuse materials, withdraw independent sufficiency, introduce joint action.
        SearchHypothesis('xz', worlds.index(('0','0','0','1')), 'interaction',
                         DescriptionLength(concepts=1, relations=2), (), ('x','z')),
    )
    evidence = tuple(SearchObservation(e.name, y, 'external-lab-v1')
                     for e,y in zip(experiments, ('0','0','0','1')))
    return ExperimentHypothesisSearch(protocol, candidates, 'nonadditive response'), evidence


def build_search_demo_report():
    search, evidence = conflict_search_scenario()
    report = search.search(evidence)
    basis = search.compress_evidence(evidence)
    return {
        'scope': search.protocol.scope,
        'full_quotient': report.full_quotient,
        'surviving_quotient': report.surviving_quotient,
        'observed_quotient': report.observed_quotient,
        'partition_complete': report.partition_complete,
        'stop_reason': report.stop_reason,
        'work': asdict(report.work),
        'sufficiency_only': asdict(search.verify_macro(basis, evidence, check_minimality=False)),
        'compatible': report.compatible,
        'rejected': report.rejected,
        'pruned': report.pruned,
        'undecided': report.undecided,
        'determined': report.determined,
        'answers': report.answers,
        'replay_checks': report.replay_checks,
        'conflict_core': report.conflicts[0].commitments,
        'conflict_evidence_count': len(report.conflicts[0].evidence),
        'retained_macro_evidence': [o.experiment for o in basis.retained_evidence],
        'minimum_cardinality': basis.minimum_cardinality,
        'certificate_valid': search.validates_macro(basis, evidence),
        'initial_next_experiment': search.next_experiment().name,
        'irreducible_against_x_and_z': search.irreducible_against('xz', ('x','z')),
        'protocol_fingerprint': search.protocol.fingerprint,
        'problem_fingerprint': search.fingerprint,
    }


def dynamic_search_scenario(copies=20):
    """Growing failure families, evidence retraction, then recalibrated evidence."""
    from dataclasses import replace
    from .search import _natural
    from .search_benchmark import SearchBenchmarkStep
    _natural(copies)
    search,data=conflict_search_scenario()
    initial=search.with_hypotheses((search.hypotheses[0],search.hypotheses[2]))
    additions=tuple(replace(search.hypotheses[1],name='z-copy-%d' % i) for i in range(copies))
    steps=(
        SearchBenchmarkStep('initial',data),
        SearchBenchmarkStep('candidate-growth',data,additions),
        SearchBenchmarkStep('evidence-retracted',data[:1]),
        SearchBenchmarkStep('recalibrated',tuple(replace(o,source='lab-v2') for o in data)),
    )
    return initial,steps
