# BeatPub — Privacy Policy

_Last updated: 18 November 2026_

BeatPub is a personal command-line tool operated by Bruno Betko ("I", "me") that
publishes my own instrumental music videos to my own YouTube channel (CapryBeats). It is
not distributed to, or used by, anyone else. This policy explains what data the tool
touches and how it is handled.

## BeatPub uses YouTube API Services

BeatPub uses YouTube API Services to upload videos to my channel, set their thumbnails,
add them to my playlists, and read the titles, descriptions and tags of my own existing
uploads. By using BeatPub you are also bound by the
[YouTube Terms of Service](https://www.youtube.com/t/terms). Google's handling of your
data is described in the [Google Privacy Policy](http://www.google.com/policies/privacy).

## What data is accessed

When the operator signs in with Google, BeatPub requests the OAuth scope
`https://www.googleapis.com/auth/youtube` ("Manage your YouTube account") for the signed-in
account only. With it the tool:

- reads the channel ID, playlists and the metadata (title, description, tags, publish
  date) of videos already on that channel;
- uploads new videos, sets custom thumbnails and adds videos to playlists on that channel.

BeatPub does not access, collect or store any data about other YouTube users, viewers,
comments or subscribers, and it does not read private information from any other Google
service.

## What is stored, where, and for how long

Everything stays on the operator's own computer:

- **OAuth token** (`token.json`) — so the tool can act on the channel without signing in
  every time. Deleted by revoking access (below) or by deleting the file.
- **A copy of my own channel's video metadata** (titles, descriptions, tags) used only to
  keep new uploads consistent with existing ones. It is refreshed whenever the tool's
  `learn` command runs, is not kept longer than 30 days without being refreshed, and is
  deleted when access is revoked.
- **A local log** of what the tool uploaded (video ID, title, date).

No data is sent to any server other than Google's YouTube API, and nothing is shared
with, sold to, or processed by third parties. BeatPub shows no advertising and uses no
cookies (this website's pages may use cookies as described in the site's own policy).

## Revoking access

You can revoke BeatPub's access to your Google account at any time on the
[Google security settings page](https://security.google.com/settings/security/permissions)
(also reachable at https://myaccount.google.com/permissions). Revocation stops all access
immediately; the locally stored token becomes invalid and should be deleted.

## Contact

Questions about this policy: loopsforcapry@gmail.com.

## Changes

If this policy changes, the updated version is published at this address with a new
"last updated" date.
