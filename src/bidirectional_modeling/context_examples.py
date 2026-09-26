"""End-to-end finite context/reconstruction acceptance scenario."""
from dataclasses import replace
from itertools import product

from .boolean_reconstruction import (BooleanExpression, BooleanLanguage, find_boolean_substitute,
                                     reconstruct_boolean)
from .certificate_transport import transport_conflict
from .context_network import ModelingContext, ContextChange, ContextTransition, validate_context_transition
from .core import Context, ScenarioKey
from .gluing import LocalDescription, GluingProblem, solve_gluing
from .macro_certificates import certify_macro_sufficiency, verify_macro_sufficiency
from .search import (SearchProtocol, SearchExperiment, SearchObservation, ResponseConstraint,
                     ExperimentHypothesisSearch, SearchHypothesis)
from .search_adapter import ModelSearchCandidate, ModelSearchCase, ExecutableSearchAdapter
from .search_lazy import LazyExecutableSearch
from .search_reconstruction import ReconstructionRule
from .search_examples import conflict_search_scenario


def build_context_demo_report():
    # One new experiment splits each existing observational behavior in two.
    old = SearchProtocol('one-probe', 'binary', (SearchExperiment('a', 'a'),), (('0',), ('1',)))
    new = SearchProtocol('two-probes', 'binary', old.experiments+(SearchExperiment('b', 'b'),),
                         tuple(product(('0', '1'), repeat=2)))
    def context(name, protocol):
        return ModelingContext(name, protocol, 'finite lab', 'exact', ('x', 'z'), 'Boolean', 'interaction')
    transition = ContextTransition(context('old', old), context('new', new), ContextChange.EXTENSION,
        (('a', 'a'),), (('a', '0', '0'), ('a', '1', '1')))
    changed = validate_context_transition(transition)

    # Three binary observations refute additivity, but adding -1 removes this proof.
    def additive(values, scope):
        rows = tuple(product(values, repeat=4))
        experiments = tuple(SearchExperiment(n, n) for n in ('00', '01', '10', '11'))
        constraints = (ResponseConstraint('additive', tuple(i for i, row in enumerate(rows)
            if int(row[3])-int(row[1])-int(row[2])+int(row[0]) == 0)),)
        return SearchProtocol(scope, 'finite-integers', experiments, rows, constraints)
    binary, expanded = additive(('0', '1'), 'binary'), additive(('-1', '0', '1'), 'expanded')
    source, target = ExperimentHypothesisSearch(binary, (), 'interaction'), ExperimentHypothesisSearch(expanded, (), 'interaction')
    evidence = tuple(SearchObservation(n, value, 'lab') for n, value in (('01', '0'), ('10', '0'), ('11', '1')))
    proof = source.learn_conflict(('additive',), evidence)
    mapping = ContextTransition(context('binary', binary), context('expanded', expanded), ContextChange.RECONSTRUCTION,
        tuple((e.name, e.name) for e in binary.experiments),
        tuple((e.name, v, v) for e in binary.experiments for v in ('-1', '0', '1')),
        (('additive', 'additive'),))
    transfer = transport_conflict(source, target, mapping, proof, evidence, evidence, tuple(zip(evidence, evidence)))

    equal, unequal = (('0', '0'), ('1', '1')), (('0', '1'), ('1', '0'))
    gluing = solve_gluing(GluingProblem(tuple((n, ('0', '1')) for n in ('x', 'y', 'z')), (
        LocalDescription('xy', ('x', 'y'), equal), LocalDescription('yz', ('y', 'z'), equal),
        LocalDescription('xz', ('x', 'z'), unequal))))

    x, z = BooleanExpression(('var', 'x')), BooleanExpression(('var', 'z'))
    interaction = BooleanExpression(('and', x.tree, z.tree))
    language = BooleanLanguage(('x', 'z'))
    inputs = tuple(dict(zip(('x', 'z'), row)) for row in product((False, True), repeat=2))
    lower = find_boolean_substitute(interaction, replace(language, operations=('not',), max_nodes=4), inputs)
    cases = tuple(ModelSearchCase(e.name, Context(environment=row), ScenarioKey('s', 'baseline'), 'y')
                  for e, row in zip(binary.experiments, inputs))
    answers = tuple('interaction' if row == ('0', '0', '0', '1') else 'other' for row in binary.worlds)
    parent = SearchHypothesis('x', binary.worlds.index(('0', '0', '1', '1')), 'other',
                              language.description(x), ('additive',), ('x', 'z'))
    _, candidate, prepared = reconstruct_boolean((x, parent), interaction, (), language,
        ReconstructionRule('independent-to-interaction', withdraw=('additive',)), name='xz',
        protocol=binary, cases=cases, target='interaction', world_answers=answers)

    # A wrong model fails at 10, before running unobserved experiments 00 and 11.
    wrong = ModelSearchCandidate(x.to_model('x'), language.description(x))
    lazy = LazyExecutableSearch(binary, (wrong,), cases, target='interaction', world_answers=answers)
    screen = lazy.screen_evidence((SearchObservation('10', '0', 'lab'),))
    full = ExecutableSearchAdapter().prepare(binary, (wrong,), cases,
        target='interaction', world_answers=answers)

    search, history = conflict_search_scenario()
    compact = certify_macro_sufficiency(search, history)
    certificate = compact.certificate
    return {
        'context_extension': {'status': changed.status, 'split_source_worlds': changed.split_source_worlds,
                              'unrepresented_source_worlds': changed.unrepresented_source_worlds},
        'expanded_domain_transport': {'status': transfer.status, 'reason': transfer.reason},
        'local_gluing': {'overlap_consistent': gluing.overlap_consistent,
                        'status': gluing.status, 'conflict_core': gluing.conflict_core},
        'boolean_reconstruction': {'response': binary.worlds[prepared.search.hypotheses[0].world],
                                  'answer': prepared.search.hypotheses[0].macro_answer,
                                  'retained_commitments': candidate.commitments,
                                  'lower_substitute_status': lower.status},
        'partial_screening': {'excluded': screen.excluded, 'simulations_used': screen.simulations_used,
                              'full_matrix_simulations': full.simulations_used},
        'macro_sufficiency': {'answer': certificate.answer, 'history_size': len(history),
                             'retained_size': len(certificate.evidence), 'minimality': compact.minimality,
                             'verification': verify_macro_sufficiency(search, certificate, certificate.evidence)},
    }
