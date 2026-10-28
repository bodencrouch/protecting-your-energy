"""Credential-aware presentation of already verified archive results.

Source verification precedes this layer. Its digests describe displayed strings,
not original files or full messages; provenance digests are left intact.
"""
from __future__ import annotations

import hashlib
import re

VERSION = 'credential-display-v1'
PLACEHOLDER = '[REDACTED CREDENTIAL]'
_KEY = (r'(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|'
        r'auth[_-]?token|password|passwd|client[_-]?secret|secret[_-]?key|'
        r'private[_-]?key|token|secret)')
_FIELD = re.compile(r'(?i)^' + _KEY + r'$')
_ASSIGNMENT = re.compile(
    r'(?i)(?<![\w-])(?P<label>[\"\']?' + _KEY + r'[\"\']?\s*[:=]\s*)'
    r'(?P<value>\[REDACTED CREDENTIAL\]|\"(?:\\.|[^\"\\])*\"?|\'(?:\\.|[^\'\\])*\'?|[^\s,;\}\]\)]+)')
_AUTH = re.compile(r'(?i)(\bauthorization[\"\']?\s*[:=]\s*[\"\']?(?:bearer|basic)\s+)(?!\[REDACTED CREDENTIAL\])([^\s\"\',;]+)')
_TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|'
                    r'github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|'
                    r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b')
_URL = re.compile(r'(?i)(\b[a-z][a-z0-9+.-]*://)([^\s/@]+:[^\s/@]+)@')
_PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?'
                          r'(?:-----END (?:[A-Z ]+ )?PRIVATE KEY-----|\Z)')


def redact_text(text: str) -> tuple[str, int]:
    """Redact recognizable credential syntax without replacing ordinary prose."""
    count = 0
    def replace(pattern, substitution):
        nonlocal text, count
        text, found = pattern.subn(substitution, text)
        count += found
    replace(_PRIVATE_KEY, PLACEHOLDER)
    replace(_AUTH, lambda m: m[1] + PLACEHOLDER)
    replace(_ASSIGNMENT, lambda m: m[0] if m['value'] == PLACEHOLDER else m['label'] + PLACEHOLDER)
    replace(_TOKEN, PLACEHOLDER)
    replace(_URL, lambda m: m[1] + PLACEHOLDER + '@')
    return text, count


def sanitize_payload(result: dict) -> dict:
    """Return a redacted copy, adding a receipt only when strings change.

    Text ranges and parent hashes continue to identify the pre-render source.
    A changed field's receipt explicitly distinguishes both display digests.
    """
    fields = []
    def walk(value, pointer='', key=None):
        if isinstance(value, dict):
            return {k: walk(v, pointer + '/' + k.replace('~', '~0').replace('/', '~1'), k)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, pointer + '/' + str(i)) for i, v in enumerate(value)]
        if not isinstance(value, str):
            return value
        rendered, count = redact_text(value)
        if key and _FIELD.fullmatch(key) and value and value != PLACEHOLDER:
            rendered, count = PLACEHOLDER, max(1, count)
        if rendered != value:
            fields.append({'pointer': pointer, 'redactions': count,
                           'original_display_text_sha256': hashlib.sha256(value.encode()).hexdigest(),
                           'rendered_text_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                           'original_display_characters': len(value), 'rendered_characters': len(rendered)})
        return rendered
    rendered = walk(result)
    if fields:
        prior = rendered.get('display_redaction')
        rendered['display_redaction'] = {
            'version': VERSION, 'redacted': True,
            'redactions': sum(field['redactions'] for field in fields), 'fields': fields,
            'scope': 'Recognizable credential syntax; unlabeled or unfamiliar secrets may remain.',
            'source_integrity': 'Source checks and provenance hashes precede rendering and remain unchanged.',
            'text_coordinates': 'Existing ranges, spans and text hashes refer to pre-render source text.'}
        if isinstance(prior, dict):
            rendered['display_redaction']['prior_receipt'] = prior
    return rendered


display_result = sanitize_payload
