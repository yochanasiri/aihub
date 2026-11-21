import { handlers } from "@/auth";
import { env } from "@/lib/env";
import type { NextRequest } from "next/server";
export const runtime="nodejs";
function trustedHost(request:NextRequest) {
  return request.headers.get("host")===new URL(env().authUrl).host;
}
export async function GET(request:NextRequest) {
  if(!trustedHost(request)) return new Response("Invalid host",{status:400});
  return handlers.GET(request);
}
export async function POST(request:NextRequest) {
  if(!trustedHost(request)) return new Response("Invalid host",{status:400});
  return handlers.POST(request);
}
