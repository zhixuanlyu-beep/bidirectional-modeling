"""Reprove finite conflicts after an explicit context/evidence translation."""
from dataclasses import asdict, dataclass

from .context_network import validate_context_transition
from .search import ConflictCertificate, SearchBudgetExceeded, SearchWorkBudget
from .structural import fingerprint_value


@dataclass(frozen=True)
class TransportedConflict:
    status: str
    reason: str
    source_fingerprint: str
    transition_fingerprint: str
    certificate: object = None
    # Explicit source/target observations, including their provenance labels.
    evidence_links: tuple = ()


def transport_conflict(source_search, target_search, transition, certificate,
                       source_evidence, target_evidence, evidence_links, *, budget=None):
    """A finite implication check plus a fresh target contradiction proof.

    Evidence links are caller declarations of provenance, not authentication.
    Target scope/calibration is part of its new protocol, never inherited by hash.
    """
    budget = budget if budget is not None else SearchWorkBudget()
    links = tuple(tuple(pair) for pair in evidence_links)
    digest = fingerprint_value(asdict(certificate))
    def result(status, reason, proof=None):
        return TransportedConflict(status, reason, digest, transition.fingerprint, proof, links)
    if (source_search.protocol.fingerprint != transition.source.protocol.fingerprint or
            target_search.protocol.fingerprint != transition.target.protocol.fingerprint):
        return result('not_applicable', 'protocol_binding_mismatch')
    if any(len(pair) != 2 for pair in links):
        return result('not_applicable', 'malformed_evidence_links')
    try:
        report = validate_context_transition(transition, budget=budget)
        if report.status != 'valid':
            return result('undecided' if report.status == 'undecided' else 'not_applicable', report.reason)
        if not source_search.validates_conflict(certificate, source_evidence, budget=budget):
            return result('not_applicable', 'source_certificate_invalid')
        target_evidence = target_search._evidence(target_evidence, budget)
        mapping = dict(transition.commitments)
        if any(c not in mapping for c in certificate.commitments):
            return result('not_applicable', 'unmapped_commitment')
        experiment_map = dict(transition.experiments)
        support = []
        for old in certificate.evidence:
            budget.consume('certificate_checks')
            matches = [new for previous, new in links if previous == old and new in target_evidence
                       and experiment_map.get(old.experiment) == new.experiment
                       and transition.translate_response(old.experiment, new.response) == old.response]
            if not matches:
                return result('not_applicable', 'missing_evidence_translation')
            support.append(matches[0])
        old_constraints = {c.name: set(c.worlds) for c in source_search.protocol.constraints}
        new_constraints = {c.name: set(c.worlds) for c in target_search.protocol.constraints}
        # Every target behavior retaining a mapped constraint must retain its
        # source meaning. Enlarging a response universe may break this property.
        for old in certificate.commitments:
            for i in sorted(new_constraints[mapping[old]]):
                budget.consume('constraint_checks')
                matches = transition.matching_source_worlds(target_search.protocol.worlds[i], budget)
                if not matches or any(j not in old_constraints[old] for j in matches):
                    return result('not_applicable', 'commitment_meaning_not_preserved')
        proposed = ConflictCertificate(target_search.protocol.fingerprint,
            tuple(dict.fromkeys(mapping[c] for c in certificate.commitments)),
            tuple(dict.fromkeys(support)))
        if not target_search.validates_conflict(proposed, target_evidence, budget=budget):
            return result('not_applicable', 'target_contradiction_not_established')
        return result('verified', 'target_conflict_reproved', proposed)
    except SearchBudgetExceeded as error:
        return result('undecided', error.reason)
    except ValueError as error:
        return result('not_applicable', str(error))


def verify_transported_conflict(receipt, source_search, target_search, transition,
                                certificate, source_evidence, target_evidence, *, budget=None):
    replay = transport_conflict(source_search, target_search, transition, certificate,
        source_evidence, target_evidence, receipt.evidence_links, budget=budget)
    if replay.status == 'undecided':
        return 'undecided'
    return 'valid' if replay.status == 'verified' and replay == receipt else 'invalid'


@dataclass(frozen=True)
class ContextMigrationResult:
    status: str
    reason: str
    session: object = None
    transports: tuple = ()


def migrate_session(session, target_search, transition, target_evidence, evidence_links, *, budget=None):
    """Fork a new session transactionally; the source session remains unchanged."""
    from .search_session import SearchSession
    budget = budget if budget is not None else SearchWorkBudget()
    target_evidence, evidence_links = tuple(target_evidence), tuple(evidence_links)
    if (session.search.protocol.fingerprint != transition.source.protocol.fingerprint or
            target_search.protocol.fingerprint != transition.target.protocol.fingerprint):
        return ContextMigrationResult('not_applicable', 'protocol_binding_mismatch')
    report = validate_context_transition(transition, budget=budget)
    if report.status != 'valid':
        return ContextMigrationResult('undecided' if report.status == 'undecided' else 'not_applicable', report.reason)
    receipts = []
    try:
        for certificate in session.certificates:
            receipt = transport_conflict(session.search, target_search, transition, certificate,
                session.evidence, target_evidence, evidence_links, budget=budget)
            receipts.append(receipt)
            if receipt.status == 'undecided':
                return ContextMigrationResult('undecided', receipt.reason, transports=tuple(receipts))
        migrated = SearchSession(target_search, target_evidence,
            tuple(r.certificate for r in receipts if r.status == 'verified'), budget=budget)
    except SearchBudgetExceeded as error:
        return ContextMigrationResult('undecided', error.reason, transports=tuple(receipts))
    migrated.events.append(dict(kind='context_migrated',
        source_problem=session.search.fingerprint, source_revision=session.revision,
        transition=transition.fingerprint, transports=[asdict(r) for r in receipts]))
    return ContextMigrationResult('completed', 'new_context_session', migrated, tuple(receipts))
