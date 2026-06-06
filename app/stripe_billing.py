"""
Stripe Checkout billing for SlideMakr credit packs.

Uses hosted Checkout Sessions with dynamic payment methods. Do not add
`payment_method_types`; Stripe chooses eligible methods from Dashboard config.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from . import db


CREDIT_PACKAGES: Dict[str, Dict[str, Any]] = {
    "credits_10": {"credits": 10, "amount_cents": 499, "name": "10 SlideMakr credits"},
    "credits_50": {"credits": 50, "amount_cents": 1999, "name": "50 SlideMakr credits"},
    "credits_100": {"credits": 100, "amount_cents": 2999, "name": "100 SlideMakr credits"},
}


def build_checkout_session_payload(
    user_id: str,
    package_id: str,
    success_url: str,
    cancel_url: str,
) -> Dict[str, Any]:
    """Build kwargs for `stripe.checkout.Session.create`."""
    if package_id not in CREDIT_PACKAGES:
        raise ValueError(f"Unknown credit package: {package_id}")
    package = CREDIT_PACKAGES[package_id]
    return {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "user_id": user_id,
            "package_id": package_id,
            "credits": str(package["credits"]),
        },
        "line_items": [
            {
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": package["amount_cents"],
                    "product_data": {
                        "name": package["name"],
                    },
                },
            }
        ],
    }


def create_checkout_session(
    user_id: str,
    package_id: str,
    success_url: str,
    cancel_url: str,
) -> Dict[str, Any]:
    """Create a Stripe Checkout Session and return its redirect URL."""
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")

    payload = build_checkout_session_payload(
        user_id=user_id,
        package_id=package_id,
        success_url=success_url,
        cancel_url=cancel_url,
    )
    session = stripe.checkout.Session.create(**payload)
    return {"id": session.id, "url": session.url}


def handle_checkout_completed(session: Dict[str, Any]) -> Dict[str, Any]:
    """Credit a user when Stripe sends `checkout.session.completed`."""
    session_id = session.get("id", "")
    if not session_id:
        return {"status": "ignored", "reason": "missing_session_id"}
    if not db.mark_checkout_session_processed(session_id):
        return {"status": "duplicate", "session_id": session_id}

    metadata = session.get("metadata", {}) or {}
    user_id = metadata.get("user_id", "")
    package_id = metadata.get("package_id", "")
    if not user_id or package_id not in CREDIT_PACKAGES:
        return {"status": "ignored", "reason": "missing_or_invalid_metadata"}

    credits = CREDIT_PACKAGES[package_id]["credits"]
    balance = db.add_user_credits(user_id, credits)
    return {
        "status": "credited",
        "session_id": session_id,
        "user_id": user_id,
        "package_id": package_id,
        "credits_added": credits,
        "balance": balance,
    }


def verify_webhook_event(payload: bytes, signature: str) -> Dict[str, Any]:
    """Verify a Stripe webhook signature and return the event dict."""
    import stripe

    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")
    return stripe.Webhook.construct_event(payload, signature, secret)
