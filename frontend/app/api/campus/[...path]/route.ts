import type { NextRequest } from "next/server";
import { campusProxy } from "@/lib/proxy";
export const runtime="nodejs";
export const dynamic="force-dynamic";
type Context={params:Promise<{path:string[]}>};
async function handle(request:NextRequest,context:Context){
  const {path}=await context.params;
  const target=path.join("/");
  const doc=/^admin\/knowledge\/[a-f0-9-]{36}$/.test(target);
  const source=/^knowledge\/[a-f0-9-]{36}$/.test(target);
  const allowed=(request.method==="GET" && target==="voice/status") ||
    (request.method==="POST" && (target==="voice/transcribe" || /^voice\/speak\/[a-f0-9-]{36}$/.test(target))) ||
    (request.method==="POST" && /^messages\/[a-f0-9-]{36}\/(feedback|escalate)$/.test(target)) ||
    (request.method==="GET" && ["feedback/me","admin/review"].includes(target)) ||
    (request.method==="PATCH" && /^admin\/(review|gaps)\/[a-f0-9-]{36}$/.test(target)) ||
    (request.method==="GET" && (doc || source || ["admin/knowledge","admin/retrieval-profile"].includes(target))) ||
    (request.method==="PATCH" && (doc || target==="admin/retrieval-profile")) ||
    (request.method==="DELETE" && doc) ||
    (request.method==="POST" && (["admin/knowledge/upload","admin/retrieval-preview"].includes(target) || /^admin\/knowledge\/[a-f0-9-]{36}\/(publish|unpublish)$/.test(target))) ||
    (request.method==="POST" && ["chat/stream","retrieve","gemini/chat"].includes(target)) ||
    (request.method==="GET" && (["auth/me","admin/access","conversations","campus/status","campus/notices"].includes(target) || /^conversations\/[a-f0-9-]{36}\/messages$/.test(target))) ||
    (request.method==="PATCH" && /^admin\/users\/[a-f0-9-]{36}\/role$/.test(target));
  if(!allowed)return Response.json({detail:"Not found"},{status:404});
  return campusProxy(request,target);
}
export {handle as GET,handle as POST,handle as PATCH,handle as DELETE};
