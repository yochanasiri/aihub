import "server-only";
export function env() {
  const authUrl = process.env.AUTH_URL || "http://localhost:3000";
  const parsed = new URL(authUrl);
  if (!['http:','https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error("Invalid AUTH_URL");
  if (process.env.NODE_ENV === "production" && parsed.protocol !== "https:") throw new Error("Production AUTH_URL must use HTTPS");
  const secret = process.env.AUTH_SECRET || "";
  const bridge = process.env.AUTH_BRIDGE_SECRET || "";
  if (secret.length < 32 || bridge.length < 32 || secret === bridge) throw new Error("Configure distinct AUTH_SECRET and AUTH_BRIDGE_SECRET values of at least 32 characters");
  const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const backendUrl = new URL(backend);
  if (!['http:','https:'].includes(backendUrl.protocol) || backendUrl.username || backendUrl.password || backendUrl.pathname !== '/') throw new Error("BACKEND_URL must be an HTTP(S) origin");
  const secure = parsed.protocol === "https:";
  return {authUrl:parsed.origin,secret,bridge,backend:backendUrl.origin,secure,
    cookieName:secure ? "__Host-aihub.session" : "aihub.session",
    googleId:process.env.GOOGLE_CLIENT_ID || "",googleSecret:process.env.GOOGLE_CLIENT_SECRET || "",
    demo:process.env.DEMO_LOGIN_ENABLED === "true"};
}
