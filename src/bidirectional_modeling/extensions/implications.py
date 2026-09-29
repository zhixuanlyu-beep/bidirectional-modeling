"""Optional, finite attribute implications with explicit counterexamples."""
from dataclasses import dataclass
from itertools import combinations
from math import comb

from ..structural import fingerprint_value


@dataclass(frozen=True)
class AttributeObject:
    name: str
    attributes: tuple

    def __post_init__(self):
        attributes = tuple(self.attributes)
        if type(self.name) is not str or not self.name.strip():
            raise ValueError('object name must be a nonempty string')
        if any(type(value) is not str or not value.strip() for value in attributes):
            raise ValueError('attributes must be nonempty strings')
        if len(set(attributes)) != len(attributes):
            raise ValueError('duplicate attribute on an object')
        object.__setattr__(self, 'attributes', attributes)


@dataclass(frozen=True)
class AttributeContext:
    """Caller-declared finite object/attribute domain, not authenticated reality."""
    attributes: tuple
    objects: tuple
    complete: bool = False
    source: str = 'caller declaration'

    def __post_init__(self):
        attributes, objects = tuple(self.attributes), tuple(self.objects)
        if (not attributes or len(set(attributes)) != len(attributes)
                or any(type(value) is not str or not value.strip() for value in attributes)):
            raise ValueError('declare distinct nonempty attribute names')
        if (not objects or any(not isinstance(obj, AttributeObject) for obj in objects)
                or len({obj.name for obj in objects}) != len(objects)):
            raise ValueError('declare distinct finite objects')
        if any(not set(obj.attributes) <= set(attributes) for obj in objects):
            raise ValueError('object uses an undeclared attribute')
        if type(self.complete) is not bool or type(self.source) is not str or not self.source.strip():
            raise ValueError('declare completeness and provenance explicitly')
        object.__setattr__(self, 'attributes', attributes)
        object.__setattr__(self, 'objects', objects)

    @property
    def fingerprint(self):
        return fingerprint_value(('attribute-context-v1', self.attributes,
            tuple((o.name, o.attributes) for o in self.objects), self.complete,
            self.source))


@dataclass(frozen=True)
class Implication:
    premises: tuple
    conclusion: str

    def __post_init__(self):
        premises = tuple(self.premises)
        if (not premises or len(set(premises)) != len(premises)
                or any(type(value) is not str or not value.strip() for value in premises)
                or type(self.conclusion) is not str or not self.conclusion.strip()
                or self.conclusion in premises):
            raise ValueError('implication needs distinct premises and a new conclusion')
        object.__setattr__(self, 'premises', premises)


@dataclass(frozen=True)
class ImplicationAssessment:
    implication: Implication
    context_fingerprint: str
    status: str  # verified / refuted / undecided
    reason: str
    supporting_objects: tuple
    counterexample: object = None
    checked_objects: int = 0


def check_implication(context, implication, *, max_object_checks=None):
    """Report a concrete refuter, or a finite-domain conclusion if complete."""
    if not isinstance(context, AttributeContext) or not isinstance(implication, Implication):
        raise TypeError('supply a declared context and implication')
    if max_object_checks is not None and (type(max_object_checks) is not int or max_object_checks < 0):
        raise ValueError('max_object_checks must be nonnegative or None')
    if (any(name not in context.attributes for name in implication.premises)
            or implication.conclusion not in context.attributes):
        raise ValueError('implication outside the declared attribute domain')
    support = []
    checked = 0
    def result(status, reason, counterexample=None):
        return ImplicationAssessment(implication, context.fingerprint, status,
            reason, tuple(support), counterexample, checked)
    for obj in context.objects:
        if max_object_checks is not None and checked >= max_object_checks:
            return result('undecided', 'object_budget_exhausted')
        checked += 1
        if set(implication.premises) <= set(obj.attributes):
            support.append(obj.name)
            if implication.conclusion not in obj.attributes:
                return result('refuted', 'object_counterexample', obj)
    if not support:
        return result('undecided', 'no_supporting_object')
    return result('verified', 'complete_declared_domain') if context.complete else result(
        'undecided', 'object_domain_incomplete')


def verify_implication_assessment(context, assessment, *, max_object_checks=None):
    """Independently inspect finite object rows and the exact declared claim."""
    if not isinstance(context, AttributeContext) or not isinstance(assessment, ImplicationAssessment):
        return 'invalid'
    if max_object_checks is not None and (type(max_object_checks) is not int or max_object_checks < 0):
        raise ValueError('max_object_checks must be nonnegative or None')
    if assessment.context_fingerprint != context.fingerprint:
        return 'invalid'
    claim = assessment.implication
    if (not isinstance(claim, Implication)
            or not set(claim.premises).issubset(context.attributes)
            or claim.conclusion not in context.attributes):
        return 'invalid'
    support = []
    for checked, obj in enumerate(context.objects):
        if max_object_checks is not None and checked >= max_object_checks:
            return 'undecided'
        if all(name in obj.attributes for name in claim.premises):
            support.append(obj.name)
            if claim.conclusion not in obj.attributes:
                expected = ImplicationAssessment(claim, context.fingerprint, 'refuted',
                    'object_counterexample', tuple(support), obj, checked + 1)
                return 'valid' if assessment == expected else 'invalid'
    reason = 'no_supporting_object' if not support else (
        'complete_declared_domain' if context.complete else 'object_domain_incomplete')
    status = 'undecided' if reason != 'complete_declared_domain' else 'verified'
    expected = ImplicationAssessment(claim, context.fingerprint, status, reason,
                                     tuple(support), None, len(context.objects))
    return 'valid' if assessment == expected else 'invalid'


@dataclass(frozen=True)
class ImplicationExploration:
    assessments: tuple
    exhaustive: bool
    candidate_count: int


def explore_implications(context, *, max_premises=2, max_candidates=256,
                         max_object_checks=None):
    """Enumerate a bounded finite proposal set, including refuted proposals."""
    if type(max_premises) is not int or max_premises < 1:
        raise ValueError('max_premises must be positive')
    if type(max_candidates) is not int or max_candidates < 0:
        raise ValueError('max_candidates must be nonnegative')
    attributes = context.attributes
    total = sum(comb(len(attributes), size) * (len(attributes) - size)
                for size in range(1, min(max_premises, len(attributes) - 1) + 1))
    assessments = []
    for size in range(1, min(max_premises, len(attributes) - 1) + 1):
        for premises in combinations(attributes, size):
            for conclusion in attributes:
                if conclusion in premises:
                    continue
                if len(assessments) >= max_candidates:
                    return ImplicationExploration(tuple(assessments), False, total)
                assessments.append(check_implication(context,
                    Implication(premises, conclusion),
                    max_object_checks=max_object_checks))
    return ImplicationExploration(tuple(assessments), True, total)
