# SlideMakr Security and Privacy

SlideMakr creates and edits Google Slides presentations from text and voice. This page is the practical v1 trust posture for beta customers, consultants, and teams making corporate presentations.

## Data Use

- Audio is processed for the current request and is not stored by SlideMakr.
- Transcripts and prompts are used to create or edit the requested presentation.
- Generated presentation metadata and quality metrics may be stored to operate the product, debug failures, and improve instruction adherence.
- Corporate presentations are accessed only to complete the user-requested creation, editing, sharing, or Drive operation.

## Google Access

SlideMakr uses Google OAuth scopes for identity, Drive, and Presentations:

- `openid email profile`
- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/presentations`

The Drive and Presentations scopes let SlideMakr find, open, duplicate, share, and edit presentations the user chooses. The product should keep the visible OAuth consent copy aligned with these capabilities.

## Retention

- Audio is not stored.
- Presentation links, prompt summaries, errors, and generation metrics may be stored for product operation.
- Google refresh tokens are stored only for signed-in users who authorize Drive/Slides access.
- Stripe payment details are handled by Stripe Checkout; SlideMakr does not store card numbers.

## Deletion Requests

Deletion requests should remove user records, stored tokens, presentation metadata, saved metrics, and user-memory records associated with the requester. Google Slides files remain in the user's Google Drive unless the user deletes them there.

## Secrets and Payments

- Production secrets live in Google Cloud Secret Manager.
- Stripe uses hosted Checkout and webhook signature verification.
- Stripe secret keys or restricted keys must never be committed, logged, or exposed to browser code.
- Use separate test and live Stripe keys; prefer restricted API keys where possible.

## Enterprise FAQ

**Will SlideMakr train on my corporate presentations?**  
No v1 product behavior should claim or rely on retaining corporate slide content for model training. The agent reacts to the prompt and current presentation state to complete the requested task.

**Can SlideMakr see my whole Drive?**  
The app has Drive scope after OAuth so it can search and open presentations, but the intended UX is user-directed: find, open, duplicate, or edit the presentation the user selects.

**What should companies review first?**  
OAuth scopes, token storage, data retention, deletion process, Stripe payment handling, and whether the beta terms match their internal policy.
