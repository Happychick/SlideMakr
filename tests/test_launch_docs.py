from pathlib import Path


def test_security_privacy_doc_covers_enterprise_trust_basics():
    text = Path("docs/security-privacy.md").read_text()

    for phrase in [
        "Audio is processed for the current request",
        "not stored",
        "Google OAuth scopes",
        "Deletion requests",
        "Stripe",
        "Secret Manager",
        "corporate presentations",
    ]:
        assert phrase in text


def test_launch_checklist_covers_domain_and_stripe_webhook():
    text = Path("LAUNCH.md").read_text()

    for phrase in [
        "Real domain",
        "Cloud Run",
        "Stripe webhook",
        "TikTok",
        "LinkedIn",
        "Clay",
        "5000",
    ]:
        assert phrase in text
