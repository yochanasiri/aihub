import "server-only";
import { env } from "./env";
export type CampusUser = {id:string;name:string;email:string;role:"Student"|"Faculty"|"Staff"|"Admin";is_demo:boolean;email_verified:boolean};
export type Grant = {user:CampusUser;session_token:string;expires_at:string};

export async function bridgePost(path:string, body:unknown):Promise<Grant> {
  const config=env();
  const result=await fetch(`${config.backend}${path}`,{method:"POST",cache:"no-store",
    headers:{"Content-Type":"application/json","X-AIHUB-Bridge":config.bridge},
    body:JSON.stringify(body),signal:AbortSignal.timeout(20000)}).catch((error:unknown)=>{
      console.warn("AIHUB auth bridge could not reach backend", path, error instanceof Error ? error.name : "UnknownError");
      throw error;
    });
  if(!result.ok) {
    console.warn("AIHUB auth bridge rejected request", path, result.status);
    throw new Error("AuthenticationFailed");
  }
  return result.json() as Promise<Grant>;
}

export async function backendUser(token:string):Promise<CampusUser|null> {
  const result=await fetch(`${env().backend}/v1/auth/me`,{cache:"no-store",headers:{Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(10000)});
  if(!result.ok) return null;
  return result.json() as Promise<CampusUser>;
}
