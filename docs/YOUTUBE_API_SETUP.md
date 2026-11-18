# YouTube Data API — setup and compliance audit, step by step

**Why this matters:** any Google Cloud project created after 28 July 2020 that uploads
through the API gets every video **locked to private** until the project passes YouTube's
API compliance audit. The lock can't be undone from YouTube Studio. So the audit is not
paperwork for later — it decides whether BeatPub can publish at all.

**The order that works** (the audit form wants proof of a *working* upload, so you can't
submit it before the uploader exists):

| When | What | Time |
|---|---|---|
| Tonight | Part A — Google Cloud project, API, OAuth client | 30 min |
| Tonight | Part B — publish the privacy policy + terms pages on your site | 20 min |
| After Claude Code builds `beatpub upload` | Part C — one private test upload, screenshots, 2-minute screencast | 30 min |
| Same day | Part D — submit the form with the answers below | 20 min |
| Then | Part E — wait, answer follow-up emails, flip the config when approved | days–weeks |

Use the **Google account that owns the CapryBeats channel** for everything below. If the
channel is a Brand Account, the sign-in step will ask which channel — pick it.

---

## Part A — Google Cloud project (tonight)

Google moves menu names around; if a label differs, look for the same idea.

1. **Create the project.** Go to https://console.cloud.google.com → project picker (top
   left) → *New project* → name `BeatPub` → *Create*. Select it.
2. **Note the Project number.** Dashboard → *Welcome / Project info* card shows **Project
   number** (digits only, not the Project ID). Write it here: `__86460671569__________`. The form
   needs it.
3. **Enable the API.** *APIs & Services → Library* → search `YouTube Data API v3` → *Enable*.
4. **OAuth consent screen** (now called *Google Auth Platform* — *APIs & Services → OAuth
   consent screen* redirects there). Click *Get started* and fill:
   - **Branding:** App name `BeatPub` (must **not** contain the word "YouTube"), user
     support email = your Gmail, app logo optional. *App domain:* home page
     `https://<your-site>/beatpub`, privacy policy `https://<your-site>/beatpub-privacy`,
     terms `https://<your-site>/beatpub-terms` (Part B creates these), *Authorized
     domain* = your site's domain. Developer contact = your Gmail.
   - **Audience:** *External*.
   - **Data Access → Add or remove scopes:** add
     `https://www.googleapis.com/auth/youtube` (listed as "Manage your YouTube account").
     Save.
5. **Publish the app.** *Audience* → publishing status *Testing* → *Publish app* →
   confirm. Reason: an app left in *Testing* gets refresh tokens that expire every 7
   days, so you'd re-login weekly. In *Production* the token lasts. You'll see "Google
   hasn't verified this app" on first login — that's expected for a personal app: click
   *Advanced → Go to BeatPub (unsafe)*. (If you'd rather stay in Testing for now, add
   your own Gmail under *Test users*.)
6. **Create the OAuth client.** *Clients → Create client* → application type **Desktop
   app** → name `BeatPub desktop` → *Create* → **Download JSON** → save it as
   `client_secret.json` in the BeatPub project folder (it's gitignored — never share it).
7. Done for the Cloud side. `beatpub auth` (once Claude Code builds it) opens the browser,
   you approve, and `token.json` is written next to `client_secret.json`.

---

## Part B — Privacy policy, terms and a tool page on your site (tonight)

The form requires a **publicly reachable privacy policy URL**, a terms-of-service
document, and a screenshot of the tool's "home page" showing where the privacy link is,
with the YouTube connection visible. Easiest with your Webflow site:

1. Create page `/beatpub-privacy` with the text from `docs/privacy-policy.md`.
2. Create page `/beatpub-terms` with the text from `docs/terms.md`.
3. Create page `/beatpub` — the "primary access URL". A few lines are enough:
   *"BeatPub is a personal command-line tool that publishes CapryBeats instrumentals to
   my YouTube channel. BeatPub uses YouTube API Services. Source code: <GitHub link>."*
   Then two visible links: **Privacy Policy** and **Terms of Service**. (If you want the
   YouTube icon next to that line, take it from Google's official branding page —
   https://developers.google.com/youtube/terms/branding-guidelines — and follow its
   rules; plain text is also fine.)
4. Replace every `<your-site>` and `<your email>` placeholder in the two documents
   before publishing.

No Webflow mood? A public GitHub repo with `README.md`, `PRIVACY.md` and `TERMS.md`
works too; use the repo URL as the primary access URL and the raw page URLs for the
policies. The site version looks more credible to a reviewer.

---

## Part C — Evidence (after the uploader exists)

Everything must be **PNG/JPEG (PDF allowed for the terms), at least 1280×720, sharp,
with the browser address bar visible on web screenshots, one file per upload field,
under 10 MB, descriptive filenames.**

| # | File | How to get it |
|---|---|---|
| 1 | `privacy-policy-page.png` | Full-page screenshot of `/beatpub-privacy`, address bar visible |
| 2 | `homepage-privacy-link.png` | Screenshot of `/beatpub` showing the Privacy Policy link and the "uses YouTube API Services" line |
| 3 | `terms-of-service.pdf` | Print `/beatpub-terms` to PDF |
| 4 | `oauth-consent-screen.png` | Run `beatpub auth`; screenshot the Google screen that says *BeatPub wants access to your Google Account — Manage your YouTube account* |
| 5 | `oauth-revocation.png` | https://myaccount.google.com/permissions → BeatPub → shows *Remove access* |
| 6 | `upload-interface.png` | Terminal after a real `beatpub upload` of a test beat (private): the progress bar, the video ID and URL |
| 7 | `youtube-studio-result.png` | YouTube Studio → Content, showing that private video with its custom thumbnail |
| 8 | `architecture-diagram.png` | Ready: `docs/evidence/architecture-diagram.png` |
| 9 | `user-flow-diagram.png` | Ready: `docs/evidence/user-flow-diagram.png` |
| 10 | Screencast link | Record 1–2 min with OBS / Xbox Game Bar (Win+G) / QuickTime: terminal → `beatpub run <slug> --yes` → upload finishes → open the video in YouTube Studio. Upload the recording to YouTube as **unlisted** (or Google Drive, link set to "anyone with the link") and paste the link in the form |
| 11 | Source link | Public GitHub repo URL (or a zip of the project exported to PDF via "print" if you want to keep the repo private) — the form wants "a complete, working upload script, not a snippet" |

Do item 6 and 7 with a throwaway test beat; the video stays private and you can delete
it afterwards.

---

## Part D — The form, field by field

Form: **https://support.google.com/youtube/contact/yt_api_form** — sign in first with the
channel's Google account. Copy the answers below; replace the `<...>` placeholders.

### Section 1 — Request type
- **Select the reason:** *Complete a compliance audit to request for additional quota.*
  (It's the only option that starts a first audit. You'll ask for *no* extra quota in
  Section 5.)

### Section 2 — Organization and contact
- **Are you applying:** *As an individual user* (the channel and the beats are yours
  personally, not the s.r.o.'s — keep it simple).
- **Your Full Legal Name:** Bruno Betko
- **Your Organization's Legal Name:** self
- **Parent Company Name:** self
- **Primary Website:** `https://<your-site>`
- **Country:** Other (please specify) → Slovakia · **Street / City / State / Postal:** your
  home address · State/Province: the kraj (e.g. "Bratislavský kraj")
- **Category:** Media and Entertainment
- **Organization Size / Type:** Independent Developer/Sole Proprietor
- **Primary Contact:** Bruno Betko, `<your Gmail>` · **Technical contact:** Same as
  Primary · **Business contact:** Same as Primary

### Section 3 — Business model and Google contacts
- **Describe your organization's work as it relates to YouTube** (paste):

  > I am an independent music producer working under the name CapryBeats. I make
  > instrumental beats and publish them on my own YouTube channel as "type beat" videos
  > (a still cover image with the audio) so that artists can find and license them.
  >
  > BeatPub is a small command-line tool I wrote for my own use only. For each new beat
  > it renders the 1080p video and a short vertical preview (a YouTube Short) locally
  > with ffmpeg, drafts the title, description and tags from my templates, uploads both
  > to my own channel through the YouTube Data API, sets the custom thumbnail and adds
  > the video to one of my playlists. It replaces a manual process (video-editor export
  > plus the manual upload form) that takes 15–20 minutes per beat.
  >
  > The tool is not distributed, sold or used by anyone else: one user (me), one channel
  > (mine). It reads only my own channel's video metadata (titles, descriptions, tags of
  > my existing uploads, so new uploads stay consistent) and writes only to my own
  > channel. It collects no data about other users, stores nothing except my own OAuth
  > token, my own channel's metadata and a local log of my uploads, and shares nothing
  > with third parties. Expected volume is two to six uploads per day at most (a video and its Short per beat).

- **Target audience:** Internal Users; Other → "Myself only — a single-user personal tool."
- **How does your API Client monetize:** Free service (we do not charge users); Other →
  "The tool itself is not monetised. The beats it uploads are licensed to artists
  through my beat store, which is separate from this tool."
- **Ads on/within YouTube content:** Not applicable · **Prior approval:** (leave)
- **Google Partner Manager:** No, I do not have a Google representative
- **How did you first learn about the API:** Google Developer Documentation
- **Content Owner IDs / Google Ads Customer IDs:** leave empty

### Section 4 — API client overview
- **API Client Name:** BeatPub
- **Contains the word "YouTube"?** No
- **Primary Access URL:** `https://<your-site>/beatpub`
- **Privacy Policy URL:** `https://<your-site>/beatpub-privacy`
- **Terms of Service URL:** `https://<your-site>/beatpub-terms`
- **Is your API Client publicly accessible?** No
- **Demo Account Credentials:** leave empty (there is no login)
- **Login URL:** leave empty
- **Special Instructions for Access:**

  > BeatPub is a command-line tool that runs on my own computer; there is no hosted
  > instance and no login system. The complete source code is at <GitHub URL>, and an
  > end-to-end screencast of the upload flow (command → upload → video visible in
  > YouTube Studio) is linked under Other Supporting Materials.

- **Acknowledgment (demo credentials waiver):** tick it.

### Section 5 — Use cases and quota
- **How many project numbers:** 1
- **Google Cloud Project Number:** the digits from Part A step 2
- **Use Case Category:** *Video Uploading & Account Management*; and *Others* →
  "Personal single-user tool that uploads the developer's own music videos to the
  developer's own channel."
- **Requires users to sign in with Google (OAuth 2.0)?** Yes
- **Derived metrics / data storage agreement:** leave **unticked** (not needed; the tool
  doesn't compute metrics)
- **Expected API Usage Volume:** Fewer than 1,000 requests per day
- **Required evidence uploads:** files 1, 2, 3 from Part C
- **Conditional evidence:** (a) OAuth flow → files 4 + 5 (combine into one image if the
  field takes one file); (b) Upload interface → files 6 + 7
- **Endpoints you plan to use:** tick
  `youtube.videos.insert`, `youtube.thumbnails.set`, `youtube.playlistItems.insert`,
  `youtube.playlists.list`, `youtube.playlistItems.list`, `youtube.channels.list`,
  `youtube.videos.list`, `youtube.videos.update`
- **Total quota you are requesting:** *No change / Default quota (10k quota points)*
- If it still asks for per-endpoint numbers and justification for `videos.insert`:
  *Total per day:* 10 · *Justification:* "Default allocation is sufficient. I upload
  one to three of my own beats per day, each as a video plus a Short, never more than ten."

### Section 6 — Additional evidence
- **Architecture Diagram:** `docs/evidence/architecture-diagram.png`
- **User Flow Diagrams:** `docs/evidence/user-flow-diagram.png`
- **Other Supporting Materials:** the screencast link (file 10) and the source link
  (file 11). If the field only takes a file, upload a screenshot that shows both links
  and paste the links into Special Instructions in Section 4 as well.

### Section 7 — Attestations
Tick every box (YouTube API Services Terms of Service, Google Privacy Policy, Developer
Policies, termination understanding, demo waiver, accuracy, data-usage and recording
consent). The Developer Policies one commits you to telling YouTube in writing before
you change the stated use case — so when the clipping pipeline later uses the same
project, send a short note to the case email first (or make it a second project with its
own form).

Submit. Then **open the confirmation email and click the verification link** — the form
says unverified submissions get delayed.

---

## Part E — After submitting

- Expect an email from the YouTube API Services team; first replies usually take days to
  a couple of weeks and may ask for clarifications or extra screenshots. Answer the same
  day, from the same address, keeping the case number in the subject.
- Meanwhile everything works, just with private uploads — fine for testing.
- **When approved:** in `config.yaml` set `audit_passed: true` and `privacy_default:
  public` (or keep private + use `publish:` dates in `meta.yaml` to schedule). Videos
  uploaded *before* approval stay locked private — re-upload the ones you want public.
- If denied, there's an appeals form linked from
  https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits — the usual
  reasons are a missing privacy policy element or no proof of the upload flow, both
  covered above.
