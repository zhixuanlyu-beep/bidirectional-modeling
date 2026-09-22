"""Finite experiment-relative hypothesis search and replayable evidence bases.

The caller owns the experiment protocol, finite response universe, constraint
semantics and candidate catalogue. This is an exact, discrete reference backend,
not an open-world theorem prover or a statistical rejection procedure.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from itertools import combinations
from fractions import Fraction
from typing import Optional, Tuple

from .structural import fingerprint_value


def _name(value: str) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError("semantic identifiers and response labels must be nonempty strings")


def _natural(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("costs and budgets must be nonnegative integers")


@dataclass(frozen=True)
class DescriptionLength:
    """All components use the protocol's shared coding convention; none are free."""
    framework: int = 0
    concepts: int = 0
    relations: int = 0
    parameters: int = 0
    observation_map: int = 0
    assumptions: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            _natural(getattr(self, field.name))

    @property
    def total(self) -> int:
        return sum(getattr(self, field.name) for field in fields(self))


@dataclass(frozen=True)
class SearchExperiment:
    name: str
    semantics: str
    cost: int = 1

    def __post_init__(self) -> None:
        _name(self.name)
        _name(self.semantics)
        _natural(self.cost)
        if not self.cost:
            raise ValueError("experiment cost must be positive")


@dataclass(frozen=True)
class ResponseConstraint:
    """Extensional semantics: indices of all protocol worlds satisfying a rule."""
    name: str
    worlds: Tuple[int, ...]

    def __post_init__(self) -> None:
        _name(self.name)
        object.__setattr__(self, "worlds", tuple(self.worlds))
        for index in self.worlds:
            _natural(index)
        if len(set(self.worlds)) != len(self.worlds):
            raise ValueError("duplicate constraint world")


@dataclass(frozen=True)
class SearchProtocol:
    scope: str
    coding: str
    experiments: Tuple[SearchExperiment, ...]
    worlds: Tuple[Tuple[str, ...], ...]
    constraints: Tuple[ResponseConstraint, ...] = ()

    def __post_init__(self) -> None:
        _name(self.scope)
        _name(self.coding)
        object.__setattr__(self, "experiments", tuple(self.experiments))
        object.__setattr__(self, "worlds", tuple(tuple(w) for w in self.worlds))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        if not self.experiments or not self.worlds:
            raise ValueError("protocol needs a nonempty experiment domain and response universe")
        for items in (self.experiments, self.constraints):
            if len({item.name for item in items}) != len(items):
                raise ValueError("duplicate semantic identifier")
        if len(set(self.worlds)) != len(self.worlds):
            raise ValueError("duplicate response world")
        for world in self.worlds:
            if len(world) != len(self.experiments):
                raise ValueError("world must specify every allowed experiment")
            for response in world:
                _name(response)
        for constraint in self.constraints:
            if any(i >= len(self.worlds) for i in constraint.worlds):
                raise ValueError("constraint references an unknown world")

    @property
    def fingerprint(self) -> str:
        return fingerprint_value((
            "search-protocol-v1", self.scope, self.coding,
            tuple((e.name, e.semantics, e.cost) for e in self.experiments),
            self.worlds, tuple((c.name, c.worlds) for c in self.constraints),
        ))


@dataclass(frozen=True)
class SearchObservation:
    experiment: str
    response: str
    source: str

    def __post_init__(self) -> None:
        for value in (self.experiment, self.response, self.source):
            _name(value)


@dataclass(frozen=True)
class SearchHypothesis:
    name: str
    world: Optional[int]
    macro_answer: str
    description: DescriptionLength
    commitments: Tuple[str, ...] = ()
    materials: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.name)
        _name(self.macro_answer)
        if self.world is not None:
            _natural(self.world)
        for field in ("commitments", "materials"):
            values = tuple(getattr(self, field))
            for value in values:
                _name(value)
            object.__setattr__(self, field, values)


@dataclass(frozen=True)
class ConflictCertificate:
    protocol_fingerprint: str
    commitments: Tuple[str, ...]
    evidence: Tuple[SearchObservation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "commitments", tuple(self.commitments))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class MacroEvidenceCertificate:
    problem_fingerprint: str
    full_evidence: Tuple[SearchObservation, ...]
    retained_evidence: Tuple[SearchObservation, ...]
    answers: Tuple[str, ...]
    determined: bool
    minimum_cardinality: bool
    subsets_checked: int


@dataclass(frozen=True)
class HypothesisSearchReport:
    compatible: Tuple[str, ...]
    pruned: Tuple[str, ...]
    rejected: Tuple[str, ...]
    undecided: Tuple[str, ...]
    quotient: Tuple[Tuple[str, ...], ...]
    answers: Tuple[str, ...]
    determined: bool
    conflicts: Tuple[ConflictCertificate, ...]
    replay_checks: int


class ExperimentHypothesisSearch:
    """Search an explicit catalogue, keeping every structural representative.

    ``world=None`` means unavailable/unknown prediction, not a contradiction.
    Reconstructed candidates are supplied by the domain adapter; parentage and
    shared materials never imply inheritance of logical commitments.
    """

    def __init__(self, protocol: SearchProtocol,
                 hypotheses: Tuple[SearchHypothesis, ...], target: str) -> None:
        _name(target)
        self.protocol = protocol
        self.hypotheses = tuple(hypotheses)
        self.target = target
        if len({h.name for h in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("duplicate hypothesis name")
        constraints = {c.name: set(c.worlds) for c in protocol.constraints}
        for h in self.hypotheses:
            if not isinstance(h.description, DescriptionLength):
                raise TypeError("hypothesis needs a complete DescriptionLength")
            if any(c not in constraints for c in h.commitments):
                raise ValueError("unknown commitment")
            if h.world is not None:
                if h.world >= len(protocol.worlds):
                    raise ValueError("hypothesis references an unknown world")
                if any(h.world not in constraints[c] for c in h.commitments):
                    raise ValueError("prediction violates a declared commitment")

    @property
    def fingerprint(self) -> str:
        return fingerprint_value((
            "search-problem-v1", self.protocol.fingerprint, self.target,
            tuple((h.name, h.world, h.macro_answer,
                   tuple(getattr(h.description, f.name) for f in fields(h.description)),
                   h.commitments, h.materials) for h in self.hypotheses),
        ))

    def _evidence(self, evidence):
        evidence = tuple(evidence)
        names = {e.name: i for i, e in enumerate(self.protocol.experiments)}
        for observation in evidence:
            if observation.experiment not in names:
                raise ValueError("evidence lies outside the allowed experiment domain")
            index = names[observation.experiment]
            if not any(w[index] == observation.response for w in self.protocol.worlds):
                raise ValueError("observed response lies outside the declared universe")
        return evidence

    def _worlds(self, commitments=(), evidence=()):
        possible = set(range(len(self.protocol.worlds)))
        constraints = {c.name: c.worlds for c in self.protocol.constraints}
        for name in commitments:
            possible.intersection_update(constraints[name])
        indices = {e.name: i for i, e in enumerate(self.protocol.experiments)}
        for observation in evidence:
            possible = {i for i in possible if self.protocol.worlds[i][
                indices[observation.experiment]] == observation.response}
        return possible

    def learn_conflict(self, commitments, evidence) -> Optional[ConflictCertificate]:
        """Extract an inclusion-minimal joint core, not blame for each member.

        Proof is exhaustive intersection in the caller-declared response universe.
        Data inconsistent on their own cannot certify a model conflict.
        """
        evidence = self._evidence(evidence)
        core = tuple(dict.fromkeys(commitments))
        known = {c.name for c in self.protocol.constraints}
        if any(c not in known for c in core):
            raise ValueError("unknown commitment")
        if (not core or not self._worlds(evidence=evidence)
                or not self._worlds(core) or self._worlds(core, evidence)):
            return None
        for name in tuple(core):
            trial = tuple(c for c in core if c != name)
            if not self._worlds(trial, evidence):
                core = trial
        support = evidence
        for observation in evidence:
            trial = tuple(o for o in support if o != observation)
            if not self._worlds(core, trial):
                support = trial
        return ConflictCertificate(self.protocol.fingerprint, core, support)

    def validates_conflict(self, certificate: ConflictCertificate, evidence) -> bool:
        """Recheck binding, live evidence dependencies, and the actual contradiction."""
        evidence = self._evidence(evidence)
        if certificate.protocol_fingerprint != self.protocol.fingerprint:
            return False
        if not set(certificate.evidence).issubset(evidence):
            return False
        if not certificate.commitments or any(
            c not in {rule.name for rule in self.protocol.constraints}
            for c in certificate.commitments
        ):
            return False
        return bool(self._worlds(evidence=certificate.evidence)
                    and self._worlds(certificate.commitments)
                    and not self._worlds(certificate.commitments, certificate.evidence))

    def partition(self, experiments=None) -> Tuple[Tuple[str, ...], ...]:
        """Full E gives theoretical equivalence; a subset gives provisional groups.

        Unknown predictions are separate unresolved representatives. These groups
        are an evaluation view and must never replace the structural catalogue.
        """
        names = tuple(e.name for e in self.protocol.experiments)
        selected = names if experiments is None else tuple(experiments)
        if any(name not in names for name in selected):
            raise ValueError("unknown experiment")
        indices = tuple(names.index(name) for name in selected)
        groups = {}
        for h in sorted(self.hypotheses, key=lambda h: (h.description.total, h.name)):
            signature = (("unknown", h.name) if h.world is None else
                         ("known", tuple(self.protocol.worlds[h.world][i] for i in indices)))
            groups.setdefault(signature, []).append(h.name)
        return tuple(tuple(group) for group in groups.values())

    def macro_identifiable(self) -> bool:
        by_name = {h.name: h for h in self.hypotheses}
        return bool(self.hypotheses) and all(h.world is not None for h in self.hypotheses) and all(
            len({by_name[name].macro_answer for name in group}) == 1
            for group in self.partition()
        )

    def irreducible_against(self, hypothesis: str, lower_names) -> Optional[bool]:
        """Whether no representative in the declared finite lower class substitutes it.

        The adapter defines the allowed lower-order language, not variable count.
        None means an empty reference class or unknown predictions. True is only
        relative to this explicit class, never all imaginable lower-order models.
        """
        by_name = {h.name: h for h in self.hypotheses}
        lower_names = tuple(lower_names)
        if hypothesis not in by_name or any(n not in by_name for n in lower_names):
            raise ValueError("unknown hypothesis")
        candidate = by_name[hypothesis]
        lower = tuple(by_name[n] for n in lower_names)
        if candidate.world is None or not lower:
            return None
        if any(h.world == candidate.world for h in lower):
            return False
        if any(h.world is None for h in lower):
            return None
        return True

    def search(self, evidence=(), certificates=(), *, max_replays=None) -> HypothesisSearchReport:
        evidence = self._evidence(evidence)
        if max_replays is not None:
            _natural(max_replays)
        active = [c for c in certificates if self.validates_conflict(c, evidence)]
        compatible, pruned, rejected, undecided = [], [], [], []
        replay_checks = 0
        possible = self._worlds(evidence=evidence)
        for h in sorted(self.hypotheses, key=lambda h: (h.description.total, h.name)):
            if any(set(c.commitments).issubset(h.commitments) for c in active):
                pruned.append(h.name)
            elif h.world is None or (max_replays is not None and replay_checks >= max_replays):
                undecided.append(h.name)
            else:
                replay_checks += 1
                if h.world in possible:
                    compatible.append(h.name)
                else:
                    rejected.append(h.name)
                    certificate = self.learn_conflict(h.commitments, evidence)
                    if certificate is not None and certificate not in active:
                        active.append(certificate)
        answers = tuple(sorted({h.macro_answer for h in self.hypotheses
                                if h.name in compatible or h.name in undecided}))
        return HypothesisSearchReport(
            tuple(compatible), tuple(pruned), tuple(rejected), tuple(undecided),
            self.partition(), answers, bool(compatible) and not undecided and len(answers) == 1,
            tuple(active), replay_checks,
        )

    def _answers(self, evidence):
        worlds = self._worlds(evidence=evidence)
        return tuple(sorted({h.macro_answer for h in self.hypotheses if h.world in worlds}))

    def compress_evidence(self, evidence, *, max_subsets=10000) -> MacroEvidenceCertificate:
        """Exact cardinality search with a hard subset-count budget.

        Every subset is evaluated against the ORIGINAL catalogue, with no free
        pruning cache. Budget exhaustion returns the full valid evidence and
        minimum_cardinality=False, never a false optimality claim.
        """
        _natural(max_subsets)
        evidence = self._evidence(evidence)
        if any(h.world is None for h in self.hypotheses):
            raise ValueError("unknown predictions prevent an exact evidence certificate")
        answers = self._answers(evidence)
        if not answers:
            raise ValueError("empty version space cannot certify a macro answer")
        checked = 0
        for size in range(len(evidence) + 1):
            for subset in combinations(evidence, size):
                if checked >= max_subsets:
                    return MacroEvidenceCertificate(self.fingerprint, evidence, evidence,
                                                    answers, len(answers) == 1, False, checked)
                checked += 1
                if self._answers(subset) == answers:
                    return MacroEvidenceCertificate(self.fingerprint, evidence, subset,
                                                    answers, len(answers) == 1, True, checked)
        raise AssertionError("full evidence must preserve its own answers")

    def validates_macro(self, certificate: MacroEvidenceCertificate, evidence) -> bool:
        evidence = self._evidence(evidence)
        if (certificate.problem_fingerprint != self.fingerprint
                or certificate.full_evidence != evidence
                or not set(certificate.retained_evidence).issubset(evidence)
                or any(h.world is None for h in self.hypotheses)):
            return False
        answers = self._answers(evidence)
        if (not answers or certificate.answers != answers
                or self._answers(certificate.retained_evidence) != answers
                or certificate.determined != (len(answers) == 1)):
            return False
        if certificate.minimum_cardinality:
            for size in range(len(certificate.retained_evidence)):
                if any(self._answers(s) == answers for s in combinations(evidence, size)):
                    return False
        return True

    def next_experiment(self, evidence=()) -> Optional[SearchExperiment]:
        """Greedy unequal-answer pair coverage per cost, not information gain.

        None means no known separating experiment; it is not a success status.
        """
        evidence = self._evidence(evidence)
        possible = self._worlds(evidence=evidence)
        survivors = tuple({(h.world, h.macro_answer): h for h in self.hypotheses
                           if h.world in possible}.values())
        measured = {o.experiment for o in evidence}
        ranked = []
        for i, experiment in enumerate(self.protocol.experiments):
            if experiment.name in measured:
                continue
            covered = sum(a.macro_answer != b.macro_answer
                          and self.protocol.worlds[a.world][i] != self.protocol.worlds[b.world][i]
                          for a, b in combinations(survivors, 2))
            if covered:
                ranked.append((Fraction(covered, experiment.cost), -experiment.cost, -i, experiment))
        return max(ranked, key=lambda item: item[:3])[3] if ranked else None
