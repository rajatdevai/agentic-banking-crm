"""
Authentication router — login and token endpoints.
"""

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from services.gateway.middleware.auth import authenticate_rm
from services.gateway.schemas.outreach import LoginRequest, TokenResponse
from shared.config.settings import get_settings
from shared.db.session import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="RM login — returns a JWT access token",
    description=(
        "Authenticate with email and password. "
        "Returns a Bearer token valid for the configured session duration. "
        "Include this token in Authorization: Bearer <token> on all subsequent requests."
    ),
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    token = await authenticate_rm(
        email=body.email,
        password=body.password,
        db=db,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=get_settings().JWT_EXPIRY_MINUTES,
    )
