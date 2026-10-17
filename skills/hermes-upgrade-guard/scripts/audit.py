#!/usr/bin/env python3
"""Read-only customization drift checks; not runtime acceptance."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess

import yaml


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check(home, repo, policy):
    checks = []
    warnings = []

    def record(name, ok, detail):
        checks.append({'name': name, 'passed': bool(ok), 'detail': detail})

    try:
        config = yaml.safe_load((home / 'config.yaml').read_text())
        if not isinstance(config, dict):
            raise ValueError('Configuration root is not a mapping')
        for dotted, expected in policy['config'].items():
            value = config
            for part in dotted.split('.'):
                value = value.get(part) if isinstance(value, dict) else None
            ok = type(value) is type(expected) and value == expected
            record('config:' + dotted, ok, 'matches' if ok else 'missing or changed; inspect locally')
        updates = config.get('updates') or {}
        if updates.get('pre_update_backup') in (False, 'off'):
            warnings.append('Automatic pre-update backup is off; explicit verified backup is required.')
        if updates.get('non_interactive_local_changes') == 'discard':
            warnings.append('Updater is configured to discard local source edits.')
    except Exception as exc:
        record('config:read', False, type(exc).__name__)

    for section in policy['soul_sections']:
        try:
            lines = (home / 'SOUL.md').read_text().splitlines()
            starts = [i for i, line in enumerate(lines) if line == section['heading']]
            if len(starts) != 1:
                raise ValueError('Missing or duplicate heading')
            start = starts[0]
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
            content = '\n'.join(lines[start:end]).strip().encode()
            record('soul:' + section['heading'], digest(content) == section['sha256'], 'normalized section hash')
        except Exception as exc:
            record('soul:' + section['heading'], False, type(exc).__name__)

    for relative, expected_hash in policy['files'].items():
        try:
            path = home / relative
            ok = digest(path.read_bytes()) == expected_hash
            record('file:' + relative, ok, 'SHA-256 comparison')
        except Exception as exc:
            record('file:' + relative, False, type(exc).__name__)

    try:
        tree = ast.parse((repo / 'gateway/run_startup.py').read_text())
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == '_recover_unclean_sessions']
        assignments = [n for f in functions for n in ast.walk(f) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'max_age' for t in n.targets)]
        found = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == '_auto_continue_freshness_window' for a in assignments for n in ast.walk(a.value))
        record('recovery:configured-window-reference', found, 'Structural check only; upstream refactors require behavior tests')
    except Exception as exc:
        record('recovery:configured-window-reference', False, type(exc).__name__)

    commit = None
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True, stderr=subprocess.DEVNULL, timeout=10).strip()
    except Exception:
        warnings.append('Could not resolve source commit.')
    if commit != policy['base_commit']:
        warnings.append('Source revision differs from baseline; compatibility and runtime revalidation required.')
    return {'static_passed': all(c['passed'] for c in checks), 'runtime_verified': False,
            'source_commit': commit, 'baseline_commit': policy['base_commit'],
            'checks': checks, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')))
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    policy_path = Path(__file__).resolve().parent.parent / 'references/baseline.json'
    policy = json.loads(policy_path.read_text())
    result = check(args.home.expanduser().resolve(), args.repo.expanduser().resolve(), policy)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['static_passed'] else 1)


if __name__ == '__main__':
    main()
