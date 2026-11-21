import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import { z } from "zod";
import { env } from "@/lib/env";
import { backendUser, bridgePost, type Grant } from "@/lib/backend";

function authUser(grant:Grant) {
  return {id:grant.user.id,name:grant.user.name,email:grant.user.email,
    campusUser:grant.user,backendToken:grant.session_token,backendExpires:grant.expires_at};
}

export const {handlers,auth,signIn,signOut}=NextAuth(()=>{
  const c=env();
  return {
    secret:c.secret,trustHost:true,useSecureCookies:c.secure,
    session:{strategy:"jwt",maxAge:8*60*60},
    pages:{signIn:"/login",error:"/login"},
    cookies:{sessionToken:{name:c.cookieName,options:{httpOnly:true,sameSite:"lax",path:"/",secure:c.secure}}},
    providers:[
      ...(c.googleId && c.googleSecret ? [Google({clientId:c.googleId,clientSecret:c.googleSecret,
        checks:["pkce","state","nonce"],authorization:{params:{hd:"iiti.ac.in",prompt:"select_account",scope:"openid email profile"}}})] : []),
      Credentials({id:"credentials",name:"Institute password",
        credentials:{identifier:{label:"Username or email",type:"text"},password:{label:"Password",type:"password"}},
        async authorize(credentials) {
          const input=z.object({identifier:z.string().min(1).max(320),password:z.string().min(1).max(128)}).safeParse(credentials);
          if(!input.success) return null;
          try {return authUser(await bridgePost("/v1/auth/password",input.data));} catch {return null;}
        }}),
      ...(c.demo ? [Credentials({id:"demo",name:"Instant Student Demo Login",credentials:{},async authorize(){
        try {return authUser(await bridgePost("/v1/auth/demo",{}));} catch {return null;}
      }})] : []),
    ],
    callbacks:{
      async jwt({token,user,account}) {
        if(account?.provider === "google") {
          if(!account.id_token) return null;
          // Auth.js validates state/PKCE/nonce; FastAPI independently verifies this signed ID token.
          try {
            const grant=await bridgePost("/v1/auth/google",{id_token:account.id_token});
            token.backendToken=grant.session_token;token.backendExpires=grant.expires_at;token.campusUser=grant.user;
          } catch {return null;}
        } else if(user?.backendToken) {
          token.backendToken=user.backendToken;token.backendExpires=user.backendExpires;token.campusUser=user.campusUser;
        }
        // Ignore client session-update payloads; roles and identity come only from the backend.
        if(!token.backendToken || !token.backendExpires || Date.parse(token.backendExpires)<=Date.now()) return null;
        try {
          const current=await backendUser(token.backendToken);
          if(!current) return null;
          token.campusUser=current;token.sub=current.id;
          return token;
        } catch {return null;}
      },
      async session({session,token}) {
        if(token.campusUser) session.user={...session.user,...token.campusUser};
        if(token.backendExpires) session.expires=token.backendExpires as typeof session.expires;
        // Deliberately omit backendToken, Google tokens, bridge secrets and password hashes.
        return session;
      },
      async redirect({url}) {
        try {const target=new URL(url,c.authUrl);return target.origin===c.authUrl ? target.toString() : `${c.authUrl}/account`;}
        catch {return `${c.authUrl}/account`;}
      },
    },
    events:{async signOut(message){
      if("token" in message && message.token?.backendToken) {
        try {await fetch(`${c.backend}/v1/auth/logout`,{method:"POST",cache:"no-store",
          headers:{Authorization:`Bearer ${message.token.backendToken}`},signal:AbortSignal.timeout(10000)});}
        catch {console.error("Session revocation service unavailable");}
      }
    }},
    logger:{error(error){console.error("Authentication error",error.name);},warn(code){console.warn("Authentication warning",code);}},
  };
});
