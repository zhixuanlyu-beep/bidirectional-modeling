"""Report statement and branch coverage separately; gate both without rounding."""
import argparse
import json
from pathlib import Path


def report(data):
    if not data.get('meta', {}).get('branch_coverage'):
        raise ValueError('branch measurement is required; run coverage with branch=True')
    totals = data['totals']
    rows = []
    passed = True
    for label, covered_key, total_key, threshold in (
        ('Statements', 'covered_lines', 'num_statements', 93.0),
        ('Branches', 'covered_branches', 'num_branches', 86.0),
    ):
        covered, total = totals[covered_key], totals[total_key]
        if total <= 0 or not 0 <= covered <= total:
            raise ValueError('invalid or empty coverage counts for ' + label)
        percent = 100 * covered / total
        ok = percent >= threshold
        passed = passed and ok
        rows.append(f'| {label} | {covered}/{total} | {percent:.2f}% | {threshold:.2f}% | {"PASS" if ok else "FAIL"} |')
    return passed, '\n'.join([
        '| Metric | Covered/total | Coverage | Minimum | Result |',
        '| --- | ---: | ---: | ---: | --- |', *rows,
    ]) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('json_report')
    parser.add_argument('--summary', help='append the same table to a CI job summary')
    args = parser.parse_args()
    passed, table = report(json.loads(Path(args.json_report).read_text()))
    print(table)
    if args.summary:
        with Path(args.summary).open('a') as stream:
            stream.write(table)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
