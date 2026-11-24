# Privacy

Watt Disco is built so that your training data stays with you. This page explains what the app keeps, what
leaves your computer, and when.

**Short version:** no account, no analytics, no ads, no crash reports, no update checks. Your rides stay in a
folder on your computer. Something leaves it only when you connect Strava, and then only to Strava (and, for the
sign-in, through the Watt Disco relay described below).

## Who is responsible

For the Strava relay and this project: **Hunor Kertesz**, [wattdisco@pm.me](mailto:wattdisco@pm.me).
The app itself runs only on your computer; the project never receives your rides or settings.

## What the app keeps on your computer

Everything is in your own app folder (macOS `~/Library/Application Support/Watt Disco`, Windows
`%LOCALAPPDATA%\Watt Disco`, Linux `~/.local/share/Watt Disco`, or the folder set with `WATTDISCO_DATA`):

| What | Why |
|---|---|
| Rides: every sample from your sensors (power, cadence, heart rate and beat-to-beat intervals, left/right balance, temperature where a sensor sends it), the workout, your FTP and weight at the time; on a climb also the virtual distance, height, speed and gradient each second | The ride summary, history, progress and the files you export |
| Settings: FTP, weight, heart rate zones, units, favourites, sound on or off | Zones and targets |
| Training plan progress, FTP history, how each ride felt and your notes | Plans, progress and history |
| Your devices: their names and Bluetooth or ANT+ ids, roles, last zero offset | Reconnecting them next time |
| The Strava sign-in (`strava_tokens.json`, readable only by your user account) | Uploading rides |
| Deleted rides, in `deleted/`, for 7 days | So a delete can be undone; after that they are removed for good |

Heart rate counts as health data. It never leaves your computer unless you upload a ride to Strava.

The app's screen is a local web page served on `127.0.0.1`, which only your own computer can reach. Fonts and
images are bundled with the app; nothing is loaded from the internet. Bluetooth and ANT+ only talk to your
sensors.

**To delete everything:** quit the app and delete the folder above. To delete one ride: the × in History (or
Discard on its summary); it is gone for good after 7 days, or at once if you also empty `deleted/`.

## Strava (only if you connect it)

- **Connecting:** you sign in on Strava's own page and allow Watt Disco to **read your profile and upload
  activities** (scopes `read` and `activity:write`). The app never sees your Strava password.
- **Uploading:** when you press Upload (or switch on automatic upload), the app sends that ride's `.fit` file
  straight to Strava: power, heart rate, cadence and estimated speed and distance, with the workout's name and a short description
  of it. New
  uploads are visible to "only you" until you change that in the app or on Strava.
- **What the app reads back:** your Strava name and id (to show who is connected) and the upload's status with the
  activity's number (for the "View on Strava" link). It never reads your other activities.
- **Disconnecting:** "Disconnect" in the app revokes the access on Strava and deletes the sign-in from your
  computer. The activity number stays next to your own ride file until you delete that ride. Rides
  already uploaded stay on Strava; delete them there if you want. You can also withdraw access at any time on
  Strava (Settings → My Apps).
- Strava records how apps use its interface (for example when an upload is sent). What Strava does with that and with
  your data is covered by [Strava's privacy policy](https://www.strava.com/legal/privacy).

## The Watt Disco relay (Strava sign-in only)

Strava's sign-in needs a secret key that a downloaded app can't keep safe. That key lives in a small relay, a
Cloudflare Worker run by this project. It is used only for the sign-in and for renewing it every few hours:

- **What passes through:** the one-time sign-in code from Strava, or the renewal token. The relay adds the secret
  key, asks Strava, and passes back the access tokens and your Strava id and name.
- **What it keeps:** nothing. It has no database and writes no logs. Your rides never go through it; the app
  uploads them straight to Strava.
- **Cloudflare:** the relay runs on Cloudflare (Cloudflare, Inc., USA; certified under the EU–US Data Privacy
  Framework). Like any web service, Cloudflare processes your IP address and the time of the request to deliver
  it and to protect against abuse. See Cloudflare's privacy policy.
- **Why (GDPR):** to provide the Strava connection you asked for (Art. 6(1)(b) GDPR).
- **Without the relay:** if you use your own Strava API keys (see the README), the app talks to Strava directly and
  the relay is not used at all.

## Your rights

You can ask what the project holds about you, and to correct or delete it, at [wattdisco@pm.me](mailto:wattdisco@pm.me). Because the app
keeps your data on your own computer and the relay stores nothing, there is usually nothing to look up: your data
is in the folder above, and on Strava if you uploaded it. You can also complain to a data protection authority.

## Changes

If this page changes, the new version is published here, with every change in the project's history.
