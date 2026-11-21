import secrets
from datetime import timedelta, datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ..models import User, AuthSession, PendingRegistration, GoogleTokenUse, AuthRateLimit, AuthAudit, now
from .security import institute_email, token_hash, keyed_hash, aware, hash_password, verify_password, hasher


def public_user(user):
    return {"id":user.id,"email":user.email,"name":user.name,"role":user.role,
            "is_demo":user.is_demo,"email_verified":user.email_verified}


class AuthService:
    def __init__(self, config, sessions, google, mailer):
        self.config,self.sessions,self.google,self.mailer = config,sessions,google,mailer

    async def rate_limit(self, action, identifier, limit, seconds=900):
        stamp=now()
        bucket=int(stamp.timestamp())//seconds
        key=keyed_hash(self.config.auth_bridge_secret,f"{action}:{identifier}:{bucket}")
        async with self.sessions() as session:
            insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
            statement=insert(AuthRateLimit).values(key=key,hits=1,expires_at=stamp+timedelta(seconds=seconds))
            statement=statement.on_conflict_do_update(index_elements=[AuthRateLimit.key],
                                                      set_={"hits":AuthRateLimit.hits+1}).returning(AuthRateLimit.hits)
            hits=await session.scalar(statement)
            await session.commit()
        if hits>limit:
            raise HTTPException(429,"Too many attempts. Try again later.",headers={"Retry-After":str(seconds)})

    async def grant(self, session, user, method):
        raw=secrets.token_urlsafe(48)
        expires=now()+timedelta(hours=1 if user.is_demo else self.config.session_hours)
        session.add(AuthSession(user_id=user.id,token_hash=token_hash(raw),method=method,expires_at=expires))
        session.add(AuthAudit(actor_id=user.id,subject_id=user.id,event="session_created",details={"method":method}))
        await session.flush()
        return {"user":public_user(user),"session_token":raw,"expires_at":expires.isoformat()}

    async def register(self, data):
        await self.rate_limit("registration",data.email,5,3600)
        if not self.config.smtp_host:
            raise HTTPException(503,"Email verification is not configured")
        password=await hash_password(data.password)
        reg_id,code=str(uuid4()),f"{secrets.randbelow(1000000):06d}"
        async with self.sessions() as session:
            existing=await session.scalar(select(User).where(User.email==data.email))
            if existing:
                # Match response shape without exposing account existence.
                return {"registration_id":reg_id,"message":"If eligible, a verification code has been sent."}
            record=PendingRegistration(id=reg_id,email=data.email,username=data.username,name=data.name,
                password_hash=password,code_hash=keyed_hash(self.config.auth_bridge_secret,f"{reg_id}:{code}"),
                expires_at=now()+timedelta(minutes=15))
            session.add(record)
            await session.commit()
        try:
            await self.mailer.send_code(data.email,code)
        except Exception:
            async with self.sessions() as session:
                await session.execute(update(PendingRegistration).where(PendingRegistration.id==reg_id).values(consumed_at=now()))
                await session.commit()
            raise HTTPException(503,"Verification email could not be delivered. Please try again later.")
        return {"registration_id":reg_id,"message":"If eligible, a verification code has been sent."}

    async def verify_registration(self, data):
        await self.rate_limit("verification",str(data.registration_id),10)
        async with self.sessions() as session:
            record=await session.scalar(select(PendingRegistration).where(
                PendingRegistration.id==str(data.registration_id)).with_for_update())
            if not record or record.consumed_at or aware(record.expires_at)<=now() or record.attempts>=5:
                raise HTTPException(400,"Invalid or expired verification request")
            record.attempts+=1
            correct=secrets.compare_digest(record.code_hash,keyed_hash(self.config.auth_bridge_secret,f"{record.id}:{data.code}"))
            password_ok=await verify_password(record.password_hash,data.password)
            if not correct or not password_ok:
                await session.commit()
                raise HTTPException(400,"Invalid verification code or password")
            # No privilege or account state is accepted from registration input.
            user=User(email=record.email,username=record.username,name=record.name,
                      password_hash=record.password_hash,email_verified=True,role="Student",is_demo=False)
            session.add(user)
            record.consumed_at=now()
            try:
                await session.flush()
                session.add(AuthAudit(actor_id=user.id,subject_id=user.id,event="email_verified",details={}))
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise HTTPException(409,"Account or username is already registered. Sign in or choose another username.")
        return {"verified":True}

    async def password_login(self, identifier, password):
        identifier=identifier.strip().lower()
        await self.rate_limit("password",identifier,10)
        async with self.sessions() as session:
            predicate=User.email==identifier if "@" in identifier else User.username==identifier
            user=await session.scalar(select(User).where(predicate).with_for_update())
            valid=await verify_password(user.password_hash if user else None,password)
            if not valid or not user or not user.email_verified or user.is_demo or not user.is_active:
                raise HTTPException(401,"Invalid username/email or password")
            try: institute_email(user.email)
            except ValueError: raise HTTPException(401,"Invalid username/email or password")
            if hasher.check_needs_rehash(user.password_hash):
                user.password_hash=await hash_password(password)
            grant=await self.grant(session,user,"password")
            await session.commit()
            return grant

    async def google_login(self, raw):
        if not self.config.google_client_id:
            raise HTTPException(503,"Google sign-in is not configured")
        try:
            claims=await self.google.verify(raw)
        except Exception:
            raise HTTPException(401,"A verified IIT Indore Google Workspace account is required")
        # Recheck policy even if a transport adapter is replaced during integration.
        try: email=institute_email(claims.get("email",""))
        except ValueError: raise HTTPException(401,"Institute account required")
        if claims.get("hd")!="iiti.ac.in" or claims.get("email_verified") is not True:
            raise HTTPException(401,"Institute account required")
        async with self.sessions() as session:
            session.add(GoogleTokenUse(token_hash=token_hash(raw),
                expires_at=datetime.fromtimestamp(claims["exp"],timezone.utc)))
            try:
                await session.flush()
                user=await session.scalar(select(User).where(User.google_sub==claims["sub"]).with_for_update())
                if user and user.email!=email:
                    raise HTTPException(403,"Institute email changed. Contact an administrator.")
                if user is None:
                    user=await session.scalar(select(User).where(User.email==email).with_for_update())
                    if user and (user.google_sub not in (None,claims["sub"]) or not user.email_verified or user.is_demo):
                        raise HTTPException(403,"Account linking requires administrator review")
                    if user is None:
                        user=User(email=email,name=str(claims.get("name") or email.split("@")[0])[:200],
                                  role="Student",email_verified=True,is_demo=False,is_active=True)
                        session.add(user)
                    user.google_sub=claims["sub"]
                if not user.is_active:
                    raise HTTPException(403,"Account is disabled")
                await session.flush()
                grant=await self.grant(session,user,"google")
                await session.commit()
                return grant
            except IntegrityError:
                await session.rollback()
                raise HTTPException(401,"Sign-in token already used or account changed. Start sign-in again.")

    async def demo_login(self):
        if not self.config.demo_login_enabled:
            raise HTTPException(403,"Student demo is disabled")
        async with self.sessions() as session:
            user=User(email=f"demo-{uuid4()}@demo.invalid",name="Demo Student",role="Student",is_demo=True,email_verified=False)
            session.add(user)
            await session.flush()
            grant=await self.grant(session,user,"demo")
            await session.commit()
            return grant

    async def authenticate(self, raw):
        if not raw or not 32<=len(raw)<=200:
            raise HTTPException(401,"Sign in required")
        async with self.sessions() as session:
            row=(await session.execute(select(AuthSession,User).join(User,User.id==AuthSession.user_id)
                  .where(AuthSession.token_hash==token_hash(raw)))).first()
            if not row:
                raise HTTPException(401,"Session is invalid or expired")
            auth_session,user=row
            if auth_session.revoked_at or aware(auth_session.expires_at)<=now() or not user.is_active:
                raise HTTPException(401,"Session is invalid or expired")
            if user.is_demo:
                if not self.config.demo_login_enabled:
                    raise HTTPException(401,"Demo access has been disabled")
            else:
                try: institute_email(user.email)
                except ValueError: raise HTTPException(401,"Institute account required")
                if not user.email_verified:
                    raise HTTPException(401,"Email verification required")
            return user

    async def logout(self, raw):
        async with self.sessions() as session:
            await session.execute(update(AuthSession).where(AuthSession.token_hash==token_hash(raw)).values(revoked_at=now()))
            await session.commit()

    async def set_role(self, actor, user_id, role):
        async with self.sessions() as session:
            if session.bind.dialect.name=="postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(73480220)"))
            current_actor=await session.get(User,actor.id)
            if not current_actor or current_actor.role!="Admin" or current_actor.is_demo or not current_actor.is_active:
                raise HTTPException(403,"Admin role required")
            target=await session.scalar(select(User).where(User.id==user_id).with_for_update())
            if not target or target.is_demo or not target.email_verified or not target.is_active:
                raise HTTPException(404,"Eligible user not found")
            if target.role=="Admin" and role!="Admin":
                count=await session.scalar(select(func.count()).select_from(User).where(User.role=="Admin",User.is_active.is_(True)))
                if count<=1: raise HTTPException(409,"Cannot remove the last active administrator")
            old_role=target.role
            target.role=role
            await session.execute(update(AuthSession).where(AuthSession.user_id==target.id).values(revoked_at=now()))
            session.add(AuthAudit(actor_id=actor.id,subject_id=target.id,event="role_changed",details={"from":old_role,"to":role}))
            await session.commit()
            return public_user(target)
