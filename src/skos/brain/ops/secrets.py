"""Fail-closed secret lint for private ops canon before projection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecretFinding:
    """A redacted secret-lint finding; never contains the matched value."""

    path: str
    line: int
    rule: str


_RULES = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(
        r"(?i)\b(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*[^\s<$({][^\s]{7,}"
    ),
    "uri-credential": re.compile(r"(?i)\b(?:postgres(?:ql)?|https?|ssh)://[^\s/:]+:[^\s/@]+@"),
}


def lint_text(text: str, *, path: str = "<memory>") -> list[SecretFinding]:
    """Return redacted findings for secret-shaped material in *text*."""
    findings: list[SecretFinding] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for rule, pattern in _RULES.items():
            if pattern.search(line):
                findings.append(SecretFinding(path=path, line=line_no, rule=rule))
    return findings


def lint_tree(root: str | Path) -> list[SecretFinding]:
    """Lint Markdown canon under *root*, rejecting unreadable/non-regular files."""
    base = Path(root).resolve(strict=True)
    findings: list[SecretFinding] = []
    for path in sorted(base.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            findings.append(SecretFinding(str(path), 0, "unsafe-file"))
            continue
        findings.extend(lint_text(path.read_text(encoding="utf-8"), path=str(path)))
    return findings
