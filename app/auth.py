"""
SlideMakr - Google OAuth 2.0 Authentication

Handles user login via Google OAuth, session management, and user context.
Uses authlib for OAuth and Starlette sessions for state.

Routes:
- GET /auth/login    — redirect to Google consent screen
- GET /auth/callback — handle OAuth callback
- GET /auth/logout   — clear session
- GET /auth/me       — return current user info
"""

import os
import logging
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from . import db

logger = logging.getLogger(__name__)

# ============================================================================
# OAUTH SETUP
# ============================================================================

oauth = OAuth()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile https://www.googleapis.com/auth/drive.readonly",
        },
    )
    logger.info("Google OAuth configured")
else:
    logger.warning("Google OAuth not configured — set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")

# ============================================================================
# ROUTER
# ============================================================================

router = APIRouter(prefix="/auth", tags=["auth"])


def get_current_user(request: Request) -> Optional[dict]:
    """Extract current user from session. Returns None if not logged in."""
    return request.session.get("user")


@router.get("/login")
async def login(request: Request):
    """Redirect to Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    # Save the 'next' URL so we can redirect back after OAuth
    next_url = request.query_params.get("next", "/")
    request.session["auth_next"] = next_url

    redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def callback(request: Request):
    """Handle Google OAuth callback."""
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "OAuth not configured"}, status_code=501)

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        logger.error(f"OAuth token exchange failed: {e}")
        return RedirectResponse("/?auth_error=token_exchange_failed")

    userinfo = token.get("userinfo")
    if not userinfo:
        return RedirectResponse("/?auth_error=no_userinfo")

    # Store user in database
    user_data = {
        "google_id": userinfo["sub"],
        "email": userinfo.get("email", ""),
        "name": userinfo.get("name", ""),
        "picture": userinfo.get("picture", ""),
    }
    db.save_user(
        google_id=user_data["google_id"],
        email=user_data["email"],
        name=user_data["name"],
        picture=user_data["picture"],
        refresh_token=token.get("refresh_token", ""),
    )

    # Set session
    request.session["user"] = user_data

    logger.info(f"User logged in: {user_data['email']}")

    # Redirect to saved 'next' URL or home
    next_url = request.session.pop("auth_next", "/")
    return RedirectResponse(next_url)


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to home."""
    user = request.session.get("user")
    if user:
        logger.info(f"User logged out: {user.get('email')}")
    request.session.clear()
    return RedirectResponse("/")


@router.get("/me")
async def me(request: Request):
    """Return current user info or null."""
    user = get_current_user(request)
    if user:
        return JSONResponse({"logged_in": True, **user})
    return JSONResponse({"logged_in": False})
