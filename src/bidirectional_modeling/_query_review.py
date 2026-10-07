"""Small review rules over raw declarations, independent of query generation.

No generator, response index, invocation snapshot or search filter is used here.
Identity encoding and cooperative budget accounting remain shared infrastructure.
"""


def review_world(problem, world, commitments, evidence, budget):
    for name in commitments:
        budget.consume('constraint_checks')
        rule = next(rule for rule in problem.protocol.constraints if rule.name == name)
        if world not in rule.worlds:
            return False
    names = tuple(e.name for e in problem.protocol.experiments)
    for observation in evidence:
        budget.consume('response_checks')
        if problem.protocol.worlds[world][names.index(observation.experiment)] != observation.response:
            return False
    return True


def review_absence(problem, query, budget):
    """Return (absence established, nonempty metadata); None means unresolved."""
    from .search import SearchObservation
    from .search_queries import ConstraintQuery, MacroAlternativeQuery
    budget.consume('query_checks')
    if isinstance(query, (ConstraintQuery, MacroAlternativeQuery)):
        names = tuple(e.name for e in problem.protocol.experiments)
        for observation in query.evidence:
            if not isinstance(observation, SearchObservation) or observation.experiment not in names:
                raise ValueError('unknown experiment or invalid observation')
            column = names.index(observation.experiment)
            for row in problem.protocol.worlds:
                budget.consume('response_checks')
                if row[column] == observation.response:
                    break
            else:
                raise ValueError('response outside declared universe')
        if isinstance(query, ConstraintQuery):
            for world in range(len(problem.protocol.worlds)):
                budget.consume('candidate_checks')
                if review_world(problem, world, query.commitments, query.evidence, budget):
                    return False, None
            return True, None
        unknown, nonempty = False, False
        for candidate in problem.hypotheses:
            budget.consume('candidate_checks')
            if candidate.world is None:
                unknown = True
            elif review_world(problem, candidate.world, (), query.evidence, budget):
                nonempty = True
                if candidate.macro_answer != query.answer:
                    return False, True
        return (None if unknown else True), nonempty
    by_name = {candidate.name: candidate for candidate in problem.hypotheses}
    world = by_name[query.candidate].world
    if not query.lower_names or world is None:
        return None, None
    unknown = False
    for name in query.lower_names:
        budget.consume('candidate_checks')
        candidate = by_name[name]
        if candidate.world is None:
            unknown = True
        elif candidate.world == world:
            return False, None
    return (None if unknown else True), None
