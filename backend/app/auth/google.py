import asyncio
import threading
from datetime import datetime, timezone
import requests
from cachecontrol import CacheControl
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from .security import institute_email


class GoogleVerifier:
    def __init__(self, config, transport=None):
        self.config = config
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.trust_env = False
        raw_transport = transport or Request(session=CacheControl(self.session))
        self.transport = lambda url, **kwargs: raw_transport(url, timeout=10, **kwargs)

    async def verify(self, token):
        if not self.config.google_client_id:
            raise RuntimeError("Google sign-in is not configured")
        def check():
            with self._lock:
                # Library verifies the Google certificate signature, audience, expiry and issuer.
                claims = id_token.verify_oauth2_token(token,self.transport,self.config.google_client_id,
                                                     clock_skew_in_seconds=30)
            stamp = datetime.now(timezone.utc).timestamp()
            if claims.get("iss") not in {"accounts.google.com","https://accounts.google.com"}:
                raise ValueError("Invalid issuer")
            if claims.get("aud") != self.config.google_client_id:
                raise ValueError("Invalid audience")
            if claims.get("azp") not in (None,self.config.google_client_id):
                raise ValueError("Invalid authorized party")
            if claims.get("email_verified") is not True or claims.get("hd") != "iiti.ac.in":
                raise ValueError("Verified institute Workspace account required")
            if not isinstance(claims.get("sub"),str) or not 1 <= len(claims["sub"]) <= 255:
                raise ValueError("Invalid subject")
            if not isinstance(claims.get("exp"),(int,float)) or claims["exp"] <= stamp:
                raise ValueError("Expired ID token")
            claims["email"] = institute_email(claims.get("email",""))
            return claims
        return await asyncio.to_thread(check)

    def close(self):
        self.session.close()
