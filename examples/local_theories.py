"""Scoped local theories solve AND; extraction emits the original base program."""
from dataclasses import asdict
import json

from bidirectional_modeling.computation_search import (
    BitEvidence, BitProgram, FiniteBitTask, verify_computation_certificate,
)
from bidirectional_modeling.extensions.local_theories import (
    LocalBitTheory, LocalTheoryLibrary, certify_local_cover, verify_local_cover,
    verify_cost_preserving_translation,
)


def build_report():
    task = FiniteBitTask('two-bit conjunction / local theory fixture', 2, (0, 0, 0, 1))
    locals_ = (
        LocalBitTheory('first input zero', task.scope, 2,
            tuple(BitEvidence(x, y, 'fixture:first-zero') for x, y in task.cases[:2]),
            BitProgram(('zero', 'halt'))),
        LocalBitTheory('first input one', task.scope, 2,
            tuple(BitEvidence(x, y, 'fixture:first-one') for x, y in task.cases[2:]),
            BitProgram(('input:1', 'halt'))),
    )
    extracted = BitProgram(('input:0', 'input:1', 'and', 'halt'))
    cover = certify_local_cover(task, locals_, extracted, max_cost=9)
    library = LocalTheoryLibrary(locals_)
    result = library.search(task, max_cost=9)
    review = verify_computation_certificate(task, (), result.certificate)
    # A discovered expression can shed its intermediate structure before use.
    redundant = BitProgram(('input:0', 'input:1', 'and', 'dup', 'drop', 'halt'))
    translation = verify_cost_preserving_translation(task, redundant, extracted, max_cost=13)
    cover_review = verify_local_cover(task, locals_, cover.certificate)
    assert cover.status == cover_review == translation.status == review.status == 'valid'
    assert review.optimality == 'verified'
    return {'task': asdict(task), 'active_theories': [t.name for t in library.activate(task)],
            'cover': asdict(cover),
            'cover_review': cover_review,
            'translation': asdict(translation), 'search': asdict(result), 'review': asdict(review)}


if __name__ == '__main__':
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
