"use client";
import { useState } from "react";
import { signIn } from "next-auth/react";
export default function LoginForm({google,demo}:{google:boolean;demo:boolean}){
  const [mode,setMode]=useState<"signin"|"register"|"verify">("signin");
  const [identifier,setIdentifier]=useState("");const [name,setName]=useState("");const [username,setUsername]=useState("");
  const [password,setPassword]=useState("");const [code,setCode]=useState("");const [registration,setRegistration]=useState("");
  const [message,setMessage]=useState("");const [busy,setBusy]=useState(false);
  async function login(provider="credentials"){
    setBusy(true);setMessage("");
    try {const result=await signIn(provider,{identifier,password,redirect:false,redirectTo:"/account"});
      if(result?.error){setMessage(provider==="demo"?"Demo sign-in failed. Check that the backend is running and demo mode is enabled in both environments.":"Unable to sign in. Check your details and institute account verification.");return;}
      window.location.assign("/account");
    } catch {setMessage("Sign-in is unavailable. Please try again.");} finally{setBusy(false);}
  }
  async function submit(event:React.FormEvent){
    event.preventDefault();if(mode==="signin"){await login();return;}
    setBusy(true);setMessage("");
    try {
      const response=await fetch(mode==="register"?"/api/account/register":"/api/account/verify-email",{
        method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(mode==="register"?
          {email:identifier,username,name,password}:{registration_id:registration,code,password})});
      const data=await response.json();
      if(!response.ok){setMessage(typeof data.detail==="string"?data.detail:"Please check the form fields.");return;}
      if(mode==="register"){setRegistration(data.registration_id);setMode("verify");setMessage("If this address is eligible for registration, a six-digit code will arrive in your institute inbox. If you already have an account, sign in instead.");}
      else{setPassword("");setMode("signin");setMessage("Email verified. You can now sign in.");}
    } catch{setMessage("The service is unavailable. Please try again.");}finally{setBusy(false);}
  }
  function tab(value:"signin"|"register"){setMode(value);setMessage("");setPassword("");setCode("");setRegistration("");}
  return <><div className="tabs"><button type="button" aria-pressed={mode==="signin"} onClick={()=>tab("signin")}>Sign In</button>
    <button type="button" aria-pressed={mode!=="signin"} onClick={()=>tab("register")}>Register</button></div>
    <form onSubmit={submit}>
      {mode==="register"&&<><label>Full name<input autoComplete="name" value={name} onChange={e=>setName(e.target.value)} required maxLength={200}/></label>
        <label>Username<input autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required pattern="[a-zA-Z][a-zA-Z0-9_]{2,39}"/></label></>}
      {mode!=="verify"&&<label>{mode==="register"?"Institute email":"Username or email"}<input type={mode==="register"?"email":"text"} autoComplete="username"
        placeholder="e.g., student@iiti.ac.in or username" value={identifier} onChange={e=>setIdentifier(e.target.value)} required maxLength={320}/></label>}
      {mode==="verify"&&<label>Verification code<input value={code} onChange={e=>setCode(e.target.value)} required pattern="[0-9]{6}" inputMode="numeric" autoComplete="one-time-code" maxLength={6}/></label>}
      <label>{mode==="verify"?"Password chosen during registration":"Password"}<input type="password" autoComplete={mode==="signin"?"current-password":"new-password"}
        value={password} onChange={e=>setPassword(e.target.value)} required minLength={mode==="signin"?1:12} maxLength={128}/></label>
      {mode==="register"&&<p className="hint">Use at least 12 characters. Access starts after institute email verification.</p>}
      <button className="primary" disabled={busy}>{busy?"Please wait…":mode==="signin"?"Sign In to Campus Assistant":mode==="register"?"Send verification code":"Verify institute email"}</button>
    </form>
    <p className="message" role="status">{message}</p>
    {mode==="signin"&&<><p className="divider">OR CONTINUE WITH</p><button className="google" disabled={busy||!google} onClick={()=>signIn("google",{redirectTo:"/account"})}><span className="google-mark" aria-hidden="true">G</span>Sign in with Google</button>
      {!google&&<p className="hint">Google sign-in is awaiting institute OAuth configuration.</p>}
      {demo&&<button disabled={busy} className="secondary" onClick={()=>login("demo")}>Instant Student Demo Login</button>}</>}
  </>;
}
