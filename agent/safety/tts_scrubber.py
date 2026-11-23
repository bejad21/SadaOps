"""Secret and credential scrubber applied prior to TTS synthesis."""

from __future__ import annotations

import math
import re
from collections import Counter

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[redacted private key]",
    ),
    (
        "github_token",
        re.compile(
            r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"gh[ous]_[A-Za-z0-9]{20,})\b"
        ),
        "[redacted credential]",
    ),
    (
        "api_key",
        re.compile(
            r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{12,}|"
            r"sk_(?:live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
            r"xox[bpas]-[A-Za-z0-9-]{10,})\b"
        ),
        "[redacted credential]",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[redacted token]",
    ),
    (
        "bearer",
        re.compile(r"(?i)\bBearer\s+(?:\[redacted token\]|[A-Za-z0-9._~+/=-]{8,})"),
        "Bearer [redacted credential]",
    ),
    (
        "dsn_password",
        re.compile(
            r"(?i)\b(https?|postgres(?:ql)?|redis|mysql|mongodb(?:\+srv)?|amqp)://"
            r"(?:[^:\s/@]+:[^@\s]+|[A-Za-z0-9_~+.-]{16,})@"
        ),
        r"\1://[redacted]@",
    ),
    (
        "kv_secret",
        re.compile(
            r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|api[_-]?key|secret|access[_-]?token|"
            r"auth[_-]?token))\s*[:=]\s*['\"]?([^\s'\",;]{8,})['\"]?"
        ),
        r"\1=[redacted credential]",
    ),
]

_TOKEN_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9_/-])[A-Za-z0-9_~+=-]{28,}(?![A-Za-z0-9_/-])")


def _shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((cnt / length) * math.log2(cnt / length) for cnt in counts.values())


def _is_high_entropy_secret(token: str) -> bool:
    if _UUID_RE.match(token):
        return False
    has_digit = any(c.isdigit() for c in token)
    has_upper = any(c.isupper() for c in token)
    has_lower = any(c.islower() for c in token)
    if not (has_digit and has_upper and has_lower):
        return False
    return _shannon_entropy(token) >= 4.0


def _redact_high_entropy(text: str) -> tuple[str, bool]:
    found = False
    parts: list[str] = []
    last = 0
    for match in _TOKEN_CANDIDATE_RE.finditer(text):
        start, end = match.span()
        prefix = text[max(0, start - 10) : start].lower()
        suffix = text[end : end + 1]
        if ("http://" in prefix or "https://" in prefix) and suffix != "@":
            continue
        token = match.group(0)
        if _is_high_entropy_secret(token):
            parts.append(text[last:start])
            parts.append("[redacted credential]")
            last = end
            found = True
    if not found:
        return text, False
    parts.append(text[last:])
    return "".join(parts), True


def scrub_for_tts(text: str) -> tuple[str, list[str]]:
    """Redact credentials, tokens, and high-entropy secrets before TTS synthesis."""
    if not text:
        return text, []

    categories: list[str] = []
    scrubbed = text
    for category, pattern, replacement in _PATTERNS:
        scrubbed, count = pattern.subn(replacement, scrubbed)
        if count > 0:
            categories.append(category)

    scrubbed, entropy_hit = _redact_high_entropy(scrubbed)
    if entropy_hit:
        categories.append("high_entropy_secret")

    return scrubbed, categories


def contains_secret(text: str) -> bool:
    """Return True if any secret or credential pattern is detected in text."""
    if not text:
        return False
    _, categories = scrub_for_tts(text)
    return len(categories) > 0
