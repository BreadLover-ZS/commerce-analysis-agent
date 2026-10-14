import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, text

from app.storage.models import AuthSession, LoginAttempt, User, utcnow

COOKIE = "commerce_session"


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        _, rounds, salt, expected = encoded.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def check_origin(request: Request):
    origin = request.headers.get("origin")
    # Requests through the same-origin reverse proxy are accepted too.
    same_origin = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin not in [*request.app.state.settings.allowed_origins, same_origin]:
        raise HTTPException(403, "请求来源不允许")


def authenticate(request: Request) -> User:
    settings = request.app.state.settings
    token = request.cookies.get(COOKIE, "")
    with request.app.state.repo.session() as db:
        login = db.get(AuthSession, token_hash(token))
        if not login or login.expires_at <= utcnow():
            raise HTTPException(401, "请先登录")
        user = db.get(User, login.user_id)
        if not user:
            raise HTTPException(401, "账户不存在")
        db.expunge(user)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        actual = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(
            actual, csrf_token(token, settings.session_secret.get_secret_value())
        ):
            raise HTTPException(403, "CSRF 校验失败，请刷新页面")
    return user


def login(repo, settings, email: str, password: str, client: str) -> tuple[User, str]:
    email = email.strip().lower()
    # Both the account and client are limited; unknown accounts do not disclose existence.
    keys = [token_hash(f"account:{email}"), token_hash(f"client:{client}")]
    with repo.session() as db:
        if repo.engine.dialect.name == "postgresql":
            # Lock rate-limit buckets before creating/incrementing them.
            for key in sorted(keys):
                lock_id = int(key[:16], 16)
                if lock_id >= 2**63:
                    lock_id -= 2**64
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
        attempts = []
        for key in keys:
            attempt = db.get(LoginAttempt, key)
            if not attempt:
                attempt = LoginAttempt(key=key, failures=0, window_start=utcnow())
                db.add(attempt)
            if attempt.window_start < utcnow() - timedelta(minutes=10):
                attempt.window_start, attempt.failures = utcnow(), 0
            if attempt.failures >= 10:
                raise HTTPException(429, "尝试过于频繁，请稍后重试")
            attempts.append(attempt)
        user = db.scalar(select(User).where(User.email == email))
        # A dummy KDF prevents a cheap timing-based account enumeration.
        encoded = user.password_hash if user else repo.dummy_password_hash
        if not password_matches(password, encoded) or not user:
            for attempt in attempts:
                attempt.failures += 1
            db.commit()
            raise HTTPException(401, "邮箱或密码错误")
        for attempt in attempts:
            attempt.failures = 0
        token = secrets.token_urlsafe(32)
        db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        db.add(
            AuthSession(
                token_hash=token_hash(token),
                user_id=user.id,
                expires_at=utcnow() + timedelta(hours=settings.session_hours),
            )
        )
        db.commit()
        db.expunge(user)
        return user, token


def bootstrap_user(repo, settings):
    with repo.session() as db:
        if not db.scalar(select(User).where(User.email == settings.bootstrap_email.lower())):
            db.add(
                User(
                    id=str(uuid4()),
                    tenant_id="demo" if settings.seed_demo else "primary",
                    email=settings.bootstrap_email.lower(),
                    password_hash=password_hash(settings.bootstrap_password.get_secret_value()),
                )
            )
        if settings.seed_demo and not db.scalar(
            select(User).where(User.email == "other@example.com")
        ):
            db.add(
                User(
                    id=str(uuid4()),
                    tenant_id="other",
                    email="other@example.com",
                    password_hash=password_hash("other-commerce-2026"),
                )
            )
        db.commit()
