import type { DefaultSession } from "next-auth";
import type { CampusUser } from "@/lib/backend";
declare module "next-auth" {
  interface User { backendToken?:string; backendExpires?:string; campusUser?:CampusUser; }
  interface Session { user:DefaultSession["user"] & CampusUser; }
}
declare module "next-auth/jwt" {
  interface JWT { backendToken?:string; backendExpires?:string; campusUser?:CampusUser; }
}
