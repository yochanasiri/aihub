'use client';
import { useState, useEffect, useCallback, createContext, useContext, useRef } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { signOut } from 'next-auth/react';
import { GraduationCap, MessageSquare, Grid2X2, CalendarDays, MapPin, MessageCircle, LifeBuoy, HeartPulse, Plus, LogOut, Menu, X, ShieldCheck, Globe, ChevronRight } from 'lucide-react';
import type { CampusUser } from '@/lib/backend';
import { Button } from '@/components/ui/button';
export type Conversation={id:string;title:string;updated_at:string};
type CampusContext={user:CampusUser;refreshHistory:()=>Promise<void>};
const Campus=createContext<CampusContext|null>(null);
export function useCampus(){const value=useContext(Campus);if(!value)throw new Error('Campus context required');return value;}
const navigation=[['','Conversation',MessageSquare],['/services','Services & More',Grid2X2],['/events','Events & News',CalendarDays],['/location','Campus Location',MapPin]] as const;
const help=[['/feedback','Feedback',MessageCircle],['/support','Contact & Support',LifeBuoy],['/emergency','Crisis Resources and Emergency Help',HeartPulse]] as const;
const topics=[['Programs & B.Tech','What B.Tech programs and admission information are available?'],['Hostels & Mess','What hostel and mess facilities are available?'],['Placements & TPC','Where can I find placement reports and recruiter information?'],['Campus Location','Where is IIT Indore located?']];
export function CampusShell({user,children}:{user:CampusUser;children:React.ReactNode}) {
 const pathname=usePathname();const router=useRouter();const [mobile,setMobile]=useState(false);const [history,setHistory]=useState<Conversation[]>([]);const [historyError,setHistoryError]=useState('');const [more,setMore]=useState(false);const [loadingMore,setLoadingMore]=useState(false);
 const panel=useRef<HTMLElement>(null);const menuButton=useRef<HTMLButtonElement>(null);
 const [status,setStatus]=useState({status:'checking',label:'Checking system'});
 const refreshHistory=useCallback(async()=>{if(user.is_demo)return;try{const r=await fetch('/api/campus/conversations');if(!r.ok)throw new Error();const data=await r.json();setHistory(data.items);setMore(data.has_more);setHistoryError('');}catch{setHistoryError('Recent chats could not load.');}},[user.is_demo]);
 useEffect(()=>{void refreshHistory();},[refreshHistory]);
 useEffect(()=>{if(user.is_demo)return;const controller=new AbortController();async function check(){try{const r=await fetch('/api/campus/campus/status',{signal:controller.signal});if(!r.ok)throw new Error();setStatus(await r.json());}catch{if(!controller.signal.aborted)setStatus({status:'unavailable',label:'Service unavailable'});}}void check();const interval=setInterval(check,60000);return()=>{controller.abort();clearInterval(interval);};},[user.is_demo]);
 useEffect(()=>{setMobile(false);},[pathname]);
 useEffect(()=>{if(!mobile)return;panel.current?.querySelector<HTMLElement>('a')?.focus();
  function key(e:KeyboardEvent){if(e.key==='Escape'){setMobile(false);return;}if(e.key==='Tab'){const links=Array.from(panel.current?.querySelectorAll<HTMLElement>('a[href],button:not([disabled])')||[]).filter(x=>x.getClientRects().length);const first=links[0],last=links.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}}
  function resize(){if(window.innerWidth>800)setMobile(false);}document.addEventListener('keydown',key);window.addEventListener('resize',resize);return()=>{document.removeEventListener('keydown',key);window.removeEventListener('resize',resize);menuButton.current?.focus();};},[mobile]);
 async function loadMore(){setLoadingMore(true);try{const r=await fetch(`/api/campus/conversations?offset=${history.length}`);if(!r.ok)throw new Error();const d=await r.json();setHistory(h=>[...h,...d.items]);setMore(d.has_more);}catch{setHistoryError('More chats could not load.');}finally{setLoadingMore(false);}}
 return <Campus.Provider value={{user,refreshHistory}}><div className="campus-app"><a href="#main-content" className="skip-link">Skip to content</a>
 {mobile&&<button className="sidebar-backdrop" aria-label="Close navigation" onClick={()=>setMobile(false)}/>}
 <aside ref={panel} role={mobile?"dialog":undefined} aria-modal={mobile?true:undefined} className={`sidebar ${mobile?'is-open':''}`} aria-label="Campus navigation" id="campus-navigation">
  <Link href="/account" className="sidebar-brand"><span className="cap-logo small"><GraduationCap size={23}/></span><span>IIT INDORE <span className="brand-badge">AIHUB</span><small>CAMPUS INTELLIGENCE</small></span></Link>
  <Button variant="ghost" size="icon" className="mobile-close" aria-label="Close navigation" onClick={()=>setMobile(false)}><X/></Button>
  <p className="nav-label">YOUR CAMPUS, CONNECTED</p><nav>{navigation.map(([path,title,Icon])=><Link key={path} href={`/account${path}`} onClick={()=>setMobile(false)} aria-current={pathname===`/account${path}`?'page':undefined} className="nav-link"><Icon size={18}/>{title}</Link>)}</nav>
  <div className="nav-secondary">{help.map(([path,title,Icon])=><Link key={path} href={`/account${path}`} className="nav-link" aria-current={pathname===`/account${path}`?'page':undefined}><Icon size={17}/><span>{title}</span></Link>)}</div>
  <Button className="new-conversation" onClick={()=>{setMobile(false);router.push(`/account?new=${Date.now()}`);}}><Plus/>New Conversation</Button>
  <p className="nav-label">EXPLORE TOPICS</p><div className="topic-chips">{topics.map(([label,q])=><Link key={label} href={`/account?q=${encodeURIComponent(q)}`} onClick={()=>setMobile(false)}>{label}</Link>)}</div>
  <div className="recent-heading"><p className="nav-label">RECENT CHATS</p><MessageSquare size={13}/></div>
  <div className="recent-chats">{history.map(c=><Link href={`/account?chat=${c.id}`} key={c.id} onClick={()=>setMobile(false)} title={c.title}><MessageSquare size={14}/><span>{c.title}</span></Link>)}
   {!history.length&&!historyError&&<p className="muted-empty">Conversations will appear here</p>}{historyError&&<p className="history-error">{historyError} <button onClick={()=>void refreshHistory()}>Retry</button></p>}
   {more&&<button className="text-button" disabled={loadingMore} onClick={loadMore}>{loadingMore?'Loading…':'Load more chats'}</button>}</div>
  <div className="sidebar-footer"><ShieldCheck size={16}/><span>Grounded in official sources<small>IIT Indore Knowledge Network</small></span></div>
 </aside>
 <div className="app-column" inert={mobile}><header className="app-header"><Button variant="ghost" size="icon" className="mobile-menu" ref={menuButton} onClick={()=>setMobile(!mobile)} aria-label="Open navigation" aria-expanded={mobile} aria-controls="campus-navigation"><Menu/></Button>
  <span className="iit-mark" aria-hidden="true">IIT<span>INDORE</span></span><div className="header-title"><strong>IIT Indore AI Assistant</strong><span>Indian Institute of Technology Indore Knowledge Network</span></div>
  <div className={`system-status ${user.is_demo?'preview':status.status}`} role="status"><i/>{user.is_demo?'Demo preview':status.label}</div>
  {user.role==='Admin'&&<Button variant="secondary" size="sm" asChild className="admin-link"><Link href="/account/admin"><Globe/>Knowledge Base</Link></Button>}
  <div className="user-profile"><span className="user-avatar">{user.name.trim().slice(0,1).toUpperCase()}</span><span className="user-name">{user.name}<small>{user.role}</small></span><Button variant="ghost" size="icon" aria-label="Sign out" title="Sign out" onClick={()=>signOut({redirectTo:'/login'})}><LogOut/></Button></div>
 </header>
 {user.is_demo&&<div className="demo-banner">Demo preview · Sample answers are simulated. Sign in for official-source retrieval. <Link href="/login">Institute sign-in <ChevronRight size={13}/></Link></div>}
 {process.env.NEXT_PUBLIC_AIHUB_REHEARSAL==="true"&&<div className="demo-banner">Submission rehearsal · Fictional documents and fixture models. Upload, retrieval and citations run through AIHUB.</div>}
 <main id="main-content" className="main-content" tabIndex={-1}>{children}</main></div></div></Campus.Provider>;
}
