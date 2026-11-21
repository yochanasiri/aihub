import type { NextRequest } from "next/server";
import { registrationProxy } from "@/lib/proxy";
export function POST(request:NextRequest){return registrationProxy(request,"/v1/auth/verify-email");}
