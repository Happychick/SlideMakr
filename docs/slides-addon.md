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
window/tab counts; an iframe `fetch` does not). Two earlier attempts at
this handoff turned out to be broken or insufficient once actually
verified — see "Why a state nonce alone wasn't enough" below — so the
current design is:

1. The sidebar generates a random `state` nonce (`crypto.getRandomValues`,
   32 lowercase hex chars) per connect attempt, keeps it only in memory, and
   opens `BASE + '/addon/connect?state=...'` as a top-level popup — the
   session cookie *is* sent on that navigation.
2. `GET /addon/connect` (in `app/server.py`) mints nothing. It requires a
   signed-in session (redirects to `/auth/login?next=/addon/connect`
   otherwise) and the `state` param (400 if missing or not exactly 32 hex
   chars), then renders an explicit **consent screen**: "Connect the
   SlideMakr add-on to your account `<email>`?" with a "Connect" button and
   a "Cancel" button. The button submits a same-origin form carrying a
   random CSRF token (`addon_csrf`) the server stashed in the session when
   rendering this page, plus the `state`.
3. `POST /addon/connect` requires the session again, and that the posted
   `csrf` field matches `session['addon_csrf']` exactly (403 otherwise, and
   the CSRF token is cleared after one use). Only once that's verified does
   it call `db.issue_addon_token(google_id, state)` and render the tiny
   handoff page whose script does `window.opener.postMessage({type:
   'slidemakr-token', token, state}, '*')` then `window.close()` — with
   "Connected. You can close this window." as the visible fallback if
   `window.close()` is blocked. The token/state values are embedded as
   proper JS string literals (`json.dumps(value).replace('<',
   '\u003c').replace('/', '\/')` — see `_js_string_literal` in
   `app/server.py`), **not** `html.escape`d — escaping `"` to `&quot;` is
   correct for an HTML attribute but is invalid inside a `<script>` block
   and silently breaks the handoff with a `SyntaxError`.
4. The sidebar's `message` listener checks `event.origin === backendUrl`
   *and* `event.data.state === expectedConnectState` before doing anything,
   then calls `POST /api/addon-token/claim` with `{token, state}`. Only a
   successful claim (`db.claim_addon_token`, single-use, state-matching)
   marks the token `claimed: true`; the sidebar then hands it to
   `google.script.run.saveToken(token)` in `Code.gs`, which persists it in
   `PropertiesService.getUserProperties()`.
5. `db.resolve_addon_token` — the function everything else, including
   `/edit-audio`'s billing identity, goes through — refuses any token that
   is not `claimed: true`. An issued-but-never-claimed token is inert.
6. Every `/edit-audio` call from the sidebar then sends the claimed token
   in the `X-SlideMakr-Token` header — no cookie involved at all.

### Why a state nonce alone wasn't enough

Two things were true of an earlier version of this flow that made the
`state` nonce alone a no-op, both confirmed by execution, not just review:

- **`GET /addon/connect` minted a token immediately.** Since the attacker
  fully controls the popup URL they open (`window.open(BASE +
  '/addon/connect?state=<anything>')`), they also control the `state` value
  — there's nothing about it the legitimate sidebar has that an attacker
  can't just supply themselves. A `state` check on the *minting* step
  doesn't distinguish "the real sidebar opened this" from "a malicious page
  opened this and picked its own state." The fix is the explicit
  consent-click: nothing is minted until a signed-in human clicks
  "Connect" on a page that names the account, and that click is bound to a
  same-origin CSRF token an attacker's page cannot forge (session-stored,
  never sent to another origin).
- **`resolve_addon_token` didn't check `claimed`.** Even with `claim`
  wired up, the token itself was fully functional at `/edit-audio` the
  moment it was issued — `claim` was decorative. Fixed by making
  `claimed: true` a hard requirement in `resolve_addon_token`, so an
  unclaimed token (however it was obtained) authenticates nobody.

The state nonce and `claim` step still matter for what's left: they keep a
`postMessage('*')` broadcast from being usable by an unrelated listener
that happens to be running in the same opener window, and they keep the
token single-use once claimed. What they were never going to do alone is
substitute for actual user consent — that's the job of the consent screen
and the CSRF-protected POST.

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
