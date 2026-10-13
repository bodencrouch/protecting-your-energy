#!/usr/bin/python3
"""Pinned local media derivations, kept separate from original messages."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

sys.dont_write_bytecode = True
BASE = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy'
DEFAULT_CONFIG = BASE / 'config.json'
BLOCKED = ('.sealed', '.sanitized', '.seal-', 'request-only', 'request_only',
           '.request-only', 'quarantine', 'credentials', '.staging')
MARKERS = ('.sealing-in-progress', '.request-only', 'REQUEST_ONLY', '.sealed')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def guard(value, roots=None):
    p = Path(value).expanduser()
    for candidate in (p.absolute(), p.resolve()):
        if any(part.lower().startswith(BLOCKED) for part in candidate.parts):
            raise ValueError('guarded media path')
        if any((ancestor / marker).exists() for ancestor in (candidate, *candidate.parents) for marker in MARKERS):
            raise ValueError('guarded media ancestry')
    p = p.resolve()
    if roots and not any(p.is_relative_to(Path(root).resolve()) for root in roots):
        raise ValueError('media path outside allowlisted roots')
    return p


def checked_bytes(path, expected=None, ceiling=1024*1024, roots=None):
    p = guard(path, roots)
    before = p.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= ceiling:
        raise ValueError('media file exceeds regular-file bound')
    with p.open('rb') as stream:
        raw = stream.read(ceiling + 1)
        after = os.fstat(stream.fileno())
    current = p.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if len(raw) > ceiling or identity(before) != identity(after) or identity(after) != identity(current):
        raise ValueError('media file changed during bounded read')
    if expected and digest(raw) != expected:
        raise ValueError('media file digest differs')
    return raw


def pointer(value, locator):
    if locator in ('', '/'):
        return value
    if not isinstance(locator, str) or not locator.startswith('/'):
        raise ValueError('invalid media JSON pointer')
    for field in locator[1:].split('/'):
        field = field.replace('~1', '/').replace('~0', '~')
        value = value[int(field)] if isinstance(value, list) else value[field]
    return value


def restricted(row):
    return any(row.get(k) for k in ('sealed', 'request_only', 'request-only', 'withheld'))


class Catalog:
    def __init__(self, config, deadline=None):
        self.deadline = deadline or time.monotonic()+20
        self.cfg = json.loads(checked_bytes(config, roots=[BASE]))
        self.lane = self.cfg.get('archive_access', {}).get('derived_media')
        if not isinstance(self.lane, dict):
            raise ValueError('derived media is not configured')
        if digest(checked_bytes(__file__)) != self.lane['script_sha256']:
            raise ValueError('derived reader source differs from configured pin')
        self.catalog = json.loads(checked_bytes(self.lane['catalog'], self.lane['catalog_sha256'], roots=[BASE]))
        self.raw_root = guard(self.cfg['owner_archive'])
        self.sidecar_root = guard(self.catalog['sidecar_root'], [BASE])
        if guard(self.catalog['raw_root']) != self.raw_root:
            raise ValueError('media catalog raw archive differs')
        self.entries = self.catalog['entries']
        if not isinstance(self.entries, list) or not 1 <= len(self.entries) <= 128:
            raise ValueError('media catalog entry count outside bound')
        ids = [e['derivation_id'] for e in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate media derivation identity')
        self.cache, self.read_total = {}, 0

    def read(self, path, sha, ceiling, roots):
        if time.monotonic() >= self.deadline:
            raise TimeoutError('derived media read budget expired')
        key = (str(guard(path, roots)), sha)
        if key not in self.cache:
            raw = checked_bytes(path, sha, ceiling, roots)
            self.read_total += len(raw)
            if self.read_total > 256*1024*1024:
                raise ValueError('derived media total read ceiling exceeded')
            self.cache[key] = raw
        return self.cache[key]

    def verify(self, entry):
        if restricted(entry) or not re.fullmatch(r'[a-f0-9]{64}', entry['derivation_id']):
            raise ValueError('restricted or invalid media entry')
        export = json.loads(self.read(entry['source_export_path'], entry['source_export_sha256'], 8*1024*1024, [self.raw_root]))
        message = pointer(export, entry['source_message_pointer'])
        attachment = pointer(export, entry['source_attachment_pointer'])
        if restricted(message) or restricted(message.get('author') or {}):
            raise ValueError('restricted media source message')
        if str(message.get('id')) != entry['source_message_id'] or str((message.get('author') or {}).get('id')) != entry['source_author_id']:
            raise ValueError('media source message or poster changed')
        if str(attachment.get('id')) != entry['attachment_id'] or attachment not in (message.get('attachments') or []):
            raise ValueError('media source attachment changed')
        if str((export.get('channel') or {}).get('id')) != entry['channel_id']:
            raise ValueError('media source channel changed')
        excluded = self.cfg.get('archive_access', {}).get('excluded_message_ids', [])
        if entry['source_message_id'] in {str(x) for x in excluded}:
            raise ValueError('media message is excluded')
        self.read(entry['captured_asset_path'], entry['captured_asset_sha256'], 32*1024*1024, [self.raw_root])
        artifact = json.loads(self.read(entry['sidecar_path'], entry['sidecar_sha256'], 1024*1024, [self.sidecar_root]))
        annotation = pointer(artifact, entry['sidecar_pointer'])
        if restricted(annotation):
            raise ValueError('restricted media annotation')
        annotation_message = annotation.get('message_id', annotation.get('source_message_id'))
        annotation_author = annotation.get('author_id', annotation.get('source_author_id'))
        annotation_asset = (annotation.get('audio') or {}).get('sha256') or annotation.get('sha256') or annotation.get('asset_sha256')
        annotation_source = annotation.get('source_sha256') or annotation.get('source_export_sha256') or artifact.get('source_sha256')
        annotation_message_pointer = annotation.get('message_pointer') or annotation.get('source_message_pointer')
        annotation_attachment_pointer = annotation.get('attachment_pointer') or annotation.get('source_attachment_pointer')
        if (str(annotation_message), str(annotation_author), annotation_asset, annotation_source,
            annotation_message_pointer, annotation_attachment_pointer) != (entry['source_message_id'],
            entry['source_author_id'],entry['captured_asset_sha256'],entry['source_export_sha256'],
            entry['source_message_pointer'],entry['source_attachment_pointer']):
            raise ValueError('sidecar annotation/source/asset binding differs')
        text = annotation.get('text', '') if entry['kind'] == 'derived_asr' else annotation.get('derived_visual_description', annotation.get('description', ''))
        if not isinstance(text, str) or len(text) > 65536:
            raise ValueError('derived annotation text outside bound')
        return annotation, text, message

    def describe(self, entry):
        return {k: entry[k] for k in ('derivation_id', 'kind', 'poster_role', 'source_message_id',
            'source_author_id', 'channel_id', 'attachment_id', 'source_export_path',
            'source_export_sha256', 'source_message_pointer', 'source_attachment_pointer',
            'captured_asset_path', 'captured_asset_sha256', 'sidecar_path', 'sidecar_sha256',
            'sidecar_pointer', 'derivation_limits')}

    def hydrate(self, entries, max_chars):
        output, remaining = [], max_chars
        for entry in entries[:16]:
            annotation, text, message = self.verify(entry)
            item = self.describe(entry)
            flags = annotation.get('low_probability_words', []) if entry['kind'] == 'derived_asr' else []
            if not isinstance(flags, list):
                raise ValueError('invalid ASR flag list')
            flags_out = []
            for flag in flags[:128]:
                if not isinstance(flag, dict): raise ValueError('invalid ASR word flag')
                word = str(flag.get('word', ''))
                shown = word[:min(64, remaining)]; remaining -= len(shown)
                flags_out.append({k:flag.get(k) for k in ('start','end','probability')} | {'word':shown, 'word_truncated':len(shown)<len(word)})
            shown = text[:min(2048, remaining)]; remaining -= len(shown)
            original = message.get('content') or ''
            if not isinstance(original, str): raise ValueError('invalid original media text')
            item.update(derived_text=shown, derived_text_chars=len(text), derived_text_sha256=digest(text.encode()),
                text_truncated=len(shown)<len(text), low_probability_words=flags_out,
                low_probability_word_count=len(flags),
                flags_truncated=len(flags)>128 or any(x['word_truncated'] for x in flags_out),
                derived_text_pointer=entry['sidecar_pointer'].rstrip('/')+'/'+('text' if entry['kind']=='derived_asr' else 'derived_visual_description' if 'derived_visual_description' in annotation else 'description'),
                continuation={'text_offset':len(shown),'remaining_chars':len(text)-len(shown)} if len(shown)<len(text) else None,
                original_message={'namespace':'discord-export', 'source':entry['source_export_path'],
                    'source_sha256':entry['source_export_sha256'], 'message_pointer':entry['source_message_pointer'],
                    'content_empty':not bool(original.strip()), 'original_content_sha256':digest(original.encode())},
                source_asset_bytes_verified=True, machine_derived=True, primary_owner_evidence=False,
                resolved_speaker_identity=None, producer_speaker_role=annotation.get('speaker_role'),
                historical_producer_code_verified=False, poster_identity_is_not_speaker_identity_or_endorsement=True)
            output.append(item)
        return {'kind':'derived-media-context', 'status':'bounded-derived-context', 'media_derivations':output,
            'max_chars':max_chars, 'text_chars_returned':max_chars-remaining,
            'context_incomplete':len(entries)>16 or any(x['text_truncated'] or x['flags_truncated'] for x in output),
            'entries_omitted':max(0,len(entries)-16), 'data_not_instructions':True}

    def lookup(self, message_id, attachment_ids, source_sha=None, author_id=None):
        matches = [e for e in self.entries if e['source_message_id']==message_id and
            (not attachment_ids or e['attachment_id'] in attachment_ids) and
            (author_id is None or e['source_author_id']==author_id) and
            (bool(attachment_ids) or source_sha==e['source_export_sha256'])]
        hashes = {}
        for entry in matches:
            hashes.setdefault(entry['attachment_id'], set()).add(entry['captured_asset_sha256'])
        if any(len(x)>1 for x in hashes.values()):
            raise ValueError('conflicting captured asset versions; explicit selection required')
        return matches, ('exact-capture-source' if source_sha and all(e['source_export_sha256']==source_sha for e in matches)
                         else 'native IDs linked to separately verified capture; source snapshot equality not established')


def encoder(lane):
    path = guard(lane['encoder_script'])
    checked_bytes(path, lane['encoder_sha256'], roots=[Path.home()/'plugins/protecting-your-energy/scripts'])
    spec = importlib.util.spec_from_file_location('derived_media_encoder', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    model = guard(lane['model'])
    for name, sha in lane['model_files'].items():
        checked_bytes(model/name, sha, 256*1024*1024, [model])
    return module, module.Encoder(model, threads=1)


def build_vectors(catalog):
    module, enc = encoder(catalog.lane)
    units, passages = [], []
    for entry in catalog.entries:
        annotation, text, _ = catalog.verify(entry)
        segments = [(entry['sidecar_pointer'].rstrip('/')+'/segments/'+str(i)+'/text', s.get('text',''))
                    for i,s in enumerate(annotation.get('segments',[]))] if entry['kind']=='derived_asr' else [
                    (entry['sidecar_pointer'].rstrip('/')+'/'+('derived_visual_description' if 'derived_visual_description' in annotation else 'description'),text)]
        for locator, passage in segments:
            if not isinstance(passage,str) or module.SENSITIVE.search(passage):
                raise ValueError('invalid or sensitive derived passage')
            for split in enc.split(passage):
                value=split['text'];identity=[entry['derivation_id'],locator,split['char_start'],split['char_end'],digest(value.encode())]
                units.append({'derived_unit_id':digest(json.dumps(identity,separators=(',',':')).encode()),
                    'derivation_id':entry['derivation_id'],'segment_pointer':locator,
                    'char_start':split['char_start'],'char_end':split['char_end'],'text_sha256':digest(value.encode()),
                    'encoded_tokens':split['encoded_tokens']})
                passages.append(value)
    if not 1<=len(units)<=512:raise ValueError('derived vector unit count outside bound')
    values=enc.encode(passages).tolist()
    for unit, vector in zip(units,values):unit['vector']=vector
    return {'schema_version':1,'catalog_sha256':catalog.lane['catalog_sha256'],
        'encoder_sha256':catalog.lane['encoder_sha256'],'reader_sha256':catalog.lane['script_sha256'],
        'model':{k:enc.meta[k] for k in ('sha256','tokenizer_sha256','dim','query_prefix','pooling','token_limit')},
        'units':units,'machine_derived':True,'primary_owner_evidence':False}


def query(catalog, queries, limit, year=None, platform=None):
    saved = json.loads(checked_bytes(catalog.lane['vectors'], catalog.lane['vectors_sha256'], 8*1024*1024, roots=[BASE]))
    if saved['catalog_sha256'] != catalog.lane['catalog_sha256'] or saved['encoder_sha256'] != catalog.lane['encoder_sha256'] or saved['reader_sha256'] != catalog.lane['script_sha256']:
        raise ValueError('derived vector cache provenance differs')
    module, enc = encoder(catalog.lane)
    if saved['model'] != {k:enc.meta[k] for k in ('sha256','tokenizer_sha256','dim','query_prefix','pooling','token_limit')}:
        raise ValueError('derived vector cache model differs')
    if any(not isinstance(q,str) or len(q)>65536 for q in queries):raise ValueError('derived query text outside bound')
    qs=list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:3]
    if not qs or len(saved['units'])>512:raise ValueError('derived query/unit bound invalid')
    pieces=[p['text'] for q in qs for p in enc.split(q)]
    vectors=enc.encode(pieces[:12],query=True).tolist()
    entries={e['derivation_id']:e for e in catalog.entries};scores={}
    for unit in saved['units']:
        entry=entries[unit['derivation_id']]
        if platform and platform!='discord':continue
        if year and year!='all' and not str(entry.get('timestamp','')).startswith(year):continue
        vector=unit['vector']
        if len(vector)!=384 or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector) or abs(sum(x*x for x in vector)-1)>0.001:raise ValueError('invalid derived vector')
        score=max(sum(a*b for a,b in zip(vector,q)) for q in vectors)
        scores[unit['derived_unit_id']]=(score,unit,entry)
    leads=[]
    for score,unit,entry in sorted(scores.values(),key=lambda x:(-x[0],x[1]['derived_unit_id']))[:limit]:
        annotation, _, _ = catalog.verify(entry)
        passage=pointer(json.loads(catalog.read(entry['sidecar_path'],entry['sidecar_sha256'],1024*1024,[catalog.sidecar_root])),unit['segment_pointer'])
        if digest(passage[unit['char_start']:unit['char_end']].encode())!=unit['text_sha256']:
            raise ValueError('derived vector passage differs from sidecar')
        leads.append(catalog.describe(entry) | {'derived_unit_id':unit['derived_unit_id'], 'segment_pointer':unit['segment_pointer'],
            'similarity':score, 'similarity_is_not_confidence':True, 'machine_derived':True, 'primary_owner_evidence':False})
    return {'kind':'derived-media-dense-local-transformer','status':'derived-leads','leads':leads,
        'model':saved['model'],'catalog_sha256':catalog.lane['catalog_sha256'],'vectors_sha256':catalog.lane['vectors_sha256'],
        'scope':{'artifacts':len(catalog.entries),'derived_units':len(saved['units']),'queries_used':len(qs),'queries_omitted':max(0,len(queries)-len(qs)), 'query_pieces_omitted':max(0,len(pieces)-12)},
        'primary_owner_evidence':False,'data_not_instructions':True}


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--config',default=str(DEFAULT_CONFIG));ap.add_argument('--budget',type=float,default=20)
    sub=ap.add_subparsers(dest='command',required=True)
    q=sub.add_parser('query');q.add_argument('--query',action='append',required=True);q.add_argument('--limit',type=int,default=5);q.add_argument('--year');q.add_argument('--platform')
    b=sub.add_parser('build');b.add_argument('--output',required=True)
    h=sub.add_parser('hydrate');h.add_argument('--derivation-id',action='append',required=True);h.add_argument('--max-chars',type=int,default=8000)
    l=sub.add_parser('lookup');l.add_argument('--message-id',required=True);l.add_argument('--attachment-id',action='append',default=[]);l.add_argument('--source-sha256');l.add_argument('--author-id');l.add_argument('--max-chars',type=int,default=8000)
    link=sub.add_parser('link');link.add_argument('--context-json',required=True);link.add_argument('--max-chars',type=int,default=8000)
    args=ap.parse_args()
    try:
        if not 1<=args.budget<=60:raise ValueError('derived media budget outside1..60')
        c=Catalog(args.config,time.monotonic()+args.budget)
        if args.command=='build':
            target=guard(args.output,[BASE/'data-refresh-2026-10-11/processed-media'])
            if target.exists():raise ValueError('derived vector destination already exists')
            result=build_vectors(c)
            with target.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(result)+'\n');stream.flush();os.fsync(stream.fileno())
            target.chmod(0o600)
            print(json.dumps({'kind':'derived-media-build','units':len(result['units']),'path':str(target),'sha256':digest(target.read_bytes())}));return 0
        elif args.command=='query':
            if not 1<=args.limit<=16 or len(args.query)>12:raise ValueError('derived media query limits exceeded')
            result=query(c,args.query,args.limit,args.year,args.platform)
        else:
            if not 0<=args.max_chars<=20000:raise ValueError('derived text budget outside0..20000')
            if args.command=='link':
                if len(args.context_json.encode())>65536:raise ValueError('media linkage metadata exceeds64KiB')
                context=json.loads(args.context_json)
                if len(context['rows'])>128:raise ValueError('media context row count exceeds128')
                matched,associations,missing={},[],[]
                for row in context['rows']:
                    found,relation=c.lookup(str(row['message_id']),row.get('attachment_ids',[]),context.get('source_sha256'),row.get('author_id'))
                    found_assets={entry['attachment_id'] for entry in found}
                    for identifier in row.get('attachment_ids',[]):
                        if identifier not in found_assets:missing.append({'message_id':row['message_id'],'attachment_id':identifier,'reason':'no matching pinned derivation'})
                    for entry in found:
                        matched[entry['derivation_id']]=entry
                        associations.append({'derivation_id':entry['derivation_id'],'context_namespace':context['namespace'],
                            'context_source_pointer':row.get('source_pointer'),'context_source_sha256':context.get('source_sha256'),
                            'association':relation})
                result=c.hydrate(list(matched.values()),args.max_chars);result['context_associations']=associations
                result['missing_derivations']=missing
                result['context_incomplete']=bool(missing) or result['context_incomplete']
                encoded=json.dumps(result,ensure_ascii=True)
                if len(encoded.encode())>256*1024:raise ValueError('derived media transport ceiling exceeded')
                print(encoded);return 0
            elif args.command=='lookup':entries,association=c.lookup(args.message_id,args.attachment_id,args.source_sha256,args.author_id)
            else:
                if len(args.derivation_id)>16:raise ValueError('derived hydrate entry limit exceeded')
                wanted=set(args.derivation_id);entries=[e for e in c.entries if e['derivation_id'] in wanted]
                if len(entries)!=len(wanted):raise ValueError('derived entry unavailable')
                association='explicit-pinned-sidecar'
            result=c.hydrate(entries,args.max_chars);result['association']=association
            result['missing_derivations']=[] if entries else [{'reason':'no matching pinned derivation','message_id':getattr(args,'message_id',None)}]
            result['context_incomplete']=bool(result['missing_derivations']) or result['context_incomplete']
        encoded=json.dumps(result,ensure_ascii=True)
        if len(encoded.encode())>256*1024:raise ValueError('derived media transport ceiling exceeded')
        print(encoded);return 0
    except (OSError,ValueError,KeyError,TypeError,IndexError,TimeoutError,ImportError) as error:
        print(json.dumps({'kind':'derived-media-unavailable','status':'integrity-or-runtime-gap','error':str(error)[:300]}));return 1


if __name__=='__main__':raise SystemExit(main())
