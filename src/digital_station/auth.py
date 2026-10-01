import hashlib
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import cast

from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import Role, User
from .db import AppUser, AuthSession

PASSWORDS = PasswordHash.recommended()
COOKIE_NAME = "station_session"
SESSION_SECONDS = 12 * 3600


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(user: AppUser) -> User:
    # PostgreSQL CHECK constrains this string to the same public Role enum.
    return User(
        id=user.id, display_name=user.display_name, role=cast(Role, user.role), resource_ids=user.resource_ids
    )


async def seed_users(session: AsyncSession, settings):
    for role, name in (
        ("viewer", "Наблюдатель"),
        ("operator", "Исполнитель"),
        ("dispatcher", "Диспетчер"),
        ("admin", "Администратор"),
    ):
        password = getattr(settings, f"demo_{role}_password").get_secret_value()
        user = await session.get(AppUser, f"u-{role}")
        if user is None:
            session.add(
                AppUser(
                    id=f"u-{role}",
                    username=role,
                    display_name=name,
                    role=role,
                    active=True,
                    resource_ids=["I1"] if role == "operator" else [],
                    password_hash=PASSWORDS.hash(password),
                )
            )
        elif not PASSWORDS.verify(password, user.password_hash):
            user.password_hash = PASSWORDS.hash(password)
            await session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))


async def create_session(session: AsyncSession, user: AppUser) -> str:
    token = token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS),
            revoked_at=None,
        )
    )
    await session.commit()
    return token


async def find_session(session: AsyncSession, token: str):
    return await session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
