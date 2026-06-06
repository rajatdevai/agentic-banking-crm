"""
JWT Authentication for the RM Copilot gateway.

Implements:
    - create_access_token(): signs a JWT containing rm_id and email
    - verify_token(): validates signature and expiry, returns payload
    - get_current_rm(): FastAPI dependency — validates token, fetches RM from DB
    - authenticate_rm(): login — verifies email + bcrypt password, returns token
    - require_rm_owns_customer(): route dependency — enforces portfolio isolation

Security design:
    - HS256 algorithm with a strong SECRET_KEY (min 32 bytes recommended)
    - Expiry from settings (default 8 hours for a full business day)
    - Passwords hashed with bcrypt at work factor 12
    - Portfolio isolation enforced at the dependency level — no RM can access
      another RM's customers even if they construct a valid request manually.
      This mirrors the PostgreSQL row-level security constraint.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config.settings import get_settings
from shared.db.models import Customer, RelationshipManager
from shared.db.session import get_db
from shared.exceptions.domain import UnauthorizedAccessError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Password hashing — using bcrypt directly (passlib 1.7.x is incompatible
# with bcrypt 4.x due to __about__ attribute removal)
# ---------------------------------------------------------------------------
_BCRYPT_ROUNDS = 12

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(plain_password: str) -> str:
    """Hash a plain-text password with bcrypt at work factor 12."""
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT token operations
# ---------------------------------------------------------------------------

def create_access_token(rm_id: str, email: str) -> str:
    """
    Generate a signed JWT access token for a verified RM.

    Payload claims:
        sub:   rm_id (UUID string) — the subject identifier
        email: RM email address for display purposes
        iat:   issued-at timestamp
        exp:   expiry timestamp
    """
    now = datetime.now(timezone.utc)
    _s = get_settings()
    expire = now + timedelta(minutes=_s.JWT_EXPIRY_MINUTES)

    payload = {
        "sub": str(rm_id),
        "email": email,
        "iat": now,
        "exp": expire,
    }

    token = jwt.encode(
        payload,
        _s.SECRET_KEY,
        algorithm=_s.JWT_ALGORITHM,
    )
    logger.info("access_token_created", rm_id=rm_id, expires_at=expire.isoformat())
    return token


def verify_token(token: str) -> dict:
    """
    Decode and verify a JWT access token.

    Raises:
        HTTPException 401: if token is missing, malformed, or expired.
    """
    try:
        _s = get_settings()
        payload = jwt.decode(
            token,
            _s.SECRET_KEY,
            algorithms=[_s.JWT_ALGORITHM],
        )
        rm_id: str | None = payload.get("sub")
        if rm_id is None:
            raise JWTError("Missing 'sub' claim")
        return payload
    except JWTError as exc:
        logger.warning("jwt_validation_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_rm(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> RelationshipManager:
    """
    FastAPI dependency — validates Bearer token and returns the authenticated RM.

    Returns:
        RelationshipManager ORM object for the authenticated RM.

    Raises:
        HTTP 401: if token is absent, invalid, or expired.
        HTTP 401: if the RM no longer exists or has been deactivated.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide a Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    rm_id: str = payload["sub"]

    result = await db.execute(
        select(RelationshipManager).where(
            RelationshipManager.id == rm_id,
            RelationshipManager.deleted_at.is_(None),
        )
    )
    rm = result.scalar_one_or_none()

    if rm is None:
        logger.warning("rm_not_found_on_token_validation", rm_id=rm_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Relationship Manager account not found or has been deactivated.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not rm.is_active:
        logger.warning("inactive_rm_access_attempt", rm_id=rm_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive. Contact your branch administrator.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return rm


async def require_rm_owns_customer(
    customer_id: str,
    current_rm: RelationshipManager = Depends(get_current_rm),
    db: AsyncSession = Depends(get_db),
) -> Customer:
    """
    Route-level dependency enforcing portfolio isolation.

    Queries the customer record and verifies the authenticated RM's ID matches
    the customer's assigned rm_id. If not, raises HTTP 403.

    This is the application-layer enforcement mirror of PostgreSQL row-level
    security — both must be in place. Defence in depth.

    Usage:
        @router.get("/{customer_id}")
        async def get_customer(customer: Customer = Depends(require_rm_owns_customer)):
            ...
    """
    result = await db.execute(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
        )
    )
    customer = result.scalar_one_or_none()

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer {customer_id} not found.",
        )

    if str(customer.rm_id) != str(current_rm.id):
        logger.warning(
            "unauthorized_customer_access",
            requesting_rm_id=str(current_rm.id),
            customer_rm_id=str(customer.rm_id),
            customer_id=customer_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this customer's data.",
        )

    return customer


# ---------------------------------------------------------------------------
# Login (authentication)
# ---------------------------------------------------------------------------

async def authenticate_rm(
    email: str,
    password: str,
    db: AsyncSession,
) -> str:
    """
    Authenticate an RM by email + password.

    Returns:
        JWT access token string on success.

    Raises:
        HTTP 401: if email not found or password doesn't match.

    Note: We use a constant-time comparison (bcrypt verify) and return the
    same generic error for both "email not found" and "wrong password" to
    prevent user enumeration attacks.
    """
    result = await db.execute(
        select(RelationshipManager).where(
            RelationshipManager.email == email,
            RelationshipManager.deleted_at.is_(None),
            RelationshipManager.is_active.is_(True),
        )
    )
    rm = result.scalar_one_or_none()

    _INVALID_CREDENTIALS = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if rm is None:
        # Still run a dummy verify to prevent timing attacks
        dummy_hash = "$2b$12$dummyhashdummyhashdummyhashdummyhashdu"
        try:
            bcrypt.checkpw(b"dummy", dummy_hash.encode("utf-8"))
        except Exception:
            pass
        raise _INVALID_CREDENTIALS

    if not verify_password(password, rm.hashed_password):
        logger.warning("failed_login_attempt", email=email)
        raise _INVALID_CREDENTIALS

    token = create_access_token(rm_id=str(rm.id), email=rm.email)
    logger.info("rm_login_success", rm_id=str(rm.id))
    return token
