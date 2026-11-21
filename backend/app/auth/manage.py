"""Local deployment-operator commands; never exposed as a browser privilege grant."""
import argparse
import asyncio
from sqlalchemy import select, update, delete
from ..db import Session, engine
from ..models import User, AuthSession, AuthAudit, PendingRegistration, GoogleTokenUse, AuthRateLimit, now
from .security import institute_email


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    grant=commands.add_parser("grant-admin")
    grant.add_argument("email")
    commands.add_parser("cleanup-expired")
    args=parser.parse_args()
    try:
        async with Session() as session:
            if args.command=="grant-admin":
                email=institute_email(args.email)
                user=await session.scalar(select(User).where(User.email==email).with_for_update())
                if not user or not user.email_verified or user.is_demo or not user.is_active:
                    raise SystemExit("User must first verify an active institute account")
                previous=user.role
                user.role="Admin"
                await session.execute(update(AuthSession).where(AuthSession.user_id==user.id).values(revoked_at=now()))
                session.add(AuthAudit(subject_id=user.id,event="operator_admin_grant",details={"from":previous,"to":"Admin"}))
                await session.commit()
                print("Admin role granted. Existing sessions revoked; sign in again.")
            else:
                for model in (AuthSession,PendingRegistration,GoogleTokenUse,AuthRateLimit):
                    await session.execute(delete(model).where(model.expires_at<now()))
                await session.commit()
                print("Expired authentication records removed.")
    finally:
        await engine.dispose()


if __name__=="__main__": asyncio.run(main())
