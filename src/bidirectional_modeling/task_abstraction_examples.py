"""Declared fixtures illustrating semantic refinement and hidden resources."""
from dataclasses import asdict, replace

from .task_abstraction import (
    FiniteOperation, FiniteTaskAbstraction, analyze_task_abstraction,
    verify_abstraction_witness, verify_task_abstraction,
)


def fairness_abstractions():
    hours = FiniteTaskAbstraction(
        scope='Two declared schedules; equal hours initially define fairness',
        states=('equal_hours_day_only', 'equal_hours_night_only'),
        macro_states=('equal_hours',), judgments=('equal_hours',),
        projection=(0, 0), responses=(('yes',), ('yes',)), complete=True,
        assumptions=('Schedule data are a fixture, not real personnel records',))
    clarified = replace(hours,
        scope='Task owner additionally distinguishes night burden',
        judgments=('equal_hours', 'night_burden_acceptable'),
        responses=(('yes', 'yes'), ('yes', 'no')))
    refined = replace(clarified,
        macro_states=('day_burden', 'night_burden'), projection=(0, 1))
    return hours, clarified, refined


def resource_abstractions():
    # Each pending task looks identical; its occupied resource changes composition.
    outputs = [None] * 16
    outputs[0], outputs[1], outputs[4], outputs[5] = 2, 3, 3, 3
    coarse = FiniteTaskAbstraction(
        scope='Combine two pending tasks under a declared one-second deadline',
        states=('free_task', 'occupied_task', 'on_time', 'late'),
        macro_states=('pending', 'on_time', 'late'),
        judgments=('deadline_status',), projection=(0, 0, 1, 2),
        responses=(('pending',), ('pending',), ('on_time',), ('late',)),
        operations=(FiniteOperation('combine', 2, tuple(outputs)),), complete=True,
        assumptions=('All sixteen input tuples have declared applicability',))
    refined = replace(coarse, macro_states=coarse.states, projection=(0, 1, 2, 3))
    return coarse, refined


def build_abstraction_demo_report():
    def report(problem):
        result = analyze_task_abstraction(problem)
        return dict(asdict(result), review=(
            verify_task_abstraction(problem, result.certificate) if result.certificate
            else verify_abstraction_witness(problem, result.witness)))
    hours, clarified, refined = fairness_abstractions()
    coarse_resource, refined_resource = resource_abstractions()
    old_certificate = analyze_task_abstraction(hours).certificate
    return {
        'fairness': {'initial_spec': report(hours), 'clarified_spec': report(clarified),
            'refined_model': report(refined),
            'old_certificate_under_new_spec': verify_task_abstraction(clarified, old_certificate)},
        'resource_composition': {'coarse_model': report(coarse_resource),
            'refined_model': report(refined_resource)},
        'boundaries': ['Fixtures declare judgments; they do not prove human intent',
            'Finite factorization does not prove a plan succeeds or a solver is complete',
            'None means known undefined, never an unknown result'],
    }
