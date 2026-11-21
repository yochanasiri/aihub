import { env } from '@/lib/env';
import { GraduationCap, ShieldCheck } from 'lucide-react';
import LoginForm from './form';
export const dynamic='force-dynamic';
export default function Login(){
 const c=env();
 return <main className="login-shell"><section className="login-card" aria-label="Institute account sign in">
  <header className="login-brand"><div className="cap-logo"><GraduationCap size={32}/></div>
   <h1>IIT INDORE <span className="brand-badge">AIHUB</span></h1><p className="institute-label">INDIAN INSTITUTE OF TECHNOLOGY INDORE</p>
   <p className="login-tagline">Sign in to access the official AI campus assistant.</p></header>
  <LoginForm google={Boolean(c.googleId&&c.googleSecret)} demo={c.demo}/>
  <footer className="login-footer"><ShieldCheck size={17}/><span>Official Knowledge Base powered by IIT Indore<br/>institute portals &amp; publications.</span></footer>
 </section><p className="login-bottom">ONE CAMPUS. EVERY ANSWER, GROUNDED.</p></main>;
}
