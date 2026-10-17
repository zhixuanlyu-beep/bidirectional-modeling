"""Check that documented claim contracts retain concrete source/test references."""
import ast
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/core_contracts.json').read_text(encoding='utf-8'))
    versions = ast.parse((root / 'src/bidirectional_modeling/versions.py').read_text(encoding='utf-8'))
    revision = next(ast.literal_eval(node.value) for node in versions.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'CORE_CONTRACT_REVISION' for target in node.targets))
    if manifest['revision'] != revision:
        raise ValueError('core contract revision differs from runtime metadata')
    contracts = manifest['contracts']
    identifiers = [contract['id'] for contract in contracts]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('duplicate core contract')
    text = (root / 'docs/core_contracts.md').read_text(encoding='utf-8')
    for contract in contracts:
        identifier = contract['id']
        if identifier not in text:
            raise ValueError('undocumented contract: ' + identifier)
        for key, folder in (('sources', 'src/bidirectional_modeling'), ('docs', 'docs')):
            if not contract[key]:
                raise ValueError('empty mapping: ' + identifier)
            for path in contract[key]:
                if not (root / folder / path).is_file():
                    raise ValueError('missing contract reference: ' + path)
        if not contract['tests']:
            raise ValueError('no regression tests: ' + identifier)
        for selector in contract['tests']:
            module, class_name, method = selector.split('.')
            tree = ast.parse((root / 'tests' / (module + '.py')).read_text(encoding='utf-8'))
            found = any(node.name == class_name and any(isinstance(item, ast.FunctionDef)
                and item.name == method for item in node.body)
                for node in tree.body if isinstance(node, ast.ClassDef))
            if not found or not method.startswith('test_'):
                raise ValueError('missing contract regression: ' + selector)
    print('Checked %d core contracts and %d regression references' % (
        len(contracts), sum(len(contract['tests']) for contract in contracts)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
