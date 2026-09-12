"""Check public file scope, connection details, credentials, and commit metadata."""
import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = frozenset({
    '.gitignore', '.github/ISSUE_TEMPLATE/bug.md', '.github/ISSUE_TEMPLATE/feature.md',
    '.github/pull_request_template.md', '.github/workflows/tests.yml',
    '.githooks/pre-commit', '.githooks/pre-push',
    'README.md', 'AGENT_BUILDING_GUIDE.md', 'CONTRIBUTING.md',
    'builder.py', 'rcon.py', 'dimensions.py', 'configure_dimensions.py', 'set_area.py',
    'mcp_server.py', 'mc_builder.sc', 'config.example.json',
    'test_builder.py', 'test_dimensions.py', 'test_publication.py',
    'docs/PROJECT_STRUCTURE.md', 'docs/MULTI_DIMENSION.md', 'docs/ROADMAP.md',
    'docs/PUBLICATION.md', 'scripts/check_publication.py', 'plans/example-preview-only.json',
})
PATTERNS = {
    'machine-specific path': r'/(?:root|home|Users)/[A-Za-z0-9_.-]+|[A-Za-z]:[\\/]+Users[\\/]+',
    'access token': r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}',
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'credentials in URL': r'https?://[^\s/@:]+:[^\s/@]+@',
    'remote SSH destination': r'\bssh\s+[^\n]*\b[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+',
}


def scan_text(text):
    problems = [label for label, pattern in PATTERNS.items() if re.search(pattern, text)]
    for match in re.finditer(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text):
        try:
            address = ipaddress.ip_address(match[0])
        except ValueError:
            continue
        if not address.is_loopback and not address.is_unspecified:
            problems.append('network address')
    for email in re.findall(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text):
        if not email.endswith('@users.noreply.github.com') and email != 'noreply@github.com':
            problems.append('personal email address')
    return sorted(set(problems))


def git(*args, root=ROOT):
    return subprocess.check_output(['git', *args], cwd=root)


def scan_tree(ref=None, root=ROOT):
    names = git('ls-files', '-z', root=root) if ref is None else git('ls-tree', '-r', '--name-only', '-z', ref, root=root)
    problems = []
    for name in names.decode().split('\0'):
        if not name:
            continue
        if name not in PUBLIC_FILES:
            problems.append((name, 'file is outside the public allowlist'))
            continue
        data = git('show', (':' if ref is None else ref+':') + name, root=root)
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            problems.append((name, 'unexpected binary file'))
            continue
        problems.extend((name, label) for label in scan_text(text))
    return problems


def scan_history(ref, root=ROOT):
    problems = []
    for commit in git('rev-list', ref, root=root).decode().splitlines():
        problems.extend((commit[:12]+':'+name, reason) for name, reason in scan_tree(commit, root))
        metadata = git('show', '-s', '--format=%an%n%ae%n%cn%n%ce%n%B', commit, root=root).decode()
        problems.extend((commit[:12]+':metadata', reason) for reason in scan_text(metadata))
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--staged', action='store_true')
    group.add_argument('--history')
    group.add_argument('--pre-push', action='store_true')
    parser.add_argument('remote', nargs='*')
    args = parser.parse_args()
    if args.staged:
        problems = scan_tree()
    elif args.history:
        problems = scan_history(args.history)
    else:
        problems = []
        for line in sys.stdin:
            fields = line.split()
            if len(fields) != 4:
                raise SystemExit('Invalid pre-push input')
            if set(fields[1]) == {'0'}:
                continue
            # Check all ancestors, including history reintroduced by a merge.
            problems.extend(scan_history(fields[1]))
    for name, reason in problems:
        print(name + ': ' + reason)
    if problems:
        raise SystemExit('Publication check failed; keep deployment details local.')
    print('Publication check passed.')


if __name__ == '__main__':
    main()
