import secrets
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .security import institute_email, username as normalize_username
from .service import public_user

router=APIRouter()


def auth_service(request: Request):
    if not request.app.state.config.auth_enabled:
        raise HTTPException(503,"Authentication is not configured")
    return request.app.state.auth_service


async def require_bridge(request: Request,x_aihub_bridge: str | None=Header(None)):
    service=auth_service(request)
    if not x_aihub_bridge or not secrets.compare_digest(x_aihub_bridge.encode(),service.config.auth_bridge_secret.encode()):
        raise HTTPException(401,"Trusted authentication service required")
    await service.rate_limit("bridge","global",500,60)


def bearer(authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401,"Sign in required",headers={"WWW-Authenticate":"Bearer"})
    return authorization[7:]


async def current_user(request: Request,authorization: str | None=Header(None)):
    return await auth_service(request).authenticate(bearer(authorization))


async def institute_user(user=Depends(current_user)):
    if user.is_demo:
        raise HTTPException(403,"Demo is a UI preview. Sign in with your institute account to access campus knowledge.")
    return user


async def institutional_actor(user=Depends(institute_user)):
    return user.id


def require_roles(*roles):
    async def check(user=Depends(institute_user)):
        if user.role not in roles:
            raise HTTPException(403,"Insufficient role")
        return user
    return check


class StrictBody(BaseModel):
    model_config=ConfigDict(extra="forbid")


class RegisterBody(StrictBody):
    email: str=Field(max_length=320)
    username: str=Field(min_length=3,max_length=40)
    name: str=Field(min_length=1,max_length=200)
    password: str=Field(min_length=12,max_length=128)
    _email=field_validator("email")(institute_email)
    _username=field_validator("username")(normalize_username)

    @field_validator("name")
    @classmethod
    def name_text(cls,value):
        value=value.strip()
        if not value or any(ord(c)<32 for c in value): raise ValueError("Enter a valid name")
        return value


class VerifyBody(StrictBody):
    registration_id: UUID
    code: str=Field(pattern=r"^\d{6}$")
    password: str=Field(min_length=12,max_length=128)


class PasswordBody(StrictBody):
    identifier: str=Field(min_length=1,max_length=320)
    password: str=Field(min_length=1,max_length=128)


class GoogleBody(StrictBody):
    id_token: str=Field(min_length=20,max_length=10000)


class RoleBody(StrictBody):
    role: Literal["Student","Faculty","Staff","Admin"]


@router.post("/v1/auth/register",dependencies=[Depends(require_bridge)])
async def register(data:RegisterBody,request:Request):
    return await auth_service(request).register(data)


@router.post("/v1/auth/verify-email",dependencies=[Depends(require_bridge)])
async def verify(data:VerifyBody,request:Request):
    return await auth_service(request).verify_registration(data)


@router.post("/v1/auth/password",dependencies=[Depends(require_bridge)])
async def password_login(data:PasswordBody,request:Request):
    return await auth_service(request).password_login(data.identifier,data.password)


@router.post("/v1/auth/google",dependencies=[Depends(require_bridge)])
async def google_login(data:GoogleBody,request:Request):
    return await auth_service(request).google_login(data.id_token)


@router.post("/v1/auth/demo",dependencies=[Depends(require_bridge)])
async def demo_login(request:Request):
    return await auth_service(request).demo_login()


@router.get("/v1/auth/me")
async def me(user=Depends(current_user)):
    return public_user(user)


@router.post("/v1/auth/logout")
async def logout(request:Request,authorization: str | None=Header(None)):
    await auth_service(request).logout(bearer(authorization))
    return {"signed_out":True}


@router.get("/v1/admin/access")
async def admin_access(user=Depends(require_roles("Admin"))):
    return {"authorized":True,"role":user.role,"user_id":user.id}


@router.patch("/v1/admin/users/{user_id}/role")
async def change_role(user_id:UUID,data:RoleBody,request:Request,actor=Depends(require_roles("Admin"))):
    return await auth_service(request).set_role(actor,str(user_id),data.role)
