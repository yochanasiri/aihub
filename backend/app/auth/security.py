import asyncio
import hashlib
import hmac
import re
from datetime import timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from email_validator import validate_email, EmailNotValidError

hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY_HASH = hasher.hash("not-a-real-user-password-" + "x"*32)
password_slots = asyncio.Semaphore(4)


def institute_email(value: str) -> str:
    value = value.strip()
    if not value.isascii() or value.count("@") != 1 or value.rsplit("@",1)[1].lower() != "iiti.ac.in":
        raise ValueError("Use your exact @iiti.ac.in institute email")
    try:
        result = validate_email(value,check_deliverability=False,allow_smtputf8=False)
    except EmailNotValidError:
        raise ValueError("Enter a valid institute email")
    return result.normalized.lower()


def username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,39}",value):
        raise ValueError("Username must be 3–40 characters: letters, numbers or underscores")
    return value


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def keyed_hash(secret: str, value: str) -> str:
    return hmac.new(secret.encode(),value.encode(),hashlib.sha256).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def hash_password(value: str):
    async with password_slots:
        return await asyncio.to_thread(hasher.hash,value)


async def verify_password(encoded: str | None, value: str):
    def check():
        try:
            valid = hasher.verify(encoded or DUMMY_HASH,value)
            return bool(encoded) and valid
        except (VerificationError,InvalidHashError):
            return False
    async with password_slots:
        return await asyncio.to_thread(check)
