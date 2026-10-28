"""Optional RAM-only evidence locations and artifact disposition contracts.

Validation establishes bounded schema and exact locations, never semantic truth.
Callers supply already display-sanitized inputs; this module performs no I/O.
"""
from __future__ import annotations

import hashlib
import json
import re

FIELDS = ('actor', 'experiencer', 'target', 'relation', 'polarity', 'modality', 'condition')
PLACEHOLDER = '[REDACTED CREDENTIAL]'
HANDLE_LIMIT = 80
TEXT_LIMIT = 512 * 1024
CATALOG_LIMIT = 1024 * 1024
VALIDATION_CODES = frozenset(('meaning-schema-invalid', 'meaning-bound-exceeded',
    'meaning-handle-unknown', 'meaning-quote-not-exact-unique', 'meaning-quote-redacted',
    'meaning-primary-support-required', 'meaning-candidate-duplicated',
    'meaning-disposition-keys-invalid', 'meaning-direct-bundle-required',
    'meaning-input-conflict', 'meaning-captured-context-changed'))


class MeaningError(ValueError):
    def __init__(self, code, *, quote_failure=None):
        super().__init__(code)
        self.quote_failure = quote_failure


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def _keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise MeaningError('meaning-schema-invalid')


def _text(value, limit):
    if not isinstance(value, str) or not value.strip():
        raise MeaningError('meaning-schema-invalid')
    if len(value) > limit or len(value.encode()) > limit * 4:
        raise MeaningError('meaning-bound-exceeded')


def _list(value, limit):
    if not isinstance(value, list):
        raise MeaningError('meaning-schema-invalid')
    if len(value) > limit:
        raise MeaningError('meaning-bound-exceeded')
    return value


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False}


def _string(limit):
    return {'type': 'string', 'minLength': 1, 'maxLength': limit}


def scope_schema():
    return _object({field: {'anyOf': [{'type': 'null'}, _string(512)]} for field in FIELDS})


def validate_scope(value):
    _keys(value, FIELDS)
    for field in FIELDS:
        if value[field] is not None:
            _text(value[field], 512)


def anchor_schema(catalog, *, primary=False):
    handles = [handle for handle, row in catalog.items() if not primary or row['primary_support']]
    return _object({'handle': {'type': 'string', 'enum': handles}, 'quote': _string(1024)})


def bundle_schema(catalog):
    anchors = {'type': 'array', 'items': anchor_schema(catalog), 'maxItems': 4}
    candidate = _object({'id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,32}$'},
        'interpretation': _string(1024), 'meaning': scope_schema(),
        'artifact_difference': _string(1024), 'support': {**anchors, 'minItems': 1},
        'counterevidence': anchors})
    return _object({'candidates': {'type': 'array', 'items': candidate, 'maxItems': 2},
                    'rationale': _string(1024)})


def _quote_failure(text, quote, kind, offsets):
    """Describe only bounded exact locations, without the source or quote body."""
    return {'kind': kind, 'rendered_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'quote_sha256': hashlib.sha256(quote.encode()).hexdigest(),
            'quote_characters': len(quote), 'quote_utf8_bytes': len(quote.encode()),
            'match_offsets_characters': offsets,
            'match_count_scope': 'at-least-two' if len(offsets) == 2 else 'exact',
            'judgment': 'quote-location failure; meaning unverified'}


def _anchor_context(handle, row, location, index):
    context = {'target_kind': 'catalog-field', 'contract_location': {'anchor_index': index}}
    # Only route-generated handle shapes and fixed role names enter diagnostics.
    if len(handle) <= 64 and re.fullmatch(
            r'current-cue|current-correction-[0-9]+|native-input-[0-9]+|selected-proposal|related-proposal-[0-9]+|caller-proposal', handle):
        context['handle'] = handle
    if row.get('role') in ('current-caller-cue', 'current-caller-correction',
            'qualified-historical-native-input', 'qualified-artifact-proposal',
            'qualified-assistant-proposal', 'caller-artifact-proposal'):
        context['role'] = row['role']
    if location:
        for key, value in location.items():
            if ((key == 'section' and value in ('meaning', 'meaning_dispositions'))
                    or (key == 'field' and value in ('support', 'counterevidence', 'distinguishing_evidence'))
                    or (key in ('candidate_index', 'entry_index') and type(value) is int and 0 <= value < 2)):
                context['contract_location'][key] = value
    return context


def _unique_quote(text, quote):
    _text(quote, 1024)
    start = text.find(quote)
    second = text.find(quote, start + 1) if start >= 0 else -1
    offsets = ([start] if start >= 0 else []) + ([second] if second >= 0 else [])
    if PLACEHOLDER in quote:
        raise MeaningError('meaning-quote-redacted',
            quote_failure=_quote_failure(text, quote, 'redacted', offsets))
    if start < 0 or second >= 0:
        raise MeaningError('meaning-quote-not-exact-unique',
            quote_failure=_quote_failure(text, quote, 'absent' if start < 0 else 'repeated', offsets))
    redacted = text.find(PLACEHOLDER)
    while redacted >= 0:
        if start < redacted + len(PLACEHOLDER) and redacted < start + len(quote):
            raise MeaningError('meaning-quote-redacted',
                quote_failure=_quote_failure(text, quote, 'redacted', offsets))
        redacted = text.find(PLACEHOLDER, redacted + len(PLACEHOLDER))


def validate_anchors(value, catalog, *, primary=False, location=None):
    seen = set()
    for index, anchor in enumerate(_list(value, 4)):
        _keys(anchor, ('handle', 'quote'))
        handle = anchor['handle']
        if not isinstance(handle, str) or handle not in catalog:
            raise MeaningError('meaning-handle-unknown')
        row = catalog[handle]
        try:
            _unique_quote(row['text'], anchor['quote'])
        except MeaningError as error:
            if error.quote_failure is not None:
                # These bindings come from the captured catalogue, not the model.
                error.quote_failure.update(_anchor_context(handle, row, location, index))
            raise
        identity = (handle, anchor['quote'])
        if identity in seen:
            raise MeaningError('meaning-schema-invalid')
        seen.add(identity)
        if primary and not row['primary_support']:
            raise MeaningError('meaning-primary-support-required')
    return value


def validate_bundle(value, catalog=None):
    _keys(value, ('candidates', 'rationale'))
    _text(value['rationale'], 1024)
    ids, meanings = set(), set()
    for index, candidate in enumerate(_list(value['candidates'], 2)):
        _keys(candidate, ('id', 'interpretation', 'meaning', 'artifact_difference', 'support', 'counterevidence'))
        cid = candidate['id']
        if not isinstance(cid, str) or re.fullmatch('[A-Za-z0-9_-]{1,32}', cid) is None:
            raise MeaningError('meaning-schema-invalid')
        validate_scope(candidate['meaning'])
        fingerprint = encoded({'interpretation': candidate['interpretation'],
            'meaning': {field: candidate['meaning'][field] for field in FIELDS},
            'artifact_difference': candidate['artifact_difference']})
        if cid in ids or fingerprint in meanings:
            raise MeaningError('meaning-candidate-duplicated')
        ids.add(cid)
        meanings.add(fingerprint)
        for field in ('interpretation', 'artifact_difference'):
            _text(candidate[field], 1024)
        if not _list(candidate['support'], 4):
            raise MeaningError('meaning-primary-support-required')
        _list(candidate['counterevidence'], 4)
        if catalog is not None:
            validate_anchors(candidate['support'], catalog,
                location={'section': 'meaning', 'candidate_index': index, 'field': 'support'})
            validate_anchors(candidate['counterevidence'], catalog,
                location={'section': 'meaning', 'candidate_index': index, 'field': 'counterevidence'})
            if not any(catalog[item['handle']]['primary_support'] for item in candidate['support']):
                raise MeaningError('meaning-primary-support-required')
        else:
            for anchor in candidate['support'] + candidate['counterevidence']:
                _keys(anchor, ('handle', 'quote'))
                _text(anchor['handle'], 64)
                _text(anchor['quote'], 1024)
    return value


def disposition_schema(bundle, catalog):
    entry = _object({'status': {'type': 'string', 'enum': ['resolved', 'rejected', 'unresolved']},
        'explanation': _string(2048),
        'distinguishing_evidence': {'type': 'array', 'items': anchor_schema(catalog, primary=True), 'maxItems': 4},
        'artifact_consequence': _string(2048),
        'artifact_quotes': {'type': 'array', 'items': _string(1024), 'minItems': 1, 'maxItems': 3}})
    return _object({item['id']: entry for item in bundle['candidates']})


def validate_dispositions(value, bundle, catalog, artifact):
    if not isinstance(value, dict) or set(value) != {item['id'] for item in bundle['candidates']}:
        raise MeaningError('meaning-disposition-keys-invalid')
    for index, entry in enumerate(value.values()):
        _keys(entry, ('status', 'explanation', 'distinguishing_evidence', 'artifact_consequence', 'artifact_quotes'))
        if entry['status'] not in ('resolved', 'rejected', 'unresolved'):
            raise MeaningError('meaning-schema-invalid')
        for field in ('explanation', 'artifact_consequence'):
            _text(entry[field], 2048)
        evidence = validate_anchors(entry['distinguishing_evidence'], catalog, primary=True,
            location={'section': 'meaning_dispositions', 'entry_index': index,
                      'field': 'distinguishing_evidence'})
        if entry['status'] != 'unresolved' and not evidence:
            raise MeaningError('meaning-primary-support-required')
        quotes = _list(entry['artifact_quotes'], 3)
        for quote in quotes:
            _text(quote, 1024)
        if not quotes or len(set(quotes)) != len(quotes):
            raise MeaningError('meaning-schema-invalid')
        for quote_index, quote in enumerate(quotes):
            try:
                _unique_quote(artifact, quote)
            except MeaningError as error:
                if error.quote_failure is not None:
                    error.quote_failure.update(target_kind='generated-artifact',
                        contract_location={'section': 'meaning_dispositions', 'entry_index': index,
                                           'field': 'artifact_quotes', 'quote_index': quote_index})
                raise
    return value


def capture_catalog(prepared, current_artifact=None):
    """Capture exact rendered fields; role comes from the caller's known bindings."""
    catalog, total = {}, 0

    def add(handle, text, role, pointer, primary):
        nonlocal total
        # Source fields retain their actual indexed values, including authenticated
        # empty/whitespace input rows. Evidence quotes remain nonempty separately.
        if not isinstance(text, str):
            raise MeaningError('meaning-schema-invalid')
        if len(text) > TEXT_LIMIT:
            raise MeaningError('meaning-bound-exceeded')
        size = len(text.encode())
        total += size
        if size > TEXT_LIMIT or total > CATALOG_LIMIT or len(catalog) >= HANDLE_LIMIT:
            raise MeaningError('meaning-bound-exceeded')
        if handle in catalog:
            raise MeaningError('meaning-schema-invalid')
        catalog[handle] = {'text': text, 'role': role, 'primary_support': primary,
            'source_pointer': pointer, 'rendered_text_sha256': hashlib.sha256(text.encode()).hexdigest()}

    request = prepared['current_request']
    add('current-cue', request['cue'], 'current-caller-cue', '/current_request/cue', True)
    for index, text in enumerate(request.get('corrections', [])):
        add('current-correction-' + str(index), text, 'current-caller-correction',
            '/current_request/corrections/' + str(index), True)
    native = prepared.get('current_native_sources', {})
    if native.get('status') == 'read':
        for index, row in enumerate(native.get('records', [])):
            add('native-input-' + str(index), row['text'], 'qualified-historical-native-input',
                '/current_native_sources/records/' + str(index) + '/text', True)
    selected = prepared.get('selected_artifact', {})
    if selected.get('status') == 'read':
        add('selected-proposal', selected['text'], 'qualified-artifact-proposal', '/selected_artifact/text', False)
    related = prepared.get('related_artifacts', {})
    if related.get('status') == 'read':
        for index, row in enumerate(related.get('items', [])):
            add('related-proposal-' + str(index), row['text'], 'qualified-assistant-proposal',
                '/related_artifacts/items/' + str(index) + '/text', False)
    if current_artifact is not None:
        add('caller-proposal', current_artifact, 'caller-artifact-proposal', '/current_artifact_proposal', False)
    return catalog


def public_catalog(catalog, *, planner=False):
    return {handle: {**{key: value for key, value in row.items() if key != 'text'},
        'source_pointer': ('/current_context' if planner and handle != 'caller-proposal' else '') + row['source_pointer']}
        for handle, row in catalog.items()}


def catalog_receipt(catalog):
    public = public_catalog(catalog)
    return {'catalog_sha256': hashlib.sha256(encoded(public)).hexdigest(),
            'handle_count': len(catalog), 'roles': sorted({row['role'] for row in catalog.values()}),
            'rendered_text_bytes': sum(len(row['text'].encode()) for row in catalog.values()),
            'hash_scope': 'post-sanitizer text hashes and source-location metadata; not source authorship'}
