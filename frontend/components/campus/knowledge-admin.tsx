'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { expectedPhraseFound } from '@/lib/knowledge-preview';

type Entry = {id:string; title:string; department:string; doc_type:string; published:boolean; indexed:boolean};
type Draft = Entry & {pages:{page:number;text:string}[]};
type Tuning = {top_k:number;min_score:number};
type TrialInput = {top_k:number|'';min_score:number|''};
function validTrial(input:TrialInput): input is Tuning {
  const {top_k,min_score}=input;
  return typeof top_k==='number'&&Number.isInteger(top_k)&&top_k>=1&&top_k<=10
    &&typeof min_score==='number'&&Number.isInteger(min_score)&&min_score>=0&&min_score<=100;
}
type Result = {
  answer:string;
  quotes:{source_id:string;text:string}[];
  candidates:{id:string;title:string;text:string;score:number;page?:number}[];
  metrics:{total_ms:number};
};

const base = '/api/campus/admin';
async function api<T>(path:string, options?:RequestInit):Promise<T> {
  const res = await fetch(base+path, options);
  const data = await res.json();
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the fields and try again.');
  return data;
}
const json = (method:string, value:unknown):RequestInit => ({
  method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(value),
});

export function KnowledgeAdmin() {
  const [entries,setEntries] = useState<Entry[]>([]);
  const [more,setMore] = useState(false);
  const [offset,setOffset] = useState(0);
  const [draft,setDraft] = useState<Draft|null>(null);
  const [page,setPage] = useState(0);
  const [dirty,setDirty] = useState(false);
  const [file,setFile] = useState<File|null>(null);
  const [fileKey,setFileKey] = useState(0);
  const [busy,setBusy] = useState('');
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
  const [live,setLive] = useState<Tuning|null>(null);
  const [trial,setTrial] = useState<TrialInput>({top_k:5,min_score:55});
  const [question,setQuestion] = useState('');
  const [expected,setExpected] = useState('');
  const [results,setResults] = useState<{label:string;result:Result;profile:Tuning}[]>([]);
  const [tested,setTested] = useState<Tuning|null>(null);
  const [testQuestion,setTestQuestion] = useState('');
  const [testExpected,setTestExpected] = useState('');

  async function loadList(start=0) {
    const data=await api<{items:Entry[];has_more:boolean}>('/knowledge?offset='+start);
    setEntries(data.items);setOffset(start);setMore(data.has_more);
  }
  useEffect(() => {
    Promise.all([loadList(),api<Tuning>('/retrieval-profile').then(p=>{setLive(p);setTrial(p);})])
      .catch(e=>setError(e.message));
  },[]);
  async function action(label:string, fn:()=>Promise<void>) {
    setBusy(label);setError('');setNotice('');
    try {await fn();} catch(e) {setError(e instanceof Error?e.message:'Request failed');}
    finally {setBusy('');}
  }
  async function open(id:string) {
    setDraft(await api<Draft>('/knowledge/'+id));setPage(0);setDirty(false);
  }
  function update(changes:Partial<Draft>) {
    setDraft(d=>d?{...d,...changes}:null);setDirty(true);
  }
  async function changePublication(publish:boolean) {
    if (!draft) return;
    const status=await api<Entry>('/knowledge/'+draft.id+'/'+(publish?'publish':'unpublish'),json('POST',{}));
    setDraft(d=>d?{...d,...status}:null);setNotice(publish?'Document indexed and published.':'Document is now a draft.');
    await loadList(offset);
  }
  async function compare() {
    if (!live || !validTrial(trial)) return;
    setResults([]);setTested(null);setTestQuestion(question);setTestExpected(expected.trim());
    const run=(profile:Tuning)=>api<Result>('/retrieval-preview',json('POST',{...profile,question}));
    const first=await run(live);
    setResults([{label:'Live settings',result:first,profile:live}]);
    const unchanged=live.top_k===trial.top_k&&live.min_score===trial.min_score;
    const second=unchanged?first:await run(trial);
    setResults([{label:'Live settings',result:first,profile:live},{label:'Trial settings',result:second,profile:trial}]);
    setTested(trial);
  }
  const locked=!!busy;
  const testedCurrent=validTrial(trial)&&tested?.top_k===trial.top_k&&tested.min_score===trial.min_score;
  return <div className="knowledge-admin">
    {busy&&<p role="status">{busy}…</p>}
    {notice&&<p role="status">{notice}</p>}
    {error&&<p role="alert" className="chat-error">{error}</p>}
    <section className="info-card">
      <h2>Upload a document</h2>
      <p>PDF, UTF-8 TXT or Markdown, PNG and JPEG · up to 20 MiB. Scans and photos use English OCR. Uploads start as drafts.</p>
      <form onSubmit={e=>{e.preventDefault();if(file)void action('Extracting text',async()=>{
        if(file.size>20*1024*1024)throw new Error('File exceeds 20 MiB');
        const result=await api<Entry>('/knowledge/upload',{method:'POST',headers:{
          'Content-Type':'application/octet-stream','X-AIHUB-Filename':encodeURIComponent(file.name)},body:file});
        setFile(null);setFileKey(n=>n+1);await open(result.id);await loadList();
        setNotice('Draft created. Review the extracted text before publishing.');
      });}}>
        <label>Choose a file<input key={fileKey} type="file" accept=".pdf,.txt,.md,.png,.jpg,.jpeg" disabled={locked||dirty} onChange={e=>setFile(e.target.files?.[0]??null)}/></label>
        <Button type="submit" disabled={!file||locked||dirty}>Upload as draft</Button>
      </form>
    </section>
    <section className="info-card">
      <h2>Uploaded documents</h2>
      {entries.length===0&&<p>No uploaded documents on this page.</p>}
      <ul>{entries.map(item=><li key={item.id}>
        <Button variant="ghost" disabled={locked||dirty} onClick={()=>void action('Opening document',()=>open(item.id))}>{item.title}</Button>
        <span>{item.published?(item.indexed?'Published':'Needs indexing'):'Draft'}</span>
      </li>)}</ul>
      <div className="knowledge-actions">
        <Button disabled={!offset||locked||dirty} onClick={()=>void action('Loading documents',()=>loadList(offset-50))}>Previous</Button>
        <Button disabled={!more||locked||dirty} onClick={()=>void action('Loading documents',()=>loadList(offset+50))}>Next</Button>
      </div>
    </section>
    {draft&&<section className="info-card">
      <h2>{draft.published?'Published document':'Review draft'}</h2>
      <label>Title<input maxLength={200} value={draft.title} disabled={locked||draft.published} onChange={e=>update({title:e.target.value})}/></label>
      <label>Department<input maxLength={200} value={draft.department} disabled={locked||draft.published} onChange={e=>update({department:e.target.value})}/></label>
      <label>Page<select value={page} disabled={locked} onChange={e=>setPage(Number(e.target.value))}>
        {draft.pages.map((p,i)=><option key={p.page} value={i}>Page {p.page}</option>)}
      </select></label>
      <label>Extracted text<textarea rows={10} value={draft.pages[page].text} disabled={locked} readOnly={draft.published}
        onChange={e=>update({pages:draft.pages.map((p,i)=>i===page?{...p,text:e.target.value}:p)})}/></label>
      <div className="knowledge-actions">
        {draft.published?<Button disabled={locked} onClick={()=>void action('Unpublishing',()=>changePublication(false))}>Unpublish</Button>:<>
          <Button disabled={locked||!dirty} onClick={()=>void action('Saving draft',async()=>{
            const status=await api<Entry>('/knowledge/'+draft.id,json('PATCH',{title:draft.title,department:draft.department,pages:draft.pages.map(p=>p.text)}));
            setDraft(d=>d?{...d,...status}:null);setDirty(false);setNotice('Draft saved.');
          })}>Save corrections</Button>
          <Button disabled={locked||dirty} onClick={()=>void action('Indexing document',()=>changePublication(true))}>Publish to user chat</Button>
          {dirty&&<Button variant="ghost" disabled={locked} onClick={()=>void action('Restoring saved draft',()=>open(draft.id))}>Discard edits</Button>}
          {!draft.published&&<Button variant="destructive" disabled={locked||dirty} onClick={()=>void action('Deleting document',async()=>{
            if(!window.confirm(`Permanently delete "${draft.title}"? This cannot be undone.`))return;
            await api<{deleted:boolean}>('/knowledge/'+draft.id,{method:'DELETE'});
            setDraft(null);setPage(0);setDirty(false);await loadList(offset);setNotice('Document permanently deleted.');
          })}>Delete permanently</Button>}
        </>}
      </div>
      {dirty&&<p>Save or discard your edits before switching documents or publishing.</p>}
    </section>}
    <section className="info-card">
      <h2>Test and tune retrieval</h2>
      <p>Compare trial settings against live settings using published documents. Preview questions do not appear in user chat history.</p>
      {live&&<p>Live: {live.top_k} evidence chunks · minimum similarity {live.min_score}%</p>}
      <div className="knowledge-actions">
        <label>Evidence chunks<input type="number" min={1} max={10} value={trial.top_k} disabled={locked} onChange={e=>{const n=e.currentTarget.valueAsNumber;setTrial(p=>({...p,top_k:Number.isFinite(n)?n:''}));}}/></label>
        <label>Minimum similarity (%)<input type="number" min={0} max={100} value={trial.min_score} disabled={locked} onChange={e=>{const n=e.currentTarget.valueAsNumber;setTrial(p=>({...p,min_score:Number.isFinite(n)?n:''}));}}/></label>
      </div>
      <label>Test question<textarea rows={2} maxLength={2000} value={question} disabled={locked} onChange={e=>setQuestion(e.target.value)}/></label>
      <label>Expected phrase in answer (optional)<input maxLength={300} value={expected} disabled={locked} onChange={e=>setExpected(e.target.value)}/></label>
      <div className="knowledge-actions">
        <Button disabled={locked||!live||question.trim().length<3||!validTrial(trial)}
          onClick={()=>void action('Comparing answers',compare)}>Compare answers</Button>
        <Button disabled={locked||!testedCurrent||question!==testQuestion||expected.trim()!==testExpected}
          onClick={()=>void action('Applying retrieval settings',async()=>{
            setLive(await api<Tuning>('/retrieval-profile',json('PATCH',trial)));setTested(null);setNotice('Settings applied to new user questions.');
          })}>Apply tested settings</Button>
      </div>
      {results.length>0&&<p>Compared question: {testQuestion}</p>}
      <div className="knowledge-results">{results.map(({label,result,profile})=><article key={label}>
        <h3>{label}</h3>
        <p>{profile.top_k} chunks · minimum {profile.min_score}% · {Math.round(result.metrics.total_ms)} ms</p>
        {testExpected&&<p>Expected phrase: {expectedPhraseFound(result.answer, result.quotes, testExpected)?'Found':'Not found'} (text check only)</p>}
        {result.quotes.length?result.quotes.map((q,i)=><blockquote key={i}>{q.text} [{q.source_id}]</blockquote>):<p>{result.answer}</p>}
        <h4>Retrieved evidence ({result.candidates.length})</h4>
        {result.candidates.map(c=><details key={c.id}><summary>{c.id} · {c.title}{c.page? ` · p. ${c.page}`:''} · {Math.round(c.score*100)}%</summary><p>{c.text}</p></details>)}
      </article>)}</div>
    </section>
  </div>;
}
