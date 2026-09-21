# SlideMakr Security and Privacy

What SlideMakr's code actually does today, not what it aspires to do. Every
claim below points to the file that implements it.

## What we access

- Presentations are created and edited through a Google service account by
  default; after you sign in, files are also shared to your own Google
  account (app/slidemakr.py `create_presentation`, `share_with_service_account`;
  app/auth.py).
- Signing in requests these Google OAuth scopes: `openid email profile`,
  Drive, and Presentations (app/auth.py, line ~42). The Drive scope lets
  SlideMakr find, open, duplicate, and share the presentations you pick —
  it is not used to browse your Drive in the background.
- Voice editing streams microphone audio to the backend over a WebSocket
  while a session is open (app/server.py `/ws`).

## What is stored

Firestore collections written by the app (app/db.py), with an in-memory
fallback when no GCP credentials are configured (local dev only):

- `presentations` — presentation id, title, status (`save_presentation`).
- `presentation_metrics` — generation quality metrics (`save_presentation_metrics`).
- `slide_errors` — errors hit while applying slide requests (`record_error`).
- `audio_log` — **transcripts** of voice turns (`transcript_user`,
  `transcript_agent`), not the raw audio itself (`log_audio_interaction`).
  Raw audio bytes are processed in memory for the live request and are not
  written to Firestore.
- `user_memory` — per-user interaction history used to personalize agent
  behavior (`save_user_memory`).
- `users` — Google id, credit balance, and (for signed-in users) a stored
  OAuth refresh token (`save_user`, app/auth.py `callback`).
- `processed_checkout_sessions` — Stripe checkout session ids, used to make
  sure a webhook only grants credits once (`credit_checkout_session_once`).
- `brand_cache` — cached brand lookups by company name (`save_brand_cache`).
- `free_deck_ips` — a salted hash of the IP address that claimed a free
  deck, kept for 24 hours, to limit free decks per client
  (`db.py` `_record_free_deck_ip`, `_free_deck_ip_seen`). We store the hash,
  never the raw IP; the salt lives in the `SLIDEMAKR_IP_SALT` environment
  variable, deployed as the `SLIDEMAKR_IP_SALT` Secret Manager secret
  (`deploy.sh` `--update-secrets`) -- **this secret must be created before
  first deploy** (`gcloud secrets create SLIDEMAKR_IP_SALT --data-file=-`).
  If it is missing in production, `_client_ip_hash` refuses to fall back to
  the public dev-default salt (which would make the stored hashes
  trivially reversible) and disables the IP limit instead, logging an
  error -- it never stores a hash salted with a known value.

**Not yet: automatic deletion or expiry for any collection above** except
`free_deck_ips`, which is only checked within its 24-hour window (old
entries are left in place but are no longer consulted). Everything else
persists until removed by hand.

## Payments

- Purchases go through Stripe Checkout. SlideMakr never receives or stores
  card numbers (app/stripe_billing.py `create_checkout_session`).
- Stripe webhook payloads are signature-verified before anything is
  credited (app/stripe_billing.py `verify_webhook_event`; app/server.py
  `/billing/webhook`). An invalid signature is rejected with 400 and no
  credits are granted.
- Credit grants are idempotent per checkout session — a retried or
  duplicate webhook cannot double-credit an account (app/db.py
  `credit_checkout_session_once`).

## Secrets

- Production API keys and webhook secrets (Gemini, Stripe, OAuth, session
  signing) live in Google Cloud Secret Manager and are injected as
  environment variables at deploy time (deploy.sh `--update-secrets`).
- `*.json` service-account files are gitignored to prevent key leaks.

## Account status

- Google OAuth is currently in **Testing** mode: only test users added in
  the GCP console can sign in. This is a Google Cloud project setting, not
  application code.

## Deletion requests

**Not yet: a self-service delete-my-data flow.** To request deletion of
your account, stored transcripts, or presentation metadata, email
**privacy@slidemakr.app** (placeholder — confirm this inbox exists before
launch) with the email address you signed in with. Presentations already
shared to your own Google Drive are yours and are not affected — SlideMakr
does not delete files from your Drive.
