'use client';
import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { GraduationCap, Building2, BriefcaseBusiness, HeartPulse, ArrowUpRight, Send, Sparkles, ShieldCheck, ExternalLink, Square, ChevronDown, LoaderCircle, Mail } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useCampus } from './shell';
import { consumeEvents } from '@/lib/stream';
import { demoAnswer, streamDemo } from '@/lib/demo';
import { AnswerReview, type Rating } from './review';
import { Listen, VoiceInput } from './voice';
type Source={id:string;url:string;title:string;page?:number;last_crawled?:string;department?:string};
type Message={id:string;role:'user'|'assistant';content:string;sources:Source[];latency_ms?:number;pending?:boolean;pdf?:boolean;demo?:boolean;gemini?:boolean;gmail?:boolean;searched?:boolean;casual?:boolean;feedback?:Rating|null;escalation_available?:boolean;escalation_status?:"pending"|"in_review"|"resolved"|null};
type GmailStatus={connected:boolean;email?:string;imported_count?:number;import_complete?:boolean;error?:string};
const suggestions=[
 {title:'B.Tech Programs & Admissions',text:'JEE Advanced, cutoffs, seat distribution, and branch-wise details.',question:'What B.Tech programs and admission requirements are listed in official IIT Indore sources?',icon:GraduationCap,color:'gold'},
 {title:'Hostel & Mess Accommodation',text:'Explore hostel facilities, rooms, mess services, and security.',question:'What information is available about hostel accommodation and mess facilities?',icon:Building2,color:'blue'},
 {title:'Placements & Recruiters',text:'Placement seasons, recruiters, and published placement reports.',question:'Where can I find official placement reports and recruiter information?',icon:BriefcaseBusiness,color:'purple'},
 {title:'Campus Facilities & Health',text:'Find health services, sports facilities, and campus amenities.',question:'What health and sports facilities are available on campus?',icon:HeartPulse,color:'green'}
];
function sourceHost(url:string){if(!url)return 'Uploaded PDF';if(url.startsWith("/api/campus/knowledge/"))return "Admin-published document";try{return new URL(url).hostname;}catch{return 'Official source';}}
function sourceHref(source:Source){if(/^\/api\/campus\/knowledge\/[a-f0-9-]{36}$/.test(source.url))return source.url;try{const url=new URL(source.url);return url.protocol==='https:'||url.protocol==='http:'?url.href+(source.page?`#page=${source.page}`:''):undefined;}catch{return undefined;}}
function Answer({message,playback}:{message:Message;playback:boolean}) {
 // No raw HTML, images, or arbitrary links from Markdown. Links must belong to the source list.
 const allowed=new Set(message.sources.flatMap(s=>[s.url,sourceHref(s)]));
 const content=message.content.replace(/^\*\*Information from IIT Indore (?:Official Portals|Sources)\*\*\s*/, '');
 return <article className="message-row assistant-row"><div className="assistant-avatar">{message.gmail?<Mail size={20}/>:message.gemini?<Sparkles size={20}/>:<ShieldCheck size={20}/>}</div><div className="answer-card">
  <div className="answer-label"><ShieldCheck size={14}/><span>{message.gmail?"Answer from your Gmail":message.gemini?(message.sources.length?"Gemini AI · Web sources":"Gemini AI · General answers"):message.pdf?"Uploaded PDF":message.casual?"IIT Indore Assistant":message.demo?"Simulated demo response":"Information from IIT Indore Sources"}</span></div>
  <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{img:()=>null,a:({href,children})=>href&&allowed.has(href)?<a href={href} target="_blank" rel="noopener noreferrer">{children}</a>:<span>{children}</span>}}>{content}</Markdown></div>
  {message.pending&&<div className="thinking"><LoaderCircle size={14} className="spin"/>{message.gmail?'Searching your Gmail and answering locally…':message.gemini?'Gemini is answering or searching the web…':message.demo?'Playing sample answer…':content?'Verifying sources…':'Searching official IIT Indore sources…'}</div>}
  {!message.pending&&message.sources.length>0&&<div className="answer-sources"><h3>{message.demo?"Example links · not retrieved sources":"Sources"} <span>{message.sources.length}</span></h3>{message.sources.map((source,index)=><a className="source-card" key={`${source.id}-${index}`} href={sourceHref(source)} target="_blank" rel="noopener noreferrer"><span className="source-index">{source.id}</span><span><strong>{source.title}</strong><small>{sourceHost(source.url)}{source.page?` · Page ${source.page}`:''}</small></span><ExternalLink size={14}/></a>)}</div>}
  {!message.pending&&<div className="answer-footer"><div><Listen id={message.id} enabled={playback&&!message.pdf&&!message.demo&&!message.gemini&&!message.gmail}/></div><small>{message.latency_ms!=null?`${(message.latency_ms/1000).toFixed(1)}s · `:''}{message.gmail?(message.sources.length?'Read-only Gmail sources · answer not saved to chat':'No matching emails · answer not saved to chat'):message.gemini?(message.sources.length?'Web sources returned · not campus-verified · not saved':message.searched?'Search ran · no source links returned · not saved':'No web sources returned · not saved'):message.demo?'Simulated · not saved':message.casual?'Conversation reply':message.pdf?'PDF page citations':message.sources.length?'Verified excerpts':'No verified answer found'}</small></div>}
  {!message.pending&&!message.pdf&&!message.demo&&!message.gemini&&!message.gmail&&!message.casual&&<AnswerReview id={message.id} feedback={message.feedback} canEscalate={message.escalation_available} status={message.escalation_status}/>}
  {!message.pending&&message.escalation_available&&<p className="unanswered-help">Try a more specific question or visit <Link href="/account/support">Contact &amp; Support</Link>.</p>}
 </div></article>;
}
export function Chat(){
 const {user,refreshHistory}=useCampus();const params=useSearchParams();const query=params.toString();
 const [messages,setMessages]=useState<Message[]>([]);const [input,setInput]=useState('');const [busy,setBusy]=useState(false);const [loading,setLoading]=useState(false);const [error,setError]=useState('');const [hasOlder,setHasOlder]=useState(false);const [olderLoading,setOlderLoading]=useState(false);const [stage,setStage]=useState('');const [showBottom,setShowBottom]=useState(false);
 const [selectedMode,setMode]=useState<'campus'|'gemini'|'gmail'|null>(null);
 const [pdf,setPdf]=useState<File|null>(null);
 const [pdfMessages,setPdfMessages]=useState<Message[]>([]);
 const pdfInput=useRef<HTMLInputElement>(null);
 const [geminiMessages,setGeminiMessages]=useState<Message[]>([]);
 const [gmailMessages,setGmailMessages]=useState<Message[]>([]);
 const [gmailStatus,setGmailStatus]=useState<GmailStatus|null>(null);
 const campusLink=params.has('chat')||params.has('q');
 const checkingMode=!user.is_demo&&!campusLink&&selectedMode===null&&gmailStatus===null;
 const mode=selectedMode??(!user.is_demo&&!campusLink&&gmailStatus?.connected?'gmail':'campus');
 const [gmailImporting,setGmailImporting]=useState(false);
 const [voice,setVoice]=useState({transcription:false,playback:false});
 useEffect(()=>{
  if(user.is_demo)return;
  const abort=new AbortController();
  fetch('/api/campus/voice/status',{signal:abort.signal}).then(r=>r.ok?r.json():null)
   .then(v=>{if(v&&!abort.signal.aborted)setVoice(v);}).catch(()=>{});
  return()=>abort.abort();
 },[user.is_demo]);
 useEffect(()=>{
  if(user.is_demo){setGmailStatus({connected:false});return;}
  const abort=new AbortController();
  fetch('/api/campus/gmail/status',{signal:abort.signal}).then(async response=>{
   const data=await response.json();
   if(!response.ok){
    if(!abort.signal.aborted)setGmailStatus({connected:false,error:typeof data.detail==='string'?data.detail:'Gmail status is unavailable.'});
    return;
   }
   if(!abort.signal.aborted)setGmailStatus(data as GmailStatus);
  }).catch(error=>{if(!abort.signal.aborted)setGmailStatus({connected:false,error:error instanceof Error?error.message:'Gmail status could not be loaded.'});});
  return()=>abort.abort();
 },[user.is_demo,query]);
 const conversation=useRef<string|null>(null);const controller=useRef<AbortController|null>(null);const generation=useRef(0);const scroll=useRef<HTMLDivElement>(null);const textarea=useRef<HTMLTextAreaElement>(null);const follow=useRef(true);const sending=useRef(false);
 const historyCount=useRef(0);const skipQuery=useRef<string|null>(null);
 useEffect(()=>{if(skipQuery.current===query){skipQuery.current=null;return;}
  const version=++generation.current;controller.current?.abort();sending.current=false;setBusy(false);setMessages([]);setPdf(null);setPdfMessages([]);setHasOlder(false);setError('');setStage('');follow.current=true;
  const p=new URLSearchParams(query);const id=p.get('chat');conversation.current=id;setInput(p.get('q')?.slice(0,2000)||'');historyCount.current=0;
  const abort=new AbortController();controller.current=abort;
  if(id&&!user.is_demo){setLoading(true);fetch(`/api/campus/conversations/${encodeURIComponent(id)}/messages`,{signal:abort.signal}).then(async r=>{if(!r.ok)throw new Error(r.status===404?'Conversation not found.':'Could not load this conversation.');return r.json();}).then(data=>{if(generation.current===version){setMessages(data.items);historyCount.current=data.items.length;setHasOlder(data.has_more);}}).catch(e=>{if(!abort.signal.aborted&&generation.current===version)setError(e.message);}).finally(()=>{if(generation.current===version)setLoading(false);});}else setLoading(false);
  return()=>{abort.abort();};
 },[query,user.is_demo]);
 useEffect(()=>()=>controller.current?.abort(),[]);
 useEffect(()=>{if(follow.current)scroll.current?.scrollTo({top:scroll.current.scrollHeight,behavior:'instant'});},[messages,geminiMessages,pdfMessages,stage,mode]);
 async function older(){if(!conversation.current||olderLoading)return;const version=generation.current;setOlderLoading(true);const before=scroll.current?.scrollHeight||0;try{const r=await fetch(`/api/campus/conversations/${conversation.current}/messages?offset=${historyCount.current}`);if(!r.ok)throw new Error('Older messages could not load.');const d=await r.json();if(generation.current!==version)return;follow.current=false;setMessages(m=>[...d.items,...m]);historyCount.current+=d.items.length;setHasOlder(d.has_more);requestAnimationFrame(()=>scroll.current?.scrollTo({top:scroll.current.scrollHeight-before}));}catch(e){if(generation.current===version)setError((e as Error).message);}finally{setOlderLoading(false);}}
 async function send(question=input){const text=question.trim();if(sending.current||loading||checkingMode||(mode==='gmail'&&!gmailStatus?.connected)||text.length<2)return;
  sending.current=true;const version=++generation.current;const abort=new AbortController();controller.current=abort;const answerId=crypto.randomUUID();const userId=crypto.randomUUID();let completed=false;let began=false;
  setBusy(true);setError('');setInput('');setStage(mode==='gemini'?'Asking Gemini':user.is_demo?'Playing simulated answer':'Searching official sources');follow.current=true;
  const attachment=mode==='campus'?pdf:null;
  const updateMessages=mode==='gemini'?setGeminiMessages:mode==='gmail'?setGmailMessages:attachment?setPdfMessages:setMessages;
  const priorGemini=geminiMessages.filter(m=>!m.pending).slice(-12).map(m=>({role:m.role,content:Array.from(m.content).slice(0,3000).join('')}));
  updateMessages(m=>[...m,{id:userId,role:'user',content:text,sources:[]},{id:answerId,role:'assistant',content:'',sources:[],pending:true,pdf:!!attachment,demo:user.is_demo&&mode==='campus',gemini:mode==='gemini',gmail:mode==='gmail'}]);
  function patch(update:Partial<Message>){if(generation.current===version)updateMessages(m=>m.map(x=>x.id===answerId?{...x,...update}:x));}
  try{
   if(mode==='gmail'){
    const started=performance.now();
    const r=await fetch('/api/campus/gmail/ask/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:text}),signal:abort.signal});
    if(!r.ok){
     const data=await r.json();
     throw new Error(typeof data.detail==='string'?data.detail:'Gmail could not answer. Please retry.');
    }
    if(!r.body)throw new Error('Gmail returned an empty response.');
    await consumeEvents(r.body,(name,data)=>{
     if(name==='error')throw new Error(typeof data.message==='string'?data.message:'Gmail could not answer. Please retry.');
     if(name!=='done')return;
     if(typeof data.answer!=='string'||!data.answer)throw new Error('Gmail returned an empty answer.');
     completed=true;
     patch({content:data.answer,sources:Array.isArray(data.sources)?data.sources:[],pending:false,gmail:true,latency_ms:Math.round(performance.now()-started)});
    });
    if(!completed)throw new Error('The Gmail response ended early. Please retry.');
    return;
   }
   if(mode==='gemini'){
    const started=performance.now();
    const r=await fetch('/api/campus/gemini/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:text,history:priorGemini}),signal:abort.signal});
    if(!r.ok){let detail='Gemini could not answer. Please retry.';try{const d=await r.json();if(typeof d.detail==='string')detail=d.detail;}catch{}throw new Error(detail);}
    const data=await r.json() as {answer:string;sources:Source[];searched:boolean};
    if(!data.answer)throw new Error('Gemini returned an empty answer.');
    completed=true;patch({content:data.answer,sources:data.sources||[],searched:data.searched,pending:false,latency_ms:Math.round(performance.now()-started)});
    return;
   }
   if(attachment){
    const r=await fetch('/api/campus/chat/pdf',{method:'POST',headers:{
      'Content-Type':'application/octet-stream','X-AIHUB-Question':encodeURIComponent(text),
      'X-AIHUB-Filename':encodeURIComponent(attachment.name)
    },body:attachment,signal:abort.signal});
    const data=await r.json();
    if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'The PDF could not be answered.');
    completed=true;
    patch({content:data.answer,sources:data.sources,pending:false});
    return;
   }
   if(user.is_demo){
    const sample=demoAnswer(text);const start=performance.now();began=true;
    patch({sources:sample.sources});
    await streamDemo(sample.content,abort.signal,chunk=>{
     if(generation.current===version)setMessages(m=>m.map(x=>x.id===answerId?{...x,content:x.content+chunk}:x));
    });
    completed=true;patch({pending:false,latency_ms:Math.round(performance.now()-start)});return;
   }
   const r=await fetch('/api/campus/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text,conversation_id:conversation.current}),signal:abort.signal});
   if(!r.ok){let detail='The knowledge service is unavailable. Please try again.';try{const d=await r.json();if(typeof d.detail==='string')detail=d.detail;}catch{}if(r.status===401)detail='Your session has expired. Please sign in again.';if(r.status===429)detail='The campus model is busy. Wait a few seconds and retry.';throw new Error(detail);}
   if(!r.body)throw new Error('No response stream received.');
   await consumeEvents(r.body,(name,data)=>{if(generation.current!==version)return;
    if(name==='start'){began=true;conversation.current=String(data.conversation_id);}
    if(name==='retrieval')setStage(Number(data.source_count)>0?'Reading and verifying evidence':'Checking available evidence');
    if(name==='delta'){setMessages(m=>m.map(x=>x.id===answerId?{...x,content:x.content+String(data.text)}:x));}
    if(name==='sources')patch({sources:data.sources as Source[]});
    if(name==='error')throw new Error(String(data.message||'The response could not be completed.'));
    if(name==='done'){completed=true;patch({id:String(data.message_id),content:String(data.answer),sources:data.sources as Source[],pending:false,escalation_available:data.escalation_available===true,casual:data.casual===true,latency_ms:(data.metrics as {total_ms?:number})?.total_ms});}
   });
   if(!completed)throw new Error('The connection ended before the answer was complete. Please retry.');
   if(generation.current===version&&conversation.current){const next=`chat=${conversation.current}`;skipQuery.current=next;window.history.replaceState(null,'',`/account?${next}`);historyCount.current+=2;void refreshHistory();}
  }catch(e){if(generation.current===version){updateMessages(m=>m.filter(x=>x.id!==answerId&&(began||x.id!==userId)));setError(abort.signal.aborted?'Response stopped. No incomplete answer was saved.':(e as Error).message);setInput(text);void refreshHistory();}}
  finally{if(generation.current===version){sending.current=false;setBusy(false);setStage('');textarea.current?.focus();}}
 }
 async function connectGmail(){
  setError('');
  try{
   const response=await fetch('/api/campus/gmail/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   const data=await response.json();
   if(!response.ok||typeof data.authorization_url!=='string')throw new Error(typeof data.detail==='string'?data.detail:'Gmail connection could not start.');
   window.location.assign(data.authorization_url);
  }catch(e){setError((e as Error).message);}
 }
 async function importGmail(){
  if(gmailImporting)return;
  setGmailImporting(true);setError('');
  try{
   const response=await fetch('/api/campus/gmail/import',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   const data=await response.json();
   if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Gmail import failed.');
   setGmailStatus(current=>current?{...current,imported_count:data.imported_count,import_complete:data.complete}:current);
  }catch(e){setError((e as Error).message);}
  finally{setGmailImporting(false);}
 }
 async function disconnectGmail(){
  if(!window.confirm('Disconnect Gmail and delete all email copies imported into AIHUB?'))return;
  setError('');
  try{
   const response=await fetch('/api/campus/gmail/connection',{method:'DELETE',headers:{'Content-Type':'application/json'},body:'{}'});
   const data=await response.json();
   if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Gmail could not be disconnected.');
   setGmailStatus({connected:false});setGmailMessages([]);
   if(data.google_revoked===false)setError('Disconnected from AIHUB and deleted local imports, but Google could not revoke the grant. Remove AIHUB under your Google Account security settings.');
  }catch(e){setError((e as Error).message);}
 }
 const activeMessages=mode==='gemini'?geminiMessages:mode==='gmail'?gmailMessages:pdf?pdfMessages:messages;
 const welcome=!activeMessages.length&&!loading;
 return <section className="chat-page" aria-label="AIHUB conversation">
 <div className="aihub-mode-tabs" role="group" aria-label="Choose AI chat mode">
  <button type="button" className={mode==='campus'?'active':''} disabled={busy} aria-pressed={mode==='campus'} onClick={()=>{setMode('campus');setError('');}}><ShieldCheck size={16}/> Campus knowledge base</button>
  <button type="button" className={mode==='gemini'?'active':''} disabled={busy||user.is_demo} aria-pressed={mode==='gemini'} title={user.is_demo?'Sign in to use Gemini':'Gemini general-purpose AI'} onClick={()=>{setMode('gemini');setError('');}}><Sparkles size={16}/> Gemini AI chat</button>
  <button type="button" className={mode==='gmail'?'active':''} disabled={busy||user.is_demo} aria-pressed={mode==='gmail'} title={user.is_demo?'Sign in to connect Gmail':'Read-only Gmail answers'} onClick={()=>{setMode('gmail');setError('');}}><Mail size={16}/> Gmail</button>
  <span>{mode==='gemini'?'General AI + live web search · not private campus sources':mode==='gmail'?'Read-only Gmail · answers use the configured model':'Answers with source links'}</span>
 </div>
 {mode==='gmail'&&<div className="gmail-tools" role="status">
  {gmailStatus?.connected?<><span>Connected: {gmailStatus.email} · {gmailStatus.import_complete?`${gmailStatus.imported_count} messages imported`:`${gmailStatus.imported_count||0} messages imported`}</span>
   <Button type="button" variant="secondary" size="sm" disabled={gmailImporting||gmailStatus.import_complete} onClick={()=>void importGmail()}>{gmailImporting?'Importing…':gmailStatus.import_complete?'Mailbox imported':'Import next 200 emails'}</Button>
   <Button type="button" variant="secondary" size="sm" disabled={busy||gmailImporting} onClick={()=>void disconnectGmail()}>Disconnect</Button>
  </>:<><span>{gmailStatus===null?'Checking Gmail configuration…':gmailStatus.error||'Connect your Gmail account to answer from its emails.'}</span>
   <Button type="button" variant="secondary" size="sm" disabled={user.is_demo||gmailStatus===null||Boolean(gmailStatus.error)} onClick={()=>void connectGmail()}>Connect Gmail (read-only)</Button>
  </>}
 </div>}
 <div className="chat-scroll" ref={scroll} onScroll={()=>{const el=scroll.current;if(el){follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<110;setShowBottom(!follow.current);}}}>
  {welcome?<div className="welcome"><div className="welcome-kicker"><span/>{mode==='gemini'?'GENERAL AI ASSISTANT':mode==='gmail'?'PRIVATE MAILBOX ASSISTANT':'YOUR OFFICIAL CAMPUS COMPANION'}</div><div className="welcome-cap">{mode==='gmail'?<Mail size={40}/>:<GraduationCap size={40}/>}</div><h1>{mode==='gemini'?'Chat with Gemini':mode==='gmail'?'Ask your Gmail':'Welcome to IIT Indore'}<br/><span>{mode==='gemini'?'General AI Assistant':mode==='gmail'?'Email Assistant':'AI Campus Assistant'}</span></h1><p className="welcome-description">{mode==='gemini'?'Ask general questions, chat naturally, search current information, or provide a public website URL. Web sources appear when returned by Gemini.':mode==='gmail'?'Connect your mailbox with Google read-only consent. Mail content is sent to the configured model endpoint and is not saved in chat history.':"Your official intelligence portal. Ask questions about admissions, academic regulations, departments, placements, and campus life."}</p>
   <div className="trust-line"><ShieldCheck size={14}/>{mode==='gemini'?'Gemini API':mode==='gmail'?'Configured model':user.is_demo?"Simulated streaming": "Official sources"} <span>·</span> {mode==='gemini'?"Not official-source verified":mode==='gmail'?"Your read-only mailbox":user.is_demo?"Sample answers":"Verified excerpts"} <span>·</span> Built for your campus</div>
   {mode==='campus'&&<div className="suggestion-grid">{suggestions.map(({title,text,question,icon:Icon,color})=><button key={title} className={`suggestion ${color}`} onClick={()=>{setInput(question);textarea.current?.focus();}}><span className="suggestion-icon"><Icon size={22}/></span><ArrowUpRight className="suggestion-arrow" size={17}/><strong>{title}</strong><span>{text}</span></button>)}</div>}
   <p className="welcome-hint">Start with a topic above, or ask your own question below.</p></div>:<div className="message-list">{loading?<p className="page-loading"><LoaderCircle className="spin"/>Loading conversation…</p>:<>{mode==='campus'&&!pdf&&hasOlder&&<Button variant="secondary" size="sm" disabled={olderLoading} onClick={older}>{olderLoading?'Loading…':'Load earlier messages'}</Button>}{activeMessages.map(m=>m.role==='assistant'?<Answer key={m.id} message={m} playback={voice.playback&&mode==='campus'}/>:<article key={m.id} className="message-row user-row"><div className="user-message"><span>You</span><p>{m.content}</p></div><div className="you-avatar">You</div></article>)}</>}</div>}
 </div>
 {showBottom&&<Button variant="secondary" size="sm" className="jump-bottom" onClick={()=>{follow.current=true;scroll.current?.scrollTo({top:scroll.current.scrollHeight,behavior:'smooth'});}}><ChevronDown/>Latest message</Button>}
 <div className="composer-area">{error&&<div className="chat-error" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={()=>setError('')}>×</button></div>}
  <div className="sr-only" role="status" aria-live="polite">{busy?stage:activeMessages.at(-1)?.role==='assistant'?'Answer complete.':''}</div>
  <form className="composer" onSubmit={e=>{e.preventDefault();void send();}}><textarea ref={textarea} aria-label={mode==='gemini'?"Ask Gemini":mode==='gmail'?"Ask about your email":"Ask a question about IIT Indore"} rows={2} maxLength={2000} value={input} onChange={e=>setInput(e.target.value)} disabled={busy||loading||checkingMode||(mode==='gmail'&&!gmailStatus?.connected)} placeholder={mode==='gemini'?"Ask Gemini anything or say hello…":mode==='gmail'?"Ask what’s in your email…":"Ask about IIT Indore, or say hello…"} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/>
   <div className="composer-actions">{mode==='campus'&&!user.is_demo&&<>
     <input ref={pdfInput} type="file" accept=".pdf,application/pdf" hidden onChange={e=>{
      const file=e.target.files?.[0];e.target.value='';
      if(!file)return;
      if(!file.name.toLowerCase().endsWith('.pdf')||file.size>10*1024*1024){setError('Choose a PDF up to 10 MiB.');return;}
      setError('');setPdf(file);setPdfMessages([]);
     }}/>
     <Button type="button" variant="secondary" size="sm" disabled={busy||loading} onClick={()=>pdfInput.current?.click()}>Upload PDF</Button>
     {pdf&&<Button type="button" variant="secondary" size="sm" disabled={busy} title={pdf.name} onClick={()=>{setPdf(null);setPdfMessages([]);}}>Remove PDF</Button>}
    </>}<VoiceInput key={query} enabled={voice.transcription&&!user.is_demo} disabled={busy||loading}
     onText={text=>setInput(value=>[value.trim(),text].filter(Boolean).join(' ').slice(0,2000))} onError={setError}/>{busy?<Button key="stop" type="button" size="icon" className="send-button" aria-label="Stop response" onClick={e=>{e.preventDefault();controller.current?.abort();}}><Square size={16}/></Button>:<Button key="send" type="submit" size="icon" className="send-button" disabled={input.trim().length<2||loading||checkingMode} aria-label="Send question"><Send size={19}/></Button>}</div>
  </form>{mode==='campus'&&pdf&&<small>Asking about {pdf.name}</small>}<div className="composer-footer"><span><ShieldCheck size={12}/>{mode==='gemini'?"Gemini replies are not verified against campus documents and are not saved to campus chat history.":mode==='gmail'?"Read-only Gmail access. Optional imports are encrypted at rest and deleted when disconnected. Answers go to the configured model endpoint.":user.is_demo?"Demo answers are simulated and are not saved or retrieved from the knowledge base.":"Check linked sources. Administrators may review unanswered questions."}</span><span>{input.length>1800?`${input.length}/2000`:'Enter to send · Shift + Enter for new line'}</span></div>
 </div></section>;
}
