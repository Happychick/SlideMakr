# SlideMakr Launch Checklist

## Product Gate

- First-shot adherence evals pass for slide-specific instructions.
- Creation speed is measured as time to usable deck, with adherence counted first.
- Stripe credit checkout works in test mode and production mode.
- Voice editing baseline remains stable after billing changes.

## Real Domain and Website

- Real domain points to Cloud Run.
- Cloud Run custom domain and HTTPS are active.
- OAuth redirect URLs include the real domain.
- Stripe Checkout success and cancel URLs use the real domain.
- Public website links to security/privacy, pricing, and support/contact.

## Stripe

- Test mode: use test key, test webhook secret, and Stripe CLI forwarding to `/billing/webhook`.
- Production mode: store live key and webhook secret in Secret Manager.
- Stripe webhook endpoint is configured for Cloud Run.
- Confirm `checkout.session.completed` credits users exactly once.
- Run Stripe go-live review before enabling live keys.

## Security and Trust

- Publish `docs/security-privacy.md` as the practical v1 security page.
- Confirm audio is not stored.
- Confirm Google OAuth scopes match product behavior.
- Document Deletion requests and response process.
- Confirm no Stripe or Google secrets are committed or logged.

## Launch Content

- TikTok: record voice-to-slides in seconds, showing the exact prompt and finished deck.
- LinkedIn: record consultant-facing workflow with first-shot adherence example.
- Blog: "All the ways to make slides with AI, and why voice-first SlideMakr is better."
- Product videos: web creation, voice editing, checkout, Google Slides add-on, PowerPoint add-in when ready.

## Outbound

- Use Clay to identify 5000 consultants and company slide-makers.
- Send short personalized outreach with a demo link.
- Offer one week free.
- Ask for feedback at the end of the week.
- Convert users to credits or team plans.

## Extension Roadmap

- Google Slides add-on first because it reuses the current backend.
- PowerPoint add-in after Google Slides add-on proves the voice workflow.
- Meeting-to-deck workflow after the core plugin loop is reliable.
