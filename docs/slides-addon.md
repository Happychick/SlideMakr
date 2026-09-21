# Google Slides add-on

SlideMakr ships a Google Slides add-on (`slides_addon/`) that lets someone
edit their deck by voice from inside Google Slides, without leaving the
browser tab.

## How it works

- `Code.gs` adds a "SlideMakr" menu item that opens `Sidebar.html` in a
  sidebar panel.
- The sidebar calls `POST {base}/api/addon-token` (with the user's SlideMakr
  session cookie) to get a short-lived (15 minute) token, then records audio
  with `MediaRecorder` and posts it to `POST {base}/edit-audio` with the
  token in the `X-SlideMakr-Token` header, `audio` as multipart audio, and
  `presentation_id` from `SlidesApp.getActivePresentation().getId()`.
- The backend resolves the token to a `google_id` (see `_resolve_addon_user`
  / `_billing_user_id` in `app/server.py`), runs the same credit-gated
  `/edit-audio` pipeline used by the web app, and returns the transcript +
  result. A 402 response means "out of credits" — the sidebar shows
  "Buy credits at {base}".

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
