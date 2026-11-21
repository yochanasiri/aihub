import "server-only";
import { env } from "./env";
import type { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";

export function sameOrigin(request:NextRequest) {
  return request.headers.get("origin")===env().authUrl;
}

async function readBytes(request:NextRequest, limit:number):Promise<Buffer> {
  const reader=request.body?.getReader();
  if(!reader) throw new Error("Missing body");
  const parts:Uint8Array[]= [];let size=0;
  try {
    for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;
      if(size>limit){await reader.cancel();throw new Error("Request too large");}parts.push(value);}
  } finally {reader.releaseLock();}
  return Buffer.concat(parts);
}
export async function readBody(request:NextRequest, limit=16384):Promise<string> {
  if(!(request.headers.get("content-type") || "").startsWith("application/json")) throw new Error("JSON required");
  return (await readBytes(request,limit)).toString("utf8");
}

export async function registrationProxy(request:NextRequest,path:string) {
  if(!sameOrigin(request)) return Response.json({detail:"Invalid request origin"},{status:403});
  let body:string;
  try {body=await readBody(request);JSON.parse(body);} catch {return Response.json({detail:"Invalid JSON request"},{status:400});}
  const c=env();
  try {
    const result=await fetch(`${c.backend}${path}`,{method:"POST",cache:"no-store",headers:{
      "Content-Type":"application/json","X-AIHUB-Bridge":c.bridge},body,signal:AbortSignal.timeout(20000)});
    // These two endpoints never return session tokens.
    return new Response(await result.text(),{status:result.status,headers:{"Content-Type":"application/json","Cache-Control":"no-store"}});
  } catch {return Response.json({detail:"Authentication service unavailable"},{status:503});}
}

export async function campusProxy(request:NextRequest,path:string) {
  const c=env();
  if(request.method!=="GET" && !sameOrigin(request)) return Response.json({detail:"Invalid request origin"},{status:403});
  // Pass only cookies to getToken: never accept a browser-supplied Authorization override.
  const token=await getToken({req:{headers:new Headers({cookie:request.headers.get("cookie") || ""})},
    secret:c.secret,cookieName:c.cookieName,salt:c.cookieName,secureCookie:c.secure});
  if(!token?.backendToken || !token.backendExpires || Date.parse(token.backendExpires)<=Date.now())
    return Response.json({detail:"Sign in required"},{status:401});
  const upload=path==="admin/knowledge/upload";
  const headersIn:Record<string,string>={Authorization:`Bearer ${token.backendToken}`,"Content-Type":"application/json"};
  let body:BodyInit|undefined;
  if(upload) {
    if(token.campusUser?.role!=="Admin" || token.campusUser?.is_demo)
      return Response.json({detail:"Administrator required"},{status:403});
    if(request.headers.get("content-type")!=="application/octet-stream")
      return Response.json({detail:"Upload a single file"},{status:415});
    try {body=new Uint8Array(await readBytes(request,20*1024*1024));}
    catch {return Response.json({detail:"Upload exceeds 20 MiB or was interrupted"},{status:413});}
    headersIn["Content-Type"]="application/octet-stream";
    headersIn["X-AIHUB-Filename"]=(request.headers.get("x-aihub-filename") || "").slice(0,2048);
  } else if(path==="voice/transcribe") {
    const kind=(request.headers.get("content-type")||"").split(";")[0].trim().toLowerCase();
    if(!["audio/webm","audio/ogg","audio/mp4"].includes(kind))
      return Response.json({detail:"Unsupported recording format"},{status:415});
    try {body=new Uint8Array(await readBytes(request,5*1024*1024));}
    catch {return Response.json({detail:"Recording exceeds 5 MiB or was interrupted"},{status:413});}
    headersIn["Content-Type"]=kind;
  } else if(request.method==="DELETE" && /^admin\/knowledge\/[a-f0-9-]{36}$/.test(path)) {
    // Document deletion has no JSON payload; pass the authenticated DELETE through unchanged.
    body=undefined;
  } else if(request.method!=="GET") {
    try {const raw=await readBody(request,request.method==="PATCH"&&path.startsWith("admin/knowledge/")?2*1024*1024:path==="gemini/chat"?256*1024:16384);JSON.parse(raw);body=raw;}
    catch {return Response.json({detail:"Invalid or oversized JSON request"},{status:400});}
  }
  try {
    const result=await fetch(`${c.backend}/v1/${path}${request.method==="GET" && request.nextUrl.searchParams.has("offset") ? `?offset=${encodeURIComponent(request.nextUrl.searchParams.get("offset")!)}` : ""}`,{method:request.method,cache:"no-store",
      headers:headersIn,
      body,signal:request.signal});
    const headers=new Headers({"Content-Type":result.headers.get("Content-Type") || "application/json",
      "Cache-Control":"private, no-store, no-transform","X-Accel-Buffering":"no","X-Content-Type-Options":"nosniff"});
    if(result.headers.has("Retry-After")) headers.set("Retry-After",result.headers.get("Retry-After")!);
    return new Response(result.body,{status:result.status,headers});
  } catch {return Response.json({detail:"Campus service unavailable"},{status:503});}
}
