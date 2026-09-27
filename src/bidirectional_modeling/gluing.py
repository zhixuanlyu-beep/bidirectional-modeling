"""Compatibility import; use bidirectional_modeling.extensions.gluing."""
from .extensions.gluing import LocalDescription, GluingProblem, GluingReport, solve_gluing, verify_gluing_report

__all__ = ['LocalDescription', 'GluingProblem', 'GluingReport', 'solve_gluing', 'verify_gluing_report']
