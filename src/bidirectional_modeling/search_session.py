"""Versioned JSON search sessions. Load data, never executable Python callbacks."""
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .search import (ConflictCertificate, DescriptionLength, ExperimentHypothesisSearch,
                     ResponseConstraint, SearchExperiment, SearchHypothesis,
                     SearchObservation, SearchProtocol, SearchWorkBudget, SearchBudgetExceeded)
from .structural import fingerprint_value, validate_fingerprint


class SearchSession:
    def __init__(self, search, evidence=(), certificates=(), *, budget=None,
                 model_bindings=(), prepared_problem_fingerprint=None):
        self.search = search
        self.evidence = search._evidence(evidence, budget if budget is not None else SearchWorkBudget())
        self.certificates = tuple(certificates)
        self.revision = 0
        self.events = []
        self.model_bindings = tuple(tuple(row) for row in model_bindings)
        self.prepared_problem_fingerprint = prepared_problem_fingerprint
        if prepared_problem_fingerprint is not None:
            validate_fingerprint(prepared_problem_fingerprint)
        for row in self.model_bindings:
            if len(row) != 5:
                raise ValueError('model binding requires candidate, case, two batches and declaration')
            for digest in row[2:]:
                validate_fingerprint(digest)

    @classmethod
    def from_prepared(cls, prepared, evidence=(), *, budget=None):
        return cls(prepared.search,evidence,budget=budget,
                   model_bindings=prepared.batch_bindings,
                   prepared_problem_fingerprint=prepared.search.fingerprint)

    def run(self, *, budget=None, max_replays=None):
        report = self.search.search(self.evidence,self.certificates,
                                    budget=budget,max_replays=max_replays)
        # Do not discard previously valid certificates just because revalidation
        # was interrupted. Every subsequent search revalidates before use.
        if report.stop_reason not in ('work_budget_exhausted','cancelled'):
            self.certificates = report.conflicts
        else:
            self.certificates = tuple(dict.fromkeys(self.certificates+report.conflicts))
        return report

    def replace_evidence(self, evidence, *, budget=None):
        budget = budget if budget is not None else SearchWorkBudget()
        evidence = self.search._evidence(evidence,budget)
        # Transactional: an interrupted revalidation leaves the old session intact.
        retained, revoked = [], []
        for c in self.certificates:
            if self.search.validates_conflict(c,evidence,budget=budget):
                retained.append(c)
            else:
                revoked.append(fingerprint_value(asdict(c)))
        old = fingerprint_value(tuple(asdict(o) for o in self.evidence))
        self.evidence, self.certificates = evidence, tuple(retained)
        self.revision += 1
        self.events.append(dict(kind='evidence_replaced',revision=self.revision,
                               previous_evidence=old,
                               evidence=fingerprint_value(tuple(asdict(o) for o in evidence)),
                               revoked=revoked))
        return tuple(revoked)

    def add_hypothesis(self, hypothesis, *, parent=None):
        parents = {h.name:h for h in self.search.hypotheses}
        if parent is not None and parent not in parents:
            raise ValueError('unknown reconstruction parent')
        updated = self.search.with_hypotheses(self.search.hypotheses+(hypothesis,))
        before = set(parents[parent].commitments) if parent is not None else set()
        after = set(hypothesis.commitments)
        self.search = updated
        self.revision += 1
        self.events.append(dict(kind='hypothesis_added',revision=self.revision,
                               name=hypothesis.name,parent=parent,
                               retained=sorted(before & after), withdrawn=sorted(before-after),
                               added=sorted(after-before)))

    def reconstruct(self, rule, parent, **candidate):
        parents = {h.name:h for h in self.search.hypotheses}
        if parent not in parents:
            raise ValueError('unknown reconstruction parent')
        hypothesis = rule.apply(parents[parent], **candidate)
        # add_hypothesis validates prediction/commitments before mutating anything.
        self.add_hypothesis(hypothesis,parent=parent)
        self.events[-1]['rule'] = rule.name
        return hypothesis

    def migrate_context(self, target_search, transition, target_evidence, evidence_links, *, budget=None):
        return migrate_session(self, target_search, transition, target_evidence, evidence_links, budget=budget)

    def to_json(self):
        payload = dict(schema_version=2, world_answers=self.search.world_answers, backend=self.search.backend, protocol=asdict(self.search.protocol),
                       hypotheses=[asdict(h) for h in self.search.hypotheses],
                       target=self.search.target,
                       problem_fingerprint=self.search.fingerprint,
                       evidence=[asdict(o) for o in self.evidence],
                       certificates=[asdict(c) for c in self.certificates],
                       revision=self.revision,events=self.events,
                       model_bindings=self.model_bindings,
                       prepared_problem_fingerprint=self.prepared_problem_fingerprint)
        # Normalize tuples to JSON arrays before computing the checksum.
        payload = json.loads(json.dumps(payload))
        return json.dumps(dict(payload=payload,checksum=fingerprint_value(payload)),
                          ensure_ascii=False,indent=2,sort_keys=True)

    @classmethod
    def from_json(cls, text, *, budget=None):
        budget = budget if budget is not None else SearchWorkBudget()
        document = json.loads(text)
        payload = document['payload']
        if document['checksum'] != fingerprint_value(payload):
            raise ValueError('session checksum mismatch')
        if type(payload['schema_version']) is not int or payload['schema_version'] not in (1,2):
            raise ValueError('unsupported session schema')
        p = payload['protocol']
        protocol = SearchProtocol(p['scope'],p['coding'],
            tuple(SearchExperiment(**e) for e in p['experiments']),tuple(p['worlds']),
            tuple(ResponseConstraint(**c) for c in p['constraints']))
        hypotheses = tuple(SearchHypothesis(**dict(h,description=DescriptionLength(**h['description'])))
                           for h in payload['hypotheses'])
        search = ExperimentHypothesisSearch(protocol,hypotheses,payload['target'],backend=payload.get('backend','scan'),
                    world_answers=payload['world_answers'] if payload['schema_version'] == 2 else None)
        if search.fingerprint != payload['problem_fingerprint']:
            raise ValueError('session problem binding mismatch')
        evidence = tuple(SearchObservation(**o) for o in payload['evidence'])
        certificates = tuple(ConflictCertificate(c['protocol_fingerprint'],tuple(c['commitments']),
                             tuple(SearchObservation(**o) for o in c['evidence']))
                             for c in payload['certificates'])
        for c in certificates:
            if not search.validates_conflict(c,evidence,budget=budget):
                raise ValueError('invalid or stale saved conflict certificate')
        session = cls(search,evidence,certificates,budget=budget,
                      model_bindings=payload.get('model_bindings',()),
                      prepared_problem_fingerprint=payload.get('prepared_problem_fingerprint'))
        if type(payload['revision']) is not int or payload['revision'] < 0:
            raise ValueError('invalid session revision')
        if not isinstance(payload['events'],list):
            raise ValueError('invalid event history')
        session.revision, session.events = payload['revision'], payload['events']
        return session

    def save(self, path):
        path = Path(path)
        text = self.to_json()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                             delete=False) as stream:
                temporary = stream.name
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path, *, max_bytes=5_000_000, budget=None):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError('max_bytes must be a nonnegative integer')
        with open(path,'rb') as stream:
            data = stream.read(max_bytes+1)
        if len(data) > max_bytes:
            raise ValueError('session exceeds input limit')
        return cls.from_json(data.decode('utf-8'),budget=budget)



@dataclass(frozen=True)
class ContextMigrationResult:
    status: str
    reason: str
    session: object = None
    transports: tuple = ()


def migrate_session(session, target_search, transition, target_evidence, evidence_links, *, budget=None):
    """Fork a new session transactionally; the source session remains unchanged."""
    from .certificate_transport import _transport_conflict
    from .context_network import _prepare_context_transition
    budget = budget if budget is not None else SearchWorkBudget()
    target_evidence, evidence_links = tuple(target_evidence), tuple(evidence_links)
    if (session.search.protocol.fingerprint != transition.source.protocol.fingerprint or
            target_search.protocol.fingerprint != transition.target.protocol.fingerprint):
        return ContextMigrationResult('not_applicable', 'protocol_binding_mismatch')
    prepared = _prepare_context_transition(transition, budget=budget)
    report, _ = prepared
    if report.status != 'valid':
        return ContextMigrationResult('undecided' if report.status == 'undecided' else 'not_applicable', report.reason)
    receipts = []
    try:
        for certificate in session.certificates:
            receipt = _transport_conflict(session.search, target_search, transition, certificate,
                session.evidence, target_evidence, evidence_links, prepared, budget=budget)
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
