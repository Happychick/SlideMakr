# Google Slides add-on

SlideMakr ships a Google Slides add-on (`slides_addon/`) that lets someone
edit their deck by voice from inside Google Slides, without leaving the
browser tab.

## How it works

- `Code.gs` adds a "SlideMakr" menu item that opens `Sidebar.html` in a
  sidebar panel.
- The sidebar records audio with `MediaRecorder` and posts it to
  `POST {base}/edit-audio` with an add-on token in the `X-SlideMakr-Token`
  header, `audio` as multipart audio, and `presentation_id` from
  `SlidesApp.getActivePresentation().getId()`.
- The backend resolves the token to a `google_id` (see the single resolver
  `_resolve_addon_user`, called from `_billing_user_id`, in
  `app/server.py`), runs the same credit-gated `/edit-audio` pipeline used
  by the web app, and returns the transcript + result. A 402 response means
  "out of credits" — the sidebar shows "Buy credits at {base}". A 401 means
  the token expired — the sidebar re-shows the connect prompt.

### Getting the token into the sidebar: the popup handoff

The sidebar itself runs inside a `googleusercontent.com` iframe that Apps
Script serves — an origin completely different from the SlideMakr backend.
Our session cookie is set `SameSite=Lax` (deliberately: loosening it to
`None` would open the backend up to cross-site request forgery), and a
`SameSite=Lax` cookie is **not** sent on a cross-origin `fetch()` — including
one made with `credentials: 'include'` from inside that iframe. So a direct
`fetch(BASE + '/api/addon-token', {credentials: 'include'})` from the
sidebar always 401s, even when the user is signed in, in a normal browser
tab, right next to the sidebar.

The fix is a **popup handoff**, which relies on a `SameSite=Lax` cookie
being sent on a *top-level navigation* (opening a URL in a new
window/tab counts; an iframe `fetch` does not):

1. The sidebar shows a "Connect SlideMakr" button. Clicking it
   `window.open(BASE + '/addon/connect', ...)` — a real top-level
   navigation, so the session cookie *is* sent.
2. `GET /addon/connect` (in `app/server.py`) requires a signed-in session;
   if there isn't one it redirects to `/auth/login?next=/addon/connect` so
   the user signs in first and lands back on the same page. Once signed in,
   it calls `db.issue_addon_token(google_id)` and renders a tiny page whose
   script does `window.opener.postMessage({type: 'slidemakr-token', token},
   '*')` then `window.close()` — with "Connected. You can close this
   window." as the visible fallback if `window.close()` is blocked.
3. The sidebar listens for that `message` event, checks
   `event.origin === backendUrl` before trusting it, and hands the token to
   `google.script.run.saveToken(token)` in `Code.gs`, which persists it in
   `PropertiesService.getUserProperties()` (scoped to that user + script, so
   it survives closing and reopening the sidebar). `getAddonConfig()` reads
   it back on load via `getToken()`.
4. Every `/edit-audio` call from the sidebar then sends the saved token in
   the `X-SlideMakr-Token` header — no cookie involved at all.

## One base URL

`slides_addon/Code.gs` defines a single constant:

```js
const SLIDEMAKR_BASE_URL = 'https://slidemakr-72711045873.us-central1.run.app';
```

`Sidebar.html` never hardcodes a host — it receives `backendUrl` from
`Code.gs` via `getAddonConfig()`. To point the add-on at a different backend
(e.g. a custom domain like `slidemakr.com` once DNS is live, or a staging
Cloud Run revision):

1. Edit `SLIDEMAKR_BASE_URL` in `slides_addon/Code.gs`.
2. If you're also moving the marketing/app static assets, update the
   `logoUrl` in `slides_addon/appsscript.json` (it's a static manifest field
   and can't reference the JS constant).
3. `clasp push` (below) to redeploy.

## Local setup with `clasp`

1. Install clasp: `npm install -g @google/clasp`
2. `clasp login` — authorize with the Google account that owns the Apps
   Script project (create one at https://script.google.com if you don't
   have one yet, or `clasp create --type standalone --title SlideMakr` from
   inside `slides_addon/`).
3. From `slides_addon/`, `clasp push` to upload `Code.gs`, `Sidebar.html`,
   and `appsscript.json` to the Apps Script project.
4. In the Apps Script editor (`clasp open`), go to **Deploy > Test
   deployments** and select **Install add-on**. This installs it only for
   your own Google account, against your current script version — no
   Marketplace review needed for local testing.

## Adding a test user

While the add-on is unpublished, only test users you explicitly add can
install it:

1. In the Apps Script project, **Deploy > Test deployments**.
2. Under **Google Workspace Marketplace SDK** (Google Cloud Console project
   linked to the script — `clasp open --creds` or the "Project Settings"
   gear icon gives you the GCP project id), open **APIs & Services > OAuth
   consent screen**.
3. While the app is in **Testing** publishing status, add each tester's
   Google account email under **Test users**. Only those accounts can grant
   OAuth consent and open the sidebar.

## Private Marketplace listing (internal / invite-only distribution)

> The Apps Script project must be explicitly linked to the same GCP project
> that hosts the Marketplace SDK configuration below (Apps Script project
> **Project Settings > Google Cloud Platform (GCP) Project** — switch it from
> the default script-bound project to that GCP project id). Otherwise the
> Marketplace SDK can't find the deployment to list.

To distribute beyond ad-hoc test installs without a public Marketplace
review:

1. In Google Cloud Console (the project backing the Apps Script deployment),
   enable the **Google Workspace Marketplace SDK** API.
2. Configure the SDK's **App Configuration** — for a Slides sidebar add-on,
   point it at the same Apps Script deployment ID (`clasp deployments` to
   list, `clasp deploy` to create one) as the Slides "editor add-on".
3. Under **Store Listing**, fill in the required name/description/icons
   (reuse the assets in `slides_addon/appsscript.json`'s `logoUrl`).
4. Under **Visibility**, choose the appropriate option:
   - **Private / unlisted** — installable only via a direct link you share,
     no public search visibility, no Google review queue.
   - **Domain-only** — restrict to a specific Google Workspace domain, set
     by a Workspace admin in the Admin console (Apps > Google Workspace
     Marketplace apps).
5. Publish. Anyone with the install link (or the domain admin's allowlist)
   can now install the add-on directly, without waiting on the full public
   Marketplace review that a public listing requires.

## Token lifecycle

- Tokens are minted by `POST /api/addon-token` (requires a signed-in
  session; 401 otherwise) and stored in the Firestore collection
  `addon_tokens` (`db.issue_addon_token`), doc id = token, fields `user_id`
  and `expires_at` (15 minutes out). Falls back to an in-process dict when
  Firestore isn't configured (local dev).
- `db.resolve_addon_token(token)` returns the `user_id` for a live token, or
  `None` if it's missing or expired.
- `db.purge_expired_addon_tokens()` runs on every issue to keep the
  collection from growing unbounded — no separate cron job needed at this
  volume.
