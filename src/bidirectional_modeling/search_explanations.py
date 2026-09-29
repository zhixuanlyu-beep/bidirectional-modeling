"""On-demand, replayable dependency paths for finite conflict claims.

These are explanations of already declared certificates, not a new inference
engine. In particular a missing path is never a proof of non-exclusion.
"""
from dataclasses import dataclass

from .certificate_transport import transport_conflict, verify_transported_conflict
from .search import SearchBudgetExceeded, SearchWorkBudget


@dataclass(frozen=True)
class DependencyNode:
    key: str
    kind: str
    subject: object
    depends_on: tuple = ()


@dataclass(frozen=True)
class DependencyExplanation:
    status: str  # valid / invalid / undecided
    reason: str
    nodes: tuple = ()
    root: str = ''


def _conflict_nodes(prefix, protocol, certificate):
    nodes = [DependencyNode(prefix + 'protocol', 'protocol', protocol.fingerprint)]
    inputs = [prefix + 'protocol']
    for i, name in enumerate(certificate.commitments):
        key = prefix + 'commitment.' + str(i)
        nodes.append(DependencyNode(key, 'commitment', name, (prefix + 'protocol',)))
        inputs.append(key)
    for i, observation in enumerate(certificate.evidence):
        key = prefix + 'observation.' + str(i)
        nodes.append(DependencyNode(key, 'observation', observation, (prefix + 'protocol',)))
        inputs.append(key)
    root = prefix + 'conflict'
    nodes.append(DependencyNode(root, 'empirical_conflict', certificate, tuple(inputs)))
    return nodes, root


def explain_conflict(search, certificate, active_evidence, *, candidate=None, budget=None):
    """Explain a currently valid conflict and optionally one affected candidate.

    The existing conflict checker independently establishes evidence-only and
    commitment-only consistency and their joint contradiction before a path is
    returned. The path records dependencies, not a new minimality claim.
    """
    budget = budget if budget is not None else SearchWorkBudget()
    try:
        if not search.validates_conflict(certificate, active_evidence, budget=budget):
            return DependencyExplanation('invalid', 'conflict_not_valid_for_live_evidence')
    except SearchBudgetExceeded as error:
        return DependencyExplanation('undecided', error.reason)
    nodes, root = _conflict_nodes('', search.protocol, certificate)
    if candidate is not None:
        candidates = {h.name: h for h in search.hypotheses}
        if candidate not in candidates or not set(certificate.commitments).issubset(
                candidates[candidate].commitments):
            return DependencyExplanation('invalid', 'candidate_does_not_inherit_conflict')
        nodes.append(DependencyNode('exclusion', 'candidate_excluded', candidate, (root,)))
        root = 'exclusion'
    return DependencyExplanation('valid', 'live_dependency_path', tuple(nodes), root)


def verify_conflict_explanation(explanation, search, certificate, active_evidence,
                                *, candidate=None, budget=None):
    """Check the proof and graph schema without invoking the graph builder."""
    budget = budget if budget is not None else SearchWorkBudget()
    try:
        if not search.validates_conflict(certificate, active_evidence, budget=budget):
            return 'invalid'
    except SearchBudgetExceeded:
        return 'undecided'
    sections = _check_conflict_section(explanation, '', search.protocol, certificate)
    if sections is None:
        return 'invalid'
    root = 'conflict'
    if candidate is not None:
        found = next((h for h in search.hypotheses if h.name == candidate), None)
        if found is None or not set(certificate.commitments).issubset(found.commitments):
            return 'invalid'
        if not _has_node(explanation, 'exclusion', 'candidate_excluded', candidate, (root,)):
            return 'invalid'
        sections.add('exclusion')
        root = 'exclusion'
    return 'valid' if _complete_graph(explanation, sections, root,
                                     'live_dependency_path') else 'invalid'


def _has_node(explanation, key, kind, subject, dependencies=()):
    return DependencyNode(key, kind, subject, tuple(dependencies)) in explanation.nodes


def _check_conflict_section(explanation, prefix, protocol, certificate):
    """Reconstruct the declared node contract independently of _conflict_nodes."""
    keys = {prefix + 'protocol', prefix + 'conflict'}
    if not _has_node(explanation, prefix + 'protocol', 'protocol', protocol.fingerprint):
        return None
    inputs = [prefix + 'protocol']
    for index, commitment in enumerate(certificate.commitments):
        key = prefix + 'commitment.' + str(index)
        if not _has_node(explanation, key, 'commitment', commitment,
                         (prefix + 'protocol',)):
            return None
        inputs.append(key)
        keys.add(key)
    for index, observation in enumerate(certificate.evidence):
        key = prefix + 'observation.' + str(index)
        if not _has_node(explanation, key, 'observation', observation,
                         (prefix + 'protocol',)):
            return None
        inputs.append(key)
        keys.add(key)
    if not _has_node(explanation, prefix + 'conflict', 'empirical_conflict',
                     certificate, inputs):
        return None
    return keys


def _complete_graph(explanation, keys, root, reason):
    if (explanation.status != 'valid' or explanation.reason != reason
            or explanation.root != root or len(explanation.nodes) != len(keys)):
        return False
    seen = {node.key for node in explanation.nodes}
    return (seen == keys and len(seen) == len(explanation.nodes)
            and all(set(node.depends_on).issubset(seen) for node in explanation.nodes))


def explain_transported_conflict(receipt, source_search, target_search, transition,
                                 certificate, source_evidence, target_evidence, *, budget=None):
    """Replay both scopes, then show the actual mapped inputs and target proof."""
    replay = transport_conflict(source_search, target_search, transition, certificate,
        source_evidence, target_evidence, receipt.evidence_links, budget=budget)
    if replay.status == 'undecided':
        return DependencyExplanation('undecided', replay.reason)
    if replay.status != 'verified':
        return DependencyExplanation('invalid', replay.reason)
    if replay != receipt:
        return DependencyExplanation('invalid', 'receipt_mismatch')
    source, source_root = _conflict_nodes('source.', source_search.protocol, certificate)
    target, target_root = _conflict_nodes('target.', target_search.protocol, receipt.certificate)
    nodes = source + target
    links = []
    target_support = set(receipt.certificate.evidence)
    active_target = set(target_evidence)
    experiments = dict(transition.experiments)
    for i, old in enumerate(certificate.evidence):
        matches = [new for previous, new in receipt.evidence_links
                   if previous == old and new in active_target
                   and experiments.get(old.experiment) == new.experiment
                   and transition.translate_response(old.experiment, new.response) == old.response]
        if not matches:
            return DependencyExplanation('invalid', 'missing_explained_evidence_link')
        new = matches[0]
        if new not in target_support:
            return DependencyExplanation('invalid', 'target_support_changed')
        index = receipt.certificate.evidence.index(new)
        key = 'link.' + str(i)
        nodes.append(DependencyNode(key, 'evidence_translation', (old, new),
            ('source.observation.' + str(i), 'target.observation.' + str(index))))
        links.append(key)
    mapping = dict(transition.commitments)
    mappings = []
    for i, old in enumerate(certificate.commitments):
        new = mapping[old]
        index = receipt.certificate.commitments.index(new)
        key = 'mapping.' + str(i)
        nodes.append(DependencyNode(key, 'commitment_translation', (old, new),
            ('source.commitment.' + str(i), 'target.commitment.' + str(index))))
        mappings.append(key)
    nodes.append(DependencyNode('transition', 'context_transition', transition.fingerprint,
                                (source_root,)))
    nodes.append(DependencyNode('transport', 'target_reproved', receipt.certificate,
        tuple(['transition', target_root] + links + mappings)))
    return DependencyExplanation('valid', 'transport_dependency_path', tuple(nodes), 'transport')


def verify_transport_explanation(explanation, receipt, source_search, target_search,
                                 transition, certificate, source_evidence, target_evidence,
                                 *, budget=None):
    """Check target reproof, then validate each dependency edge separately."""
    verdict = verify_transported_conflict(receipt, source_search, target_search,
        transition, certificate, source_evidence, target_evidence, budget=budget)
    if verdict != 'valid':
        return verdict
    source_keys = _check_conflict_section(explanation, 'source.',
                                          source_search.protocol, certificate)
    target_keys = _check_conflict_section(explanation, 'target.',
                                          target_search.protocol, receipt.certificate)
    if source_keys is None or target_keys is None:
        return 'invalid'
    keys = source_keys | target_keys | {'transition', 'transport'}
    if not _has_node(explanation, 'transition', 'context_transition',
                     transition.fingerprint, ('source.conflict',)):
        return 'invalid'
    active_target = set(target_evidence)
    experiment_map = dict(transition.experiments)
    linked = []
    for index, old in enumerate(certificate.evidence):
        matches = [new for previous, new in receipt.evidence_links
                   if previous == old and new in active_target
                   and experiment_map.get(old.experiment) == new.experiment
                   and transition.translate_response(old.experiment, new.response) == old.response]
        if not matches or matches[0] not in receipt.certificate.evidence:
            return 'invalid'
        new = matches[0]
        key = 'link.' + str(index)
        dependencies = ('source.observation.' + str(index),
                        'target.observation.' + str(receipt.certificate.evidence.index(new)))
        if not _has_node(explanation, key, 'evidence_translation', (old, new), dependencies):
            return 'invalid'
        keys.add(key)
        linked.append(key)
    commitment_map = dict(transition.commitments)
    mapped = []
    for index, old in enumerate(certificate.commitments):
        new = commitment_map.get(old)
        if new not in receipt.certificate.commitments:
            return 'invalid'
        key = 'mapping.' + str(index)
        dependencies = ('source.commitment.' + str(index),
                        'target.commitment.' + str(receipt.certificate.commitments.index(new)))
        if not _has_node(explanation, key, 'commitment_translation', (old, new), dependencies):
            return 'invalid'
        keys.add(key)
        mapped.append(key)
    if not _has_node(explanation, 'transport', 'target_reproved', receipt.certificate,
                     ('transition', 'target.conflict') + tuple(linked) + tuple(mapped)):
        return 'invalid'
    return 'valid' if _complete_graph(explanation, keys, 'transport',
                                     'transport_dependency_path') else 'invalid'
