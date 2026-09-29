"""Measure update/revalidation and screening separately before adding indexes.

Run with PYTHONPATH=src. Construction, certificate discovery and fingerprints
are excluded. Counts describe this synthetic workload, not universal speedups.
"""
import json
from dataclasses import asdict

from bidirectional_modeling import (DescriptionLength, ExperimentHypothesisSearch,
    ResponseConstraint, SearchExperiment, SearchHypothesis, SearchObservation,
    SearchProtocol, SearchWorkBudget)
from bidirectional_modeling.search import ConflictCertificate
from bidirectional_modeling.search_session import SearchSession


def run():
    rows = []
    for size in (4, 16, 64):
        protocol = SearchProtocol('update-costs', 'finite table',
            (SearchExperiment('a', 'read'),), (('0',), ('1',)),
            tuple(ResponseConstraint('c%d' % i, (0,)) for i in range(size)))
        candidates = tuple(SearchHypothesis('h%d' % i, 1, 'yes', DescriptionLength())
                           for i in range(size))
        search = ExperimentHypothesisSearch(protocol, candidates, 'out')
        evidence = tuple(SearchObservation('a', '1', 'source-%d' % i) for i in range(size))
        certificates = tuple(ConflictCertificate(protocol.fingerprint,
            ('c0',) if i == 0 else ('c0', 'c%d' % i), (evidence[i],)) for i in range(size))
        self_contained = all(search.validates_conflict(c, evidence) for c in certificates)
        assert self_contained
        search.fingerprint
        session = SearchSession(search, evidence, certificates)
        update = SearchWorkBudget()
        session.replace_evidence(evidence[1:], budget=update)
        assert len(session.certificates) == size - 1
        full = search.search(evidence, certificates, learn_conflicts=False)
        assert full.work.certificate_checks <= 3 * size
        assert len(full.conflicts) == size
        # Snapshot comparison only: c0 covers the other cones while its evidence is live.
        representative = search.search(evidence, certificates[:1], learn_conflicts=False)
        assert full.compatible == representative.compatible == tuple(h.name for h in
            sorted(candidates, key=lambda h: h.name))
        assert not search.validates_conflict(certificates[0], evidence[1:])
        assert all(search.validates_conflict(c, evidence[1:]) for c in certificates[1:])
        rows.append(dict(certificates=size, candidates=size,
            retained_after_one_source_withdrawal=len(session.certificates),
            update_work=asdict(update.work), full_screen_work=asdict(full.work),
            representative_screen_work=asdict(representative.work)))
    return dict(constructor_and_discovery_cost_included=False,
        representative_is_snapshot_only=True, results=rows)


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
