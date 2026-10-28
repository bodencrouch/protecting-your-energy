#!/usr/bin/env python3
"""Local transformer discovery over explicit original-source generations."""
from __future__ import annotations
import argparse, collections, datetime, hashlib, importlib.util, json, os, re, sys, time, uuid
from pathlib import Path
sys.dont_write_bytecode = True
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
DEFAULT = Path.home() / 'brain-data/upar-dip/derived/protect-your-energy/2026-10-11/deeper-pass/semantic'
OWNER = '227896831944687616'
MODEL = 'BAAI/bge-small-en-v1.5'
PREFIX = 'Represent this sentence for searching relevant passages: '
BLOCKED = ('.sealed', '.sanitized', '.seal-', 'request-only', 'request_only', 'quarantine', 'credentials', '.staging')
MARKERS = ('.sealing-in-progress', '.request-only', 'REQUEST_ONLY', '.sealed')
SENSITIVE = re.compile(r'(?i)(?:authorization\s*[:=]\s*[\"\']?(?:bearer|basic)|(?:api[_ -]?key|access[_ -]?token|password|secret[_ -]?key)\s*[:=]\s*[\"\']?[A-Za-z0-9_./+\-=]{12,}|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}))')
SCAFFOLD = re.compile(r'MEMORY_SUMMARY BEGINS|<oai-mem-citation>|## Learned User Preferences|<multi_agent_role>')
FAMILIES = {'discord-original-OpenKotOR', 'discord-original-DM', 'discord-original-snapshot'}


def digest(data): return hashlib.sha256(data).hexdigest()
def dump(p, obj):
    tmp=p.with_name(p.name+'.'+uuid.uuid4().hex+'.tmp');tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2));tmp.chmod(0o600);os.replace(tmp,p)
def private_dir(p): p.mkdir(parents=True, exist_ok=True, mode=0o700); p.chmod(0o700)
def guard(p):
    for q in (p.absolute(), p.resolve()):
        if any(part.lower().startswith(BLOCKED) for part in q.parts): raise ValueError('guarded path')
        if any((a/m).exists() for a in (q,*q.parents) for m in MARKERS): raise ValueError('guard marker present')
    return p.resolve()
def original(p, archive):
    p=guard(Path(p).expanduser()); root=guard(Path(archive).expanduser())/'archive/originals'
    if not p.is_relative_to(root): raise ValueError('source is outside archive originals')
    return p

_POLICY=None

def access_policy():
    global _POLICY
    if _POLICY is None:
        path=guard(Path.home()/'brain-data/upar-dip/tooling/retrieval/common.py')
        spec=importlib.util.spec_from_file_location('pye_archive_policy',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        ids=set(mod.EXCLUDED_IDS); message_ids=set(); config=Path.home()/'brain-data/upar-dip/derived/protect-your-energy/config.json'
        if config.exists():
            access=json.loads(guard(config).read_text()).get('archive_access',{});ids.update(access.get('excluded_author_ids',[]));message_ids.update(access.get('excluded_message_ids',[]))
        _POLICY=(ids,mod.EXCLUDED_NAMES,message_ids)
    return _POLICY

def restricted(row):
    if any(row.get(k) for k in ('sealed','request_only','request-only','withheld')):return True
    return any(v in str(row.get(k,'')).lower() for k in ('access','access_scope','visibility','sensitivity','labels') for v in ('request-only','request_only','sealed','withheld'))

def excluded(m):
    ids,names,message_ids=access_policy();author=m.get('author',{})
    if restricted(m) or restricted(author):return 'restricted-or-withheld-row'
    if str(author.get('id')) in ids or author.get('name') in names or author.get('nickname') in names or str(m.get('id')) in message_ids:return 'installed-access-exclusion'
    text=m.get('content','')
    if SENSITIVE.search(text):return 'credential-bearing-message'
    if SCAFFOLD.search(text):return 'memory-or-profile-scaffolding'
    return None

def version(m):
    # Text versions exclude export presentation changes (reactions/avatar/embed fetches).
    def instant(value):
        if not value:return None
        return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(datetime.timezone.utc).isoformat()
    ref=m.get('reference') or {}
    fields={'id':m.get('id'),'timestamp':instant(m.get('timestamp')),'timestampEdited':instant(m.get('timestampEdited')),'content':m.get('content'),'author_id':m.get('author',{}).get('id'),'reply_message_id':ref.get('messageId'),'reply_channel_id':ref.get('channelId')}
    return digest(json.dumps(fields, ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())



class Encoder:
    def __init__(self, directory, threads=2):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np; self.path=guard(Path(directory)); self.tokenizer=Tokenizer.from_file(str(self.path/'tokenizer.json'))
        self.tokenizer.no_truncation(); self.tokenizer.no_padding()
        cfg=json.loads((self.path/'config.json').read_text()); self.limit=int(cfg['max_position_embeddings'])
        if self.limit!=512 or cfg['hidden_size']!=384: raise ValueError('unsupported model config')
        opts=ort.SessionOptions();opts.intra_op_num_threads=threads;opts.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(self.path/'model_optimized.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
        self.model_hash=digest((self.path/'model_optimized.onnx').read_bytes())
        self.tokenizer_hash=digest((self.path/'tokenizer.json').read_bytes())
        self.meta={'name':MODEL,'sha256':self.model_hash,'tokenizer_sha256':self.tokenizer_hash,'dim':384,'token_limit':self.limit,'pooling':'CLS + L2 normalization','query_prefix':PREFIX,'runtime':'onnxruntime '+ort.__version__,'providers':self.session.get_providers(),'threads':threads}
    def split(self, text):
        encoding=self.tokenizer.encode(text,add_special_tokens=False)
        token_count=len(encoding.ids)
        if not token_count: return []
        offsets=encoding.offsets
        span=480; overlap=48; out=[];start=0
        while start<token_count:
            end=min(start+span,token_count); a=0 if start==0 else offsets[start][0]; b=len(text) if end==token_count else offsets[end-1][1]
            part=text[a:b]; n=len(self.tokenizer.encode(part).ids)
            while n>self.limit:
                end-=1;b=offsets[end-1][1];part=text[a:b];n=len(self.tokenizer.encode(part).ids)
            out.append({'text':part,'char_start':a,'char_end':b,'token_start':start,'token_end':end,'encoded_tokens':n})
            if end==token_count:break
            start=max(start+1,end-overlap)
        return out
    def _encode_flat(self, texts, query=False):
        xs=[PREFIX+x if query else x for x in texts]
        self.tokenizer.enable_padding(pad_id=0,pad_token='[PAD]')
        es=self.tokenizer.encode_batch(xs);self.tokenizer.no_padding()
        if any(len(e.ids)>self.limit for e in es): raise ValueError('input exceeds actual tokenizer limit; split query or passage')
        feed={'input_ids':self.np.array([e.ids for e in es],dtype='int64'),'attention_mask':self.np.array([e.attention_mask for e in es],dtype='int64'),'token_type_ids':self.np.array([e.type_ids for e in es],dtype='int64')}
        raw=self.session.run(None,feed)[0][:,0,:].astype('float32'); norms=self.np.linalg.norm(raw,axis=1,keepdims=True)
        return raw/self.np.maximum(norms,1e-12)

    def encode(self, texts, query=False):
        if query or len(texts)<12:return self._encode_flat(texts,query)
        lengths=[len(self.tokenizer.encode(t).ids) for t in texts]
        order=sorted(range(len(texts)),key=lambda i:lengths[i]);groups=[order[i:i+8] for i in range(0,len(order),8)]
        plain=len(texts)*max(lengths);bucket=sum(len(g)*max(lengths[i] for i in g) for g in groups)
        if plain<bucket*1.25:return self._encode_flat(texts,query)
        result=self.np.empty((len(texts),384),dtype='float32')
        for group in groups:result[group]=self._encode_flat([texts[i] for i in group])
        return result


def collect(args, enc):
    cp=guard(Path(args.catalog)); catalog=json.loads(cp.read_text()); sources=[]; records={}; skips=[]; exclusions=collections.Counter(); processed=0
    for entry in catalog['sources']:
        if entry.get('family') not in FAMILIES: continue
        if args.family and entry.get('family') not in args.family:continue
        if any(entry.get(k) for k in ('sealed','request_only','withheld')) or any(v in str(entry).lower() for v in ('request-only','request_only','withheld')):continue
        try:
            p=original(entry['source'],args.archive)
            if p.stat().st_size>args.max_source_mb*1024*1024: raise ValueError('source byte bound exceeded')
            raw=p.read_bytes();obj=json.loads(raw);msgs=obj.get('messages',[]);g=str(obj.get('guild',{}).get('id',''));ch=str(obj.get('channel',{}).get('id',''));label=obj.get('channel',{}).get('name','')
            if args.guild and g not in args.guild:continue
            if args.channel and ch not in args.channel:continue
            anchors=[i for i,m in enumerate(msgs) if str(m.get('timestamp','')).startswith(str(args.year)) and m.get('author',{}).get('id')==args.owner]
            selected=set(anchors) # Dense documents anchor owner text; all peer context remains directly hydratable.
            source={'path':str(p),'sha256':digest(raw),'bytes':len(raw),'mtime_ns':p.stat().st_mtime_ns,'family':entry['family'],'guild_id':g,'channel_id':ch,'channel':label,'export_messages':len(msgs),'owner_anchors':len(anchors),'selected_messages':len(selected),'date_min':min((m.get('timestamp','') for m in msgs),default=None),'date_max':max((m.get('timestamp','') for m in msgs),default=None)}
            sources.append(source);processed+=1
            for i in sorted(selected):
                m=msgs[i];reason=excluded(m)
                if reason:exclusions[reason]+=1;continue
                text=m.get('content','')
                if not text.strip():exclusions['empty-text']+=1;continue
                vh=version(m);key=(g,ch,m['id'],vh);locator={'source':str(p),'source_hash':source['sha256'],'json_pointer':'/messages/'+str(i),'original_index':i}
                if key in records:records[key]['exports'].append(locator);continue
                records[key]={'message_id':m['id'],'version_hash':vh,'source':str(p),'source_hash':source['sha256'],'json_pointer':'/messages/'+str(i),'original_index':i,'exports':[locator],'observed_export_metadata':{'attachment_count':len(m.get('attachments',[])),'embed_count':len(m.get('embeds',[])),'timestamp_edited':m.get('timestampEdited')},'timestamp':m['timestamp'],'timestamp_edited':m.get('timestampEdited'),'speaker_id':m.get('author',{}).get('id'),'speaker':m.get('author',{}).get('name'),'role':'self' if m.get('author',{}).get('id')==args.owner else 'other','guild_id':g,'channel_id':ch,'channel':label,'family':entry['family'],'reference':m.get('reference'),'neighbor_ids':[v.get('id') for v in msgs[max(0,i-args.context):i+args.context+1] if v.get('id')!=m['id']],'evidence_class':'retrieval-lead-only; copied prose and belief adoption unverified','text':text}
            if len(records)>args.max_messages:raise ValueError('message capacity exceeded; narrow explicit source scope')
        except Exception as e:
            if 'capacity exceeded' in str(e):raise
            skips.append({'source':entry.get('source'),'reason':str(e)})
    # Pack short owner messages in the same channel/time episode. This is mechanical
    # chronological packing, not a topic/personality taxonomy. Every parent survives.
    groups=[];group=[];tokens=0;last=None
    ordered=sorted(records.values(),key=lambda r:(r['guild_id'],r['channel_id'],datetime.datetime.fromisoformat(r['timestamp']),int(r['message_id']),r['version_hash']))
    for r in ordered:
        moment=datetime.datetime.fromisoformat(r['timestamp']); n=len(enc.tokenizer.encode(r['text'],add_special_tokens=False).ids)
        same=last is not None and (r['guild_id'],r['channel_id'])==(last['guild_id'],last['channel_id']) and 0<=(moment-datetime.datetime.fromisoformat(last['timestamp'])).total_seconds()<=900
        if group and (not same or tokens+n>400 or any(v['message_id']==r['message_id'] for v in group)):groups.append(group);group=[];tokens=0
        group.append(r);tokens+=n;last=r
    if group:groups.append(group)
    chunks=[]
    for group in groups:
        text='';spans=[]
        for r in group:
            if text:text+='\n'
            a=len(text);text+=r['text'];spans.append((a,len(text),r))
        for j,part in enumerate(enc.split(text)):
            parents=[]
            for a,b,r in spans:
                left=max(a,part['char_start']);right=min(b,part['char_end'])
                if right<=left:continue
                parent={k:v for k,v in r.items() if k not in ('text','neighbor_ids')};parent.update(parent_char_start=left-a,parent_char_end=right-a,parent_characters=len(r['text']));parents.append(parent)
            if not parents:continue
            primary=parents[0];chunk={k:v for k,v in primary.items() if k not in ('parent_char_start','parent_char_end')};chunk.update(part);chunk['chunk_number']=j;chunk['parent_refs']=parents;chunk['parent_characters']=sum(p['parent_characters'] for p in parents);chunk['neighbor_ids']=next(r['neighbor_ids'] for a,b,r in spans if r['message_id']==primary['message_id']);chunk['unit_id']=digest(json.dumps([(p['message_id'],p['version_hash'],p['parent_char_start'],p['parent_char_end']) for p in parents]).encode());chunks.append(chunk)
            if len(chunks)>args.max_chunks:raise ValueError('chunk capacity exceeded; increase bounded explicit capacity or narrow scope')
    scope={'catalog':str(cp),'catalog_hash':digest(cp.read_bytes()),'year':args.year,'owner_id':args.owner,'families':args.family or sorted(FAMILIES),'guild_filter':args.guild,'channel_filter':args.channel,'context_neighbors':args.context,'dense_unit':'Chronological same-channel owner-only windows, packed <=400 content tokens or split at actual model limit; all parent refs retained; peers hydrated directly','processed_source_files':processed,'source_files':sources,'skipped_sources':skips,'excluded_occurrences':dict(exclusions),'unique_message_versions':len(records),'chunks':len(chunks),'scope_note':'Only catalog-listed selected original Discord exports and year/owner anchors with surrounding neighbors; not whole archive or complete peer/attachment reading.'}
    counts={}
    for r in records.values():
        key=(r['guild_id'],r['channel_id']); v=counts.setdefault(key,{'guild_id':key[0],'channel_id':key[1],'label':r['channel'],'unique_versions':0,'owner_versions':0,'indexed_date_min':r['timestamp'],'indexed_date_max':r['timestamp']})
        v['unique_versions']+=1;v['owner_versions']+=int(r['role']=='self');v['indexed_date_min']=min(v['indexed_date_min'],r['timestamp']);v['indexed_date_max']=max(v['indexed_date_max'],r['timestamp'])
    scope['channel_counts']=list(counts.values())
    return chunks,scope


def compact_scope(scope):
    fields={k:v for k,v in scope.items() if k not in ('source_files','skipped_sources','channel_counts')}
    fields['source_manifest_note']='Complete source paths, hashes and exclusions are retained in the generation manifest.'
    fields['source_files_count']=len(scope['source_files'])
    fields['skipped_sources_count']=len(scope['skipped_sources'])
    fields['channel_counts']=scope.get('channel_counts',[])
    return fields


def generations(index, archive):
    index=guard(Path(index)); archive=guard(Path(archive).expanduser())
    if index.is_relative_to(archive): raise ValueError('index must be outside the read-only archive')
    private_dir(index);private_dir(index/'generations');return index

def active(index,generation=None):
    index=guard(Path(index));name=generation if generation else json.loads((index/'CURRENT.json').read_text())['generation'];p=guard(index/'generations'/name)
    if p.parent!=index/'generations':raise ValueError('invalid generation pointer')
    meta=json.loads((p/'manifest.json').read_text())
    if meta.get('state')!='complete':raise ValueError('generation incomplete')
    return p,meta

def source_fresh(row, archive, cache):
    alternatives=[row]+row.get('exports',[]);errors=[]
    for locator in alternatives:
        try:
            p=original(locator.get('source',locator.get('path',row['source'])),archive)
            if str(p) not in cache:
                if p.stat().st_size>64*1024*1024:raise ValueError('fresh source exceeds byte bound')
                raw=p.read_bytes();cache[str(p)]=(digest(raw),json.loads(raw))
            sha,obj=cache[str(p)]
            if sha!=locator.get('source_hash',row['source_hash']):raise ValueError('source changed since generation')
            msgs=obj.get('messages',[]);i=locator.get('original_index',row['original_index'])
            if i>=len(msgs) or msgs[i].get('id')!=row['message_id']:raise ValueError('source pointer mismatch')
            m=msgs[i]
            if version(m)!=row['version_hash']:raise ValueError('message version mismatch')
            if str(m.get('author',{}).get('id'))!=str(row.get('speaker_id')):raise ValueError('source author metadata differs')
            if str(obj.get('channel',{}).get('id'))!=str(row.get('channel_id')) or str(obj.get('guild',{}).get('id'))!=str(row.get('guild_id')):raise ValueError('source channel/guild metadata differs')
            if excluded(m):raise PermissionError('message now excluded by current access policy')
            row.update(source=str(p),source_hash=sha,json_pointer='/messages/'+str(i),original_index=i,timestamp=m['timestamp'],speaker=m.get('author',{}).get('name'),channel=obj.get('channel',{}).get('name'))
            return m,msgs
        except PermissionError:raise
        except Exception as e:errors.append(str(e))
    raise ValueError('No permitted unchanged export matches this ID/version: '+'; '.join(dict.fromkeys(errors)))


def build(args):
    started=time.monotonic();index=generations(args.index,args.archive);enc=Encoder(args.model,args.threads)
    import fcntl
    lock=open(index/'BUILD.lock','a+');os.chmod(index/'BUILD.lock',0o600);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.resume:
        gen=guard(index/'generations'/args.resume)
        if gen.parent!=index/'generations':raise ValueError('resume outside generation directory')
        meta=json.loads((gen/'manifest.json').read_text())
        if meta['state']=='complete':raise ValueError('completed generations are immutable')
        if {k:v for k,v in meta['model'].items() if k!='threads'}!={k:v for k,v in enc.meta.items() if k!='threads'}:raise ValueError('resume model/runtime differs')
        meta.setdefault('runtime_history',[]).append({'resumed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'from_threads':meta['model']['threads'],'to_threads':args.threads});meta['model']=enc.meta
        for s in meta['scope']['source_files']:
            p=original(s['path'],args.archive)
            stat=p.stat()
            if (stat.st_size,stat.st_mtime_ns)!=(s['bytes'],s['mtime_ns']) and digest(p.read_bytes())!=s['sha256']:raise ValueError('resume source changed')
        chunks=[json.loads(x) for x in (gen/'chunks.jsonl').read_text().splitlines()]
    else:
        chunks,scope=collect(args,enc);gen=index/'generations'/('gen-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]);private_dir(gen)
        (gen/'chunks.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in chunks));(gen/'chunks.jsonl').chmod(0o600)
        meta={'state':'building','generation':gen.name,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'model':enc.meta,'scope':scope,'embedded_chunks':0,'batches':[],'batch_size':args.batch_size};dump(gen/'manifest.json',meta)
    cursor=meta['embedded_chunks'];batch_size=meta['batch_size']
    while cursor<len(chunks):
        if time.monotonic()-started>=args.budget_seconds:break
        end=min(cursor+batch_size,len(chunks))
        for r in chunks[cursor:end]:
            for parent in r.get('parent_refs',[r]):
                if excluded({'id':parent['message_id'],'author':{'id':parent['speaker_id'],'name':parent.get('speaker')},'content':''}):raise ValueError('Current access policy excludes a planned parent; build a newly filtered generation')
        vectors=enc.encode([x['text'] for x in chunks[cursor:end]])
        meta['batching']='Original row order; adaptive length-sorted microbatches <=8 when padded-token reduction exceeds20%, otherwise original batch'
        filename=f'vectors-{cursor:08d}.npy';dest=gen/filename
        with open(dest,'wb') as out:enc.np.save(out,vectors,allow_pickle=False)
        dest.chmod(0o600);meta['batches'].append({'file':filename,'start':cursor,'end':end,'threads':args.threads,'batching':meta['batching'],'sha256':digest(dest.read_bytes())});cursor=end;meta['embedded_chunks']=cursor;dump(gen/'manifest.json',meta)
    if cursor==len(chunks):
        meta['state']='complete';meta['completed_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();meta['build_seconds']=round(time.monotonic()-started,3);dump(gen/'manifest.json',meta)
        ptr=index/('CURRENT.'+uuid.uuid4().hex+'.tmp');dump(ptr,{'generation':gen.name});os.replace(ptr,index/'CURRENT.json')
    return {'kind':'dense-local-transformer','state':meta['state'],'generation':gen.name,'embedded_chunks':cursor,'total_chunks':len(chunks),'seconds':round(time.monotonic()-started,3),'model':enc.meta,'scope':compact_scope(meta['scope']),'resume_command':None if meta['state']=='complete' else f'build --resume {gen.name}'}

def query(args):
    started=time.monotonic();gen,meta=active(args.index)
    if args.year is not None and args.year!=meta['scope']['year'] or args.role=='other':return {'kind':'dense-local-transformer','state':'scope-unavailable','generation':gen.name,'scope':compact_scope(meta['scope']),'requested_year':args.year,'requested_role':args.role,'queries':[{'query':q,'leads':[],'failure':'Requested year/role is outside this owner-anchored dense generation; no absence claim.'} for q in args.query],'failures':[],'seconds':round(time.monotonic()-started,3)}
    enc=Encoder(args.model,args.threads)
    if meta['model']['sha256']!=enc.model_hash or meta['model']['tokenizer_sha256']!=enc.tokenizer_hash:raise ValueError('query model differs from generation')
    chunks=[json.loads(x) for x in (gen/'chunks.jsonl').read_text().splitlines()];np=enc.np;arrays=[]
    for b in meta['batches']:
        p=gen/b['file']
        if digest(p.read_bytes())!=b['sha256']:raise ValueError('vector checkpoint hash mismatch')
        arrays.append(np.load(p,allow_pickle=False))
    matrix=np.vstack(arrays) if arrays else np.zeros((0,384),dtype='float32')
    if matrix.shape!=(len(chunks),384):raise ValueError('vector/locator count mismatch')
    mask=np.array([(args.role=='all' or x['role']==args.role) and (args.year is None or x['timestamp'].startswith(str(args.year))) and (not args.channel or x['channel_id'] in args.channel) and (not args.guild or x['guild_id'] in args.guild) for x in chunks]);eligible=np.where(mask)[0];cache={};failures=[];outputs=[]
    for text in args.query:
        t=time.monotonic();parts=enc.split(text)
        # Query windows also retain their full meaning; averaging independently encoded windows is explicit.
        if len(parts)==1 and len(enc.tokenizer.encode(PREFIX+text).ids)<=enc.limit:qv=enc.encode([text],query=True)[0]
        else:
            parts=enc.split(text);qvs=enc.encode([x['text'] for x in parts],query=True);qv=qvs.mean(axis=0);qv/=max(float(np.linalg.norm(qv)),1e-12)
        scores=matrix[eligible]@qv;order=eligible[np.argsort(-scores)];leads=[];seen=set();selected_parent_ids=set()
        for i in order:
            r=chunks[int(i)];key=r['unit_id']
            if key in seen:continue
            seen.add(key)
            parent_ids={p['message_id'] for p in r.get('parent_refs',[r])}
            if not args.include_versions and parent_ids and parent_ids.issubset(selected_parent_ids):continue
            try:
                m,_=source_fresh(r,args.archive,cache)
                pieces=[]
                for parent in r.get('parent_refs',[]):
                    pm,_=source_fresh(parent,args.archive,cache);pieces.append(pm.get('content','')[parent['parent_char_start']:parent['parent_char_end']])
                if '\n'.join(pieces)!=r['text']:raise ValueError('cached unit text differs from fresh original parent spans')
                hit={k:v for k,v in r.items() if k not in ('text','exports','neighbor_ids','encoded_tokens','parent_characters','parent_refs','observed_export_metadata')};hit.update(score=float(matrix[i]@qv),source_state='guarded-and-hash-verified-original',preview=r['text'][:args.preview_chars],preview_truncated=len(r['text'])>args.preview_chars,chunk_span={'characters':[r['char_start'],r['char_end']],'tokens':[r['token_start'],r['token_end']]},parent_characters=r['parent_characters'],neighbor_ids=r['neighbor_ids'],version_exports=len(r['exports']),parent_message_ids=[p['message_id'] for p in r['parent_refs']],parent_locator_count=len(r['parent_refs']),parent_locators=[{k:p[k] for k in ('message_id','version_hash','source','json_pointer','timestamp','speaker_id','parent_char_start','parent_char_end')} for p in r['parent_refs'][:4]],parent_locators_truncated=len(r['parent_refs'])>4);hit['version_interpretation']='Export/text variants are preserved in the index; no belief update inferred.';leads.append(hit);selected_parent_ids.update(parent_ids)
            except Exception as e:
                failures.append({'message_id':r['message_id'],'source':r['source'],'reason':str(e)})
            if len(leads)>=args.limit:break
            if len(failures)>=100:break
        outputs.append({'query':text,'seconds':round(time.monotonic()-t,3),'query_windows':len(parts),'leads':leads})
    return {'kind':'dense-local-transformer','generation':gen.name,'model':enc.meta,'scope':compact_scope(meta['scope']),'queries':outputs,'failures':failures,'seconds':round(time.monotonic()-started,3),'interpretation':'Similarity is a discovery lead, not a factual answer, adoption judgment, exhaustive search or completed contextual reading.'}

def hydrate(args):
    gen,meta=active(args.index);matches=[];seen=set();cache={}
    for line in (gen/'chunks.jsonl').open():
        r=json.loads(line)
        parents=r.get('parent_refs',[r]);r=next((p for p in parents if p['message_id']==args.message_id and (not args.version or p['version_hash']==args.version)),None)
        if r is None:continue
        if r['version_hash'] in seen:continue
        seen.add(r['version_hash']);m,msgs=source_fresh(r,args.archive,cache);i=r['original_index'];rows=[]
        for j in range(max(0,i-args.neighbors),min(len(msgs),i+args.neighbors+1)):
            n=msgs[j];rows.append({'json_pointer':'/messages/'+str(j),'message_id':n['id'],'timestamp':n.get('timestamp'),'speaker_id':n.get('author',{}).get('id'),'speaker':n.get('author',{}).get('name'),'reference':n.get('reference'),'content':'[excluded from display]' if excluded(n) else n.get('content',''),'exclusion':excluded(n),'attachment_count':len(n.get('attachments',[]))})
        matches.append({'message_id':r['message_id'],'version_hash':r['version_hash'],'source':r['source'],'source_hash':r['source_hash'],'json_pointer':r['json_pointer'],'source_state':'guarded-and-hash-verified-original','full_original_characters':len(m.get('content','')),'context':rows})
    return {'kind':'exact-original-hydration','dense_ranking':False,'generation':gen.name,'matches':matches,'scope':'Exact ID/version lookup; context is original message text, attachments uninspected.'}

def status(args):
    p=guard(Path(args.index));out={'kind':'dense-local-transformer','index':str(p),'state':'unbuilt','network':'No inference network calls; local ONNX model only'}
    if (p/'CURRENT.json').exists():
        gen,meta=active(p);out.update(state='ready',generation=gen.name,model=meta['model'],scope=compact_scope(meta['scope']),embedded_chunks=meta['embedded_chunks'])
    if (p/'generations').exists():out['checkpoints']=[{'generation':x.name,'state':json.loads((x/'manifest.json').read_text()).get('state')} for x in (p/'generations').iterdir() if x.is_dir() and (x/'manifest.json').exists()]
    return out

def publish(args):
    index=generations(args.index,args.archive);src=guard(index/'generations'/args.checkpoint)
    if src.parent!=index/'generations':raise ValueError('checkpoint outside generation directory')
    meta=json.loads((src/'manifest.json').read_text());n=meta['embedded_chunks']
    if not n:raise ValueError('checkpoint has no embedded chunks yet')
    if meta['state']=='complete':raise ValueError('complete generation already publishable; no partial copy needed')
    rows=[]
    with (src/'chunks.jsonl').open() as fh:
        for line in fh:
            rows.append(json.loads(line))
            if len(rows)==n:break
    if len(rows)!=n:raise ValueError('incomplete chunk checkpoint')
    gen=index/'generations'/('snapshot-'+uuid.uuid4().hex[:12]);private_dir(gen)
    (gen/'chunks.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));(gen/'chunks.jsonl').chmod(0o600)
    for b in meta['batches']:
        source=src/b['file']
        if digest(source.read_bytes())!=b['sha256']:raise ValueError('checkpoint vector hash differs')
        os.link(source,gen/b['file'])
    scope=meta['scope'].copy();scope['planned_excluded_occurrences']=scope.pop('excluded_occurrences',{});scope['exclusion_note']='Exclusion occurrence counts belong to the complete planned collection, not this embedded subset. Current access policy is rechecked on selected original hydration.';scope['planned_chunks']=scope['chunks'];scope['chunks']=n;scope['planned_message_versions']=scope['unique_message_versions'];parents={};paths=set()
    for r in rows:
        for v in r.get('parent_refs',[r]):parents[(v['message_id'],v['version_hash'])]=v;paths.add(v['source'])
    scope['unique_message_versions']=len(parents);counts={}
    for r in parents.values():
        key=(r['guild_id'],r['channel_id']);v=counts.setdefault(key,{'guild_id':key[0],'channel_id':key[1],'label':r['channel'],'unique_versions':0,'owner_versions':0,'indexed_date_min':r['timestamp'],'indexed_date_max':r['timestamp']});v['unique_versions']+=1;v['owner_versions']+=int(r['role']=='self');v['indexed_date_min']=min(v['indexed_date_min'],r['timestamp']);v['indexed_date_max']=max(v['indexed_date_max'],r['timestamp'])
    scope['channel_counts']=list(counts.values());scope['planned_source_files']=len(scope['source_files']);scope['source_files']=[x for x in scope['source_files'] if x['path'] in paths];scope['processed_source_files']=len(scope['source_files']);scope['partial']=True;scope['scope_note']='Immutable snapshot of an unfinished all-source build. ONLY these indexed parent versions/channels are queryable; planned corpus is not indexed coverage.'
    meta.update(state='complete',generation=gen.name,scope=scope,partial=True,checkpoint_parent=src.name,completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());dump(gen/'manifest.json',meta);ptr=index/('CURRENT.'+uuid.uuid4().hex+'.tmp');dump(ptr,{'generation':gen.name});os.replace(ptr,index/'CURRENT.json')
    return {'kind':'dense-local-transformer','state':'ready-partial','generation':gen.name,'scope':compact_scope(scope),'embedded_chunks':n,'checkpoint_parent':src.name}


def combine(args):
    import numpy as np
    index=generations(args.index,args.archive);rows=[];vectors=[];seen=set();inputs=[];scopes=[];model=None;source_files={}
    for value in args.source_index:
        src,meta=active(value)
        if model is not None and (model['sha256'],model['tokenizer_sha256'])!=(meta['model']['sha256'],meta['model']['tokenizer_sha256']):raise ValueError('cannot combine unlike embedding models')
        model=meta['model'];scopes.append(meta['scope']);inputs.append({'index':str(Path(value).resolve()),'generation':src.name,'partial':meta.get('partial',False)})
        for f in meta['scope']['source_files']:source_files[f['path']]=f
        rr=[json.loads(x) for x in (src/'chunks.jsonl').read_text().splitlines()]
        for b in meta['batches']:
            fp=src/b['file']
            if digest(fp.read_bytes())!=b['sha256']:raise ValueError('input checkpoint changed')
            ar=np.load(fp,allow_pickle=False)
            for j in range(b['start'],b['end']):
                r=rr[j]
                if r['unit_id'] in seen:continue
                seen.add(r['unit_id']);rows.append(r);vectors.append(ar[j-b['start']])
    if not rows:raise ValueError('no input vectors')
    gen=index/'generations'/('combined-'+uuid.uuid4().hex[:12]);private_dir(gen);(gen/'chunks.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows));(gen/'chunks.jsonl').chmod(0o600)
    batches=[]
    for start in range(0,len(rows),256):
        end=min(start+256,len(rows));fn=f'vectors-{start:08d}.npy';fp=gen/fn
        with fp.open('wb') as fh:np.save(fh,np.array(vectors[start:end],dtype='float32'),allow_pickle=False)
        fp.chmod(0o600);batches.append({'file':fn,'start':start,'end':end,'sha256':digest(fp.read_bytes())})
    parent_refs={};counts={};paths=set()
    for r in rows:
        for v in r.get('parent_refs',[r]):parent_refs[(v['guild_id'],v['channel_id'],v['message_id'],v['version_hash'])]=v;paths.add(v['source'])
    for r in parent_refs.values():
        key=(r['guild_id'],r['channel_id']);v=counts.setdefault(key,{'guild_id':key[0],'channel_id':key[1],'label':r['channel'],'unique_versions':0,'owner_versions':0,'indexed_date_min':r['timestamp'],'indexed_date_max':r['timestamp']});v['unique_versions']+=1;v['owner_versions']+=int(r['role']=='self');v['indexed_date_min']=min(v['indexed_date_min'],r['timestamp']);v['indexed_date_max']=max(v['indexed_date_max'],r['timestamp'])
    scope=max(scopes,key=lambda s:s.get('planned_chunks',s['chunks'])).copy();scope['planned_excluded_occurrences']=scope.pop('excluded_occurrences',scope.get('planned_excluded_occurrences',{}));scope['exclusion_note']='Planned collection exclusions, distinct from actual embedded coverage; fresh selected-source checks apply.';scope.update(planned_chunks=max(s.get('planned_chunks',s['chunks']) for s in scopes),planned_message_versions=max(s.get('planned_message_versions',s['unique_message_versions']) for s in scopes),chunks=len(rows),unique_message_versions=len(parent_refs),channel_counts=list(counts.values()),source_files=[v for k,v in source_files.items() if k in paths],processed_source_files=len(paths),partial=True,scope_note='Combined immutable dense checkpoints. Only the actual indexed parents/channels below are covered; whole-catalog planned expansion remains incomplete.')
    meta={'state':'complete','partial':True,'generation':gen.name,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'model':model,'scope':scope,'embedded_chunks':len(rows),'batches':batches,'combined_inputs':inputs};dump(gen/'manifest.json',meta);ptr=index/('CURRENT.'+uuid.uuid4().hex+'.tmp');dump(ptr,{'generation':gen.name});os.replace(ptr,index/'CURRENT.json')
    return {'kind':'dense-local-transformer','state':'ready-partial','generation':gen.name,'scope':compact_scope(scope),'combined_inputs':inputs}


def parents(args):
    gen,meta=active(args.index,args.generation);unit=None
    with (gen/'chunks.jsonl').open() as fh:
        for line in fh:
            r=json.loads(line)
            if r.get('unit_id')==args.unit_id:unit=r;break
    if unit is None:return {'kind':'exact-parent-locators','generation':gen.name,'unit_id':args.unit_id,'state':'unit-not-in-this-generation','parent_count':0,'parents':[],'raw_text_returned':False}
    output=[];cache={}
    for p in unit.get('parent_refs',[unit]):
        source_fresh(p,args.archive,cache)
        out={k:p[k] for k in ('message_id','version_hash','source','source_hash','json_pointer','original_index','timestamp','speaker_id','role','parent_char_start','parent_char_end','parent_characters') if k in p};out['source_state']='guarded-and-hash-verified-original';output.append(out)
    return {'kind':'exact-parent-locators','generation':gen.name,'unit_id':args.unit_id,'parent_count':len(output),'parents':output,'scope':compact_scope(meta['scope']),'raw_text_returned':False,'interpretation':'Complete original locator continuation for one packed dense unit; exact lookup, not semantic ranking or a belief judgment.'}


def remap(args):
    index=generations(args.index,args.archive);src=guard(index/'generations'/args.generation)
    if src.parent!=index/'generations':raise ValueError('generation outside index')
    meta=json.loads((src/'manifest.json').read_text());target=guard(Path(args.source_root));catalog=guard(Path(args.catalog))
    roots=[Path(v).absolute() for v in args.old_root]
    def path_map(value):
        q=Path(value)
        for root in roots:
            if q.is_relative_to(root):return str(target/q.relative_to(root))
        return value
    def walk(value):
        if isinstance(value,dict):
            return {k:(path_map(v) if k in ('source','path') and isinstance(v,str) else walk(v)) for k,v in value.items()}
        if isinstance(value,list):return [walk(v) for v in value]
        return value
    gen=index/'generations'/('ssd-'+uuid.uuid4().hex[:12]);private_dir(gen);n=0
    with (src/'chunks.jsonl').open() as inp,(gen/'chunks.jsonl').open('w') as out:
        for line in inp:out.write(json.dumps(walk(json.loads(line)),ensure_ascii=False)+'\n');n+=1
    (gen/'chunks.jsonl').chmod(0o600)
    for b in meta['batches']:
        source=src/b['file']
        if digest(source.read_bytes())!=b['sha256']:raise ValueError('vector checkpoint changed before SSD remap')
        os.link(source,gen/b['file'])
    meta=walk(meta)
    for source in meta['scope']['source_files']:
        p=original(source['path'],target);stat=p.stat()
        if stat.st_size!=source['bytes']:raise ValueError('SSD source size differs from recorded original')
        source['mtime_ns']=stat.st_mtime_ns
    meta.update(generation=gen.name,remapped_from=src.name,source_root=str(target),remapped_at=datetime.datetime.now(datetime.timezone.utc).isoformat());meta['scope']['original_catalog_hash']=meta['scope'].get('catalog_hash');meta['scope']['catalog']=str(catalog);meta['scope']['catalog_hash']=digest(catalog.read_bytes());dump(gen/'manifest.json',meta)
    if meta['state']=='complete':
        pointer=index/('CURRENT.'+uuid.uuid4().hex+'.tmp');dump(pointer,{'generation':gen.name});os.replace(pointer,index/'CURRENT.json')
    return {'kind':'dense-local-transformer','state':meta['state'],'generation':gen.name,'remapped_from':src.name,'source_root':str(target),'catalog':str(catalog),'rows':n,'embedded_chunks':meta['embedded_chunks'],'planned_chunks':meta['scope']['chunks'],'vector_bytes_recomputed':False,'source_bytes_recomputed':False,'source_sizes_verified':len(meta['scope']['source_files'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__);subs=parser.add_subparsers(dest='command',required=True)
    for name in ('build','query','hydrate','status','publish','combine','parents','remap'):
        p=subs.add_parser(name);p.add_argument('--index',default=str(DEFAULT));p.add_argument('--model',default=str(DEFAULT/'model-bge-small-en-v1.5'));p.add_argument('--archive',default=json.loads((Path.home()/'brain-data/upar-dip/derived/protect-your-energy/config.json').read_text()).get('owner_archive',str(Path.home()/'boden_wizard_chat_context')));p.add_argument('--threads',type=int,default=2);p.add_argument('--json',action='store_true')
        if name in ('build','query'):
            p.add_argument('--year',type=int,default=2026);p.add_argument('--channel',action='append');p.add_argument('--guild',action='append')
        if name=='build':
            p.add_argument('--catalog',required=False,default=str(DEFAULT.parent/'source-catalog.json'));p.add_argument('--owner',default=OWNER);p.add_argument('--family',action='append',choices=sorted(FAMILIES));p.add_argument('--context',type=int,default=2);p.add_argument('--max-source-mb',type=int,default=64);p.add_argument('--max-messages',type=int,default=300000);p.add_argument('--max-chunks',type=int,default=160000);p.add_argument('--batch-size',type=int,default=24);p.add_argument('--budget-seconds',type=float,default=300);p.add_argument('--resume')
        elif name=='query':p.add_argument('query',nargs='+');p.add_argument('--limit','-k',type=int,default=8);p.add_argument('--role',choices=['self','other','all'],default='self');p.add_argument('--preview-chars',type=int,default=280);p.add_argument('--include-versions',action='store_true',help='Show separate indexed variants of the same original message')
        elif name=='remap':p.add_argument('--generation',required=True);p.add_argument('--source-root',required=True);p.add_argument('--old-root',action='append',required=True);p.add_argument('--catalog',required=True)
        elif name=='parents':p.add_argument('--unit-id',required=True);p.add_argument('--generation')
        elif name=='combine':p.add_argument('--source-index',action='append',required=True)
        elif name=='publish':p.add_argument('--checkpoint',required=True)
        elif name=='hydrate':p.add_argument('--message-id',required=True);p.add_argument('--version');p.add_argument('--neighbors',type=int,default=5)
    args=parser.parse_args()
    if args.threads<1 or args.threads>8:parser.error('threads must be 1..8')
    if getattr(args,'context',0)<0 or getattr(args,'context',0)>20:parser.error('context must be 0..20')
    if getattr(args,'neighbors',0)<0 or getattr(args,'neighbors',0)>20:parser.error('neighbors must be 0..20')
    if getattr(args,'preview_chars',0)<0 or getattr(args,'preview_chars',0)>2000:parser.error('preview characters must be 0..2000')
    if getattr(args,'batch_size',1)<1 or getattr(args,'batch_size',1)>64:parser.error('batch size must be 1..64')
    if getattr(args,'limit',1)<1 or getattr(args,'limit',1)>50:parser.error('limit must be 1..50')
    # No archive text can leave this process through Python network clients.
    def audit(event, values):
        if event in ('socket.connect','socket.bind','socket.getaddrinfo'):raise PermissionError('semantic retrieval is offline')
    sys.addaudithook(audit)
    try:result=globals()[args.command](args);print(json.dumps(result,ensure_ascii=False))
    except Exception as e:print(json.dumps({'kind':'dense-local-transformer','state':'failed-closed','error':str(e)},ensure_ascii=False));sys.exit(2)
if __name__=='__main__':main()
