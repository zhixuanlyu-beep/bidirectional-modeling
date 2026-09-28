"""The CI gate must not confuse combined coverage with branch coverage."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'tools' / 'check_coverage.py'
spec = importlib.util.spec_from_file_location('coverage_gate', path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CoverageGateTests(unittest.TestCase):
    def data(self, lines=9300, branches=8600):
        return {'meta': {'branch_coverage': True}, 'totals': {
            'covered_lines': lines, 'num_statements': 10000,
            'covered_branches': branches, 'num_branches': 10000}}

    def test_each_threshold_is_independent_and_uses_unrounded_values(self):
        self.assertTrue(gate.report(self.data())[0])
        self.assertFalse(gate.report(self.data(lines=9299, branches=10000))[0])
        self.assertFalse(gate.report(self.data(lines=10000, branches=8599))[0])
        data = self.data(); data['totals']['num_branches'] = 1000000
        data['totals']['covered_branches'] = 859999  # Displays 86.00, still fails.
        self.assertFalse(gate.report(data)[0])

    def test_missing_branch_measurement_is_not_silently_accepted(self):
        data = self.data(); data['meta']['branch_coverage'] = False
        with self.assertRaises(ValueError): gate.report(data)

    def test_empty_or_inconsistent_counts_fail_closed(self):
        for key, value in (('num_statements', 0), ('num_branches', 0),
                           ('covered_branches', -1), ('covered_lines', 10001)):
            data = self.data(); data['totals'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): gate.report(data)
