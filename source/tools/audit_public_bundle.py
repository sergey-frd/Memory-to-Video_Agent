"""Read-only checks for a prepared publication tree; never contacts GitHub."""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.project_publication import _secret_hits, _is_excluded_file_name


def audit(root, private_terms=()):
    root = Path(root).resolve()
    manifest = json.loads((root / 'data/publication_manifest.json').read_text(encoding='utf-8'))
    managed = set(manifest['managed_files']) | {'data/publication_manifest.json'}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}
    errors = [{'file': rel, 'issue': 'unmanaged_file'} for rel in sorted(actual - managed)]
    errors += [{'file': rel, 'issue': 'missing_managed_file'} for rel in sorted(managed - actual)]
    binary = {'.jpg', '.jpeg', '.png', '.webp', '.mp4', '.mov', '.wav', '.mp3', '.prproj', '.zip', '.pyc'}
    python_count = 0
    for rel in sorted(actual):
        p = root / rel
        if not p.resolve().is_relative_to(root) or p.is_symlink():
            errors.append({'file': rel, 'issue': 'path_escape'})
            continue
        if p.suffix.lower() in binary or _is_excluded_file_name(p.name):
            errors.append({'file': rel, 'issue': 'private_or_runtime_file'})
            continue
        try:
            text = p.read_text(encoding='utf-8-sig')
        except UnicodeError:
            errors.append({'file': rel, 'issue': 'unexpected_binary'})
            continue
        hits = _secret_hits(text)
        if hits:
            errors.append({'file': rel, 'issue': 'secret_pattern', 'patterns': hits})
        if any(term.casefold() in (rel + '\n' + text).casefold() for term in private_terms):
            errors.append({'file': rel, 'issue': 'private_term'})
        if p.suffix == '.py':
            try:
                ast.parse(text, filename=rel)
                python_count += 1
            except SyntaxError as exc:
                errors.append({'file': rel, 'issue': 'python_syntax', 'line': exc.lineno})
        if p.suffix == '.json':
            try:
                json.loads(text)
            except ValueError:
                errors.append({'file': rel, 'issue': 'invalid_json'})
    return {'status': 'PASS' if not errors else 'FAIL', 'version': manifest['publication_version'],
            'files': len(actual), 'python_parsed': python_count, 'errors': errors,
            'scope': 'Current bundle tree only; no Git history audit and no upload.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--private-term', action='append', default=[])
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = audit(args.bundle, args.private_term)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + '\n', encoding='utf-8')
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(payload)
    sys.exit(0 if result['status'] == 'PASS' else 1)
