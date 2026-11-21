'use client';

import { useEffect, useState } from 'react';
import { ThumbsDown, ThumbsUp } from 'lucide-react';
import Link from 'next/link';
import { Button } from '@/components/ui/button';
import { useCampus } from './shell';

export type Rating = {rating:1|-1;comment:string|null};
type Status = 'pending'|'in_review'|'resolved';
type Ticket = {id:string;question:string|null;description:string;status:Status};
type Queue = {tickets:Ticket[];ratings:{id:string;comment:string|null;answer:string}[];gaps:{id:string;question:string;reason:string}[];has_more:boolean};

const statusLabel:Record<Status,string> = {pending:'Pending',in_review:'In review',resolved:'Resolved'};
async function request<T>(path:string, method='GET', payload?:object):Promise<T> {
  const response=await fetch('/api/campus/'+path,{method,...(method==='GET'?{}:{
    headers:{'Content-Type':'application/json'},body:JSON.stringify(payload??{}),
  })});
  const data=await response.json();
  if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Request failed. Try again.');
  return data;
}

export function AnswerReview({id,feedback,canEscalate,status}:{id:string;feedback?:Rating|null;canEscalate?:boolean;status?:Status|null}) {
  const [saved,setSaved]=useState(feedback);
  const [rating,setRating]=useState<1|-1|null>(null);
  const [comment,setComment]=useState(feedback?.comment??'');
  const [ticket,setTicket]=useState(status);
  const [showRequest,setShowRequest]=useState(false);
  const [description,setDescription]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  async function submit(action:()=>Promise<void>) {
    setBusy(true);setMessage('');setError('');
    try {await action();} catch(e){setError(e instanceof Error?e.message:'Request failed');}
    finally{setBusy(false);}
  }
  return <div className="answer-review">
    <div className="knowledge-actions" aria-label="Rate this answer">
      <Button size="sm" variant="secondary" aria-pressed={(rating??saved?.rating)===1} disabled={busy} onClick={()=>{setRating(1);setShowRequest(false);setMessage('');}}><ThumbsUp size={14}/> Helpful</Button>
      <Button size="sm" variant="secondary" aria-pressed={(rating??saved?.rating)===-1} disabled={busy} onClick={()=>{setRating(-1);setShowRequest(false);setMessage('');}}><ThumbsDown size={14}/> Not helpful</Button>
      {canEscalate&&!ticket&&<Button size="sm" variant="secondary" disabled={busy} onClick={()=>{setShowRequest(true);setRating(null);setMessage('');}}>Request review</Button>}
      {ticket&&<Link href="/account/feedback">Review request: {statusLabel[ticket]}</Link>}
    </div>
    {rating!==null&&<form onSubmit={e=>{e.preventDefault();void submit(async()=>{
      setSaved(await request<Rating>('messages/'+id+'/feedback','POST',{rating,comment}));
      setRating(null);setMessage('Feedback saved.');
    });}}>
      <label>Optional comment<textarea rows={2} maxLength={1000} value={comment} disabled={busy} onChange={e=>setComment(e.target.value)}/></label>
      <Button size="sm" type="submit" disabled={busy}>Save feedback</Button>
    </form>}
    {showRequest&&<form onSubmit={e=>{e.preventDefault();void submit(async()=>{
      const result=await request<Ticket>('messages/'+id+'/escalate','POST',{description});
      setTicket(result.status);setShowRequest(false);setMessage('Added to the AIHUB review queue.');
    });}}>
      <p>The AIHUB review team will see your question and any note you include.</p>
      <label>Optional note<textarea rows={2} maxLength={1000} value={description} disabled={busy} onChange={e=>setDescription(e.target.value)}/></label>
      <Button size="sm" type="submit" disabled={busy}>Send request</Button>
    </form>}
    {(rating!==null||showRequest)&&<Button size="sm" variant="ghost" disabled={busy} onClick={()=>{setRating(null);setShowRequest(false);setError('');}}>Cancel</Button>}
    {message&&<p role="status">{message}</p>}
    {error&&<p role="alert">{error}</p>}
  </div>;
}

function Paging({offset,more,busy,load}:{offset:number;more:boolean;busy:boolean;load:(offset:number)=>void}) {
  return <div className="knowledge-actions">
    <Button size="sm" variant="secondary" disabled={!offset||busy} onClick={()=>load(offset-30)}>Previous</Button>
    <Button size="sm" variant="secondary" disabled={!more||busy} onClick={()=>load(offset+30)}>Next</Button>
  </div>;
}
export function MyReviewRequests() {
  const {user}=useCampus();
  const [data,setData]=useState<{items:Ticket[];has_more:boolean}|null>(null);
  const [offset,setOffset]=useState(0);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function load(start:number) {
    setBusy(true);setError('');
    try {setData(await request('feedback/me?offset='+start));setOffset(start);}
    catch(e){setError(e instanceof Error?e.message:'Could not load requests');}
    finally{setBusy(false);}
  }
  useEffect(()=>{if(!user.is_demo)void load(0);},[user.is_demo]);
  if(user.is_demo)return <p>Sign in with your institute account to submit feedback or request a review.</p>;
  return <section className="info-card">
    <h2>Your review requests</h2>
    <p>Rate a reply from the conversation. When AIHUB cannot answer, use <strong>Request review</strong> to add the question to the review queue.</p>
    {busy&&<p role="status">Loading…</p>}
    {error&&<p role="alert">{error}</p>}
    {data&&!data.items.length&&<p>No requests on this page.</p>}
    {data?.items.map(item=><article key={item.id} className="review-entry">
      <h3>{item.question??'Question no longer available'}</h3>
      <p>Status: {statusLabel[item.status]}</p>{item.description&&<p>{item.description}</p>}
    </article>)}
    <Paging offset={offset} more={!!data?.has_more} busy={busy} load={load}/>
  </section>;
}

export function ReviewAdmin() {
  const [data,setData]=useState<Queue|null>(null);
  const [offset,setOffset]=useState(0);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function load(start=0, change?:()=>Promise<unknown>) {
    setBusy(true);setError('');
    try {
      if(change)await change();
      setData(await request<Queue>('admin/review?offset='+start));setOffset(start);
    }catch(e){setError(e instanceof Error?e.message:'Could not load review queue');}
    finally{setBusy(false);}
  }
  return <section className="info-card">
    <h2>Answer review queue</h2>
    <p>Use feedback and unanswered questions to decide which sources to add or correct. Changing a status updates the in-app queue.</p>
    <Button size="sm" variant="secondary" disabled={busy} onClick={()=>void load()}>Load review queue</Button>
    {busy&&<p role="status">Loading…</p>}
    {error&&<p role="alert">{error}</p>}
    {data&&<>
      <h3>Review requests</h3>
      {!data.tickets.length&&<p>No requests on this page.</p>}
      {data.tickets.map(item=><article className="review-entry" key={item.id}>
        <h4>{item.question??'Question no longer available'}</h4>
        {item.description&&<p>{item.description}</p>}
        <label>Status<select disabled={busy} value={item.status} onChange={e=>{
          const status=e.target.value;
          void load(offset,()=>request('admin/review/'+item.id,'PATCH',{status}));
        }}>
          {Object.entries(statusLabel).map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select></label>
      </article>)}
      <h3>Unhelpful answers</h3>
      {!data.ratings.length&&<p>No negative ratings on this page.</p>}
      {data.ratings.map(item=><details className="review-entry" key={item.id}><summary>{item.comment||'Marked not helpful'}</summary><p>{item.answer}</p></details>)}
      <h3>Unanswered questions</h3>
      {!data.gaps.length&&<p>No unresolved questions on this page.</p>}
      {data.gaps.map(item=><article className="review-entry" key={item.id}>
        <p>{item.question}</p>
        <small>{item.reason==='no_current_evidence'?'No matching source':'No verified excerpt'}</small>
        <Button size="sm" variant="secondary" disabled={busy} onClick={()=>void load(offset,()=>request('admin/gaps/'+item.id,'PATCH'))}>Mark reviewed</Button>
      </article>)}
      <Paging offset={offset} more={data.has_more} busy={busy} load={load}/>
    </>}
  </section>;
}
