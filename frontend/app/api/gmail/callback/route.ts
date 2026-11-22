import { NextRequest, NextResponse } from "next/server";
import { campusProxy } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request:NextRequest) {
  const destination=new URL("/account",request.url);
  if(request.nextUrl.searchParams.has("error")) {
    destination.searchParams.set("gmail","denied");
    return NextResponse.redirect(destination);
  }
  const response=await campusProxy(request,"gmail/callback");
  destination.searchParams.set("gmail",response.ok?"connected":"failed");
  return NextResponse.redirect(destination);
}
