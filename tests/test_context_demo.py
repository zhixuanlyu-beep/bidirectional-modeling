import unittest
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

from bidirectional_modeling.cli import main
from bidirectional_modeling.context_examples import build_context_demo_report


class ContextDemoTests(unittest.TestCase):
    def test_end_to_end_acceptance(self):
        report = build_context_demo_report()
        self.assertEqual(report['context_extension']['split_source_worlds'], 2)
        self.assertEqual(report['expanded_domain_transport']['status'], 'not_applicable')
        self.assertEqual(report['local_gluing']['status'], 'absent')
        self.assertTrue(report['local_gluing']['overlap_consistent'])
        self.assertEqual(report['boolean_reconstruction']['lower_substitute_status'], 'absent')
        self.assertEqual(report['boolean_reconstruction']['answer'], 'interaction')
        self.assertEqual(report['partial_screening']['simulations_used'], 2)
        self.assertEqual(report['partial_screening']['full_matrix_simulations'], 8)
        self.assertEqual(report['macro_sufficiency']['retained_size'], 2)
        self.assertEqual(report['macro_sufficiency']['verification'], 'valid')

    def test_json_command(self):
        output = io.StringIO()
        with patch('sys.argv', ['bidirectional-modeling', 'context-demo', '--json']), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())['macro_sufficiency']['verification'], 'valid')
