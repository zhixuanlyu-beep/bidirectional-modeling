"""Reprove finite conflicts after an explicit context/evidence translation."""
from dataclasses import asdict, dataclass

from .context_network import _prepare_context_transition
from .search import ConflictCertificate, SearchBudgetExceeded, SearchWorkBudget, SearchObservation, _well_formed_conflict
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


def _transport_conflict(source_search, target_search, transition, certificate,
                        source_evidence, target_evidence, evidence_links, prepared, *, budget=None):
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
        report, relation = prepared
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
                matches = relation[i]
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


def transport_conflict(source_search, target_search, transition, certificate,
                       source_evidence, target_evidence, evidence_links, *, budget=None):
    budget = budget if budget is not None else SearchWorkBudget()
    prepared = _prepare_context_transition(transition, budget=budget)
    return _transport_conflict(source_search, target_search, transition, certificate,
        source_evidence, target_evidence, evidence_links, prepared, budget=budget)


def verify_transported_conflict(receipt, source_search, target_search, transition,
                                certificate, source_evidence, target_evidence, *, budget=None):
    """Check the supplied source/target proofs and mapping without the generator."""
    if (not isinstance(receipt, TransportedConflict)
            or not _well_formed_conflict(certificate)
            or any(not isinstance(getattr(receipt, field), str) for field in
                   ('status', 'reason', 'source_fingerprint', 'transition_fingerprint'))
            or not isinstance(receipt.evidence_links, tuple)
            or any(not isinstance(pair, tuple) or len(pair) != 2
                   or any(not isinstance(o, SearchObservation) for o in pair)
                   for pair in receipt.evidence_links)):
        return 'invalid'
    if (certificate.protocol_fingerprint != source_search.protocol.fingerprint
            or receipt.source_fingerprint != fingerprint_value(asdict(certificate))
            or receipt.transition_fingerprint != transition.fingerprint
            or source_search.protocol.fingerprint != transition.source.protocol.fingerprint
            or target_search.protocol.fingerprint != transition.target.protocol.fingerprint):
        return 'invalid'
    if receipt.status == 'undecided':
        return 'undecided'
    if (receipt.status != 'verified' or receipt.reason != 'target_conflict_reproved'
            or not _well_formed_conflict(receipt.certificate)
            or receipt.certificate.protocol_fingerprint != target_search.protocol.fingerprint):
        return 'invalid'
    budget = budget if budget is not None else SearchWorkBudget()
    try:
        report, relation = _prepare_context_transition(transition, budget=budget)
        if report.status != 'valid':
            return 'undecided' if report.status == 'undecided' else 'invalid'
        if not source_search.validates_conflict(certificate, source_evidence, budget=budget):
            return 'invalid'
        active = target_search._evidence(target_evidence, budget)
        mapping = dict(transition.commitments)
        if any(name not in mapping for name in certificate.commitments):
            return 'invalid'
        expected = tuple(dict.fromkeys(mapping[name] for name in certificate.commitments))
        if receipt.certificate.commitments != expected:
            return 'invalid'
        experiment_map = dict(transition.experiments)
        translated = []
        for old in certificate.evidence:
            budget.consume('certificate_checks')
            matches = [new for previous, new in receipt.evidence_links
                       if previous == old and new in active
                       and experiment_map.get(old.experiment) == new.experiment
                       and transition.translate_response(old.experiment, new.response) == old.response]
            if not matches:
                return 'invalid'
            translated.append(matches[0])
        if receipt.certificate.evidence != tuple(dict.fromkeys(translated)):
            return 'invalid'
        source_constraints = {c.name: set(c.worlds) for c in source_search.protocol.constraints}
        target_constraints = {c.name: set(c.worlds) for c in target_search.protocol.constraints}
        for old in certificate.commitments:
            for i in sorted(target_constraints[mapping[old]]):
                budget.consume('constraint_checks')
                if not relation[i] or any(j not in source_constraints[old] for j in relation[i]):
                    return 'invalid'
        return 'valid' if target_search.validates_conflict(
            receipt.certificate, active, budget=budget) else 'invalid'
    except SearchBudgetExceeded:
        return 'undecided'
    except ValueError:
        return 'invalid'
