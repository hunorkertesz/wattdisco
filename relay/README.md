# Strava relay

Lets riders connect Watt Disco to Strava with one tap, without creating their own Strava API app.

Strava's sign-in needs the app's **client secret** twice: to turn the sign-in code into tokens, and to renew them every
few hours. A downloaded app can't keep a secret, so the secret lives in this small Cloudflare Worker instead. The app
sends it the code (or the renewal token), the Worker adds the secret, asks Strava and passes back only the tokens and
the rider's name.

It stores nothing and logs nothing. Rides never pass through it: the app uploads them straight to Strava.

```
Watt Disco on the rider's computer ──code──▶ relay (Cloudflare) ──code + secret──▶ Strava
                                   ◀─tokens─                     ◀─────tokens─────
Watt Disco ──────────────── .fit upload, straight ─────────────────────────────────▶ Strava
```

## Set it up, step by step

About 30 minutes, free, all in the browser. You need your Strava login and an email address for Cloudflare.

### 1. Your Strava API app

1. Sign in to Strava and open <https://www.strava.com/settings/api>. Strava says creating an API app needs a Strava
   subscription; an app you created earlier keeps working.
2. If there is no app yet, create one:
   - **Application Name:** Watt Disco
   - **Category:** the closest fit (for example Training)
   - **Website:** the project page, `https://github.com/hunorkertesz/wattdisco`
   - **Application Description:** for example "Indoor cycling training app. Uploads rides you choose."
   - **Authorization Callback Domain:** `localhost`
   - An app icon: the Watt Disco icon (`docs/images/icon.png`), never a Strava logo.
3. On the same page, note the **Client ID** (a number, public) and the **Client Secret** (keep it private).
   Never paste the secret into a chat, a file in this repository, an email or a screenshot. It goes only into Cloudflare,
   in step 3.

### 2. A Cloudflare account

1. Sign up at <https://dash.cloudflare.com/sign-up>. The free plan is enough: up to 100,000 requests a day, and each
   rider needs a handful a day at most.
2. Turn on two-factor sign-in (your profile → Authentication), because this account will hold the secret.
3. The first time you open **Workers & Pages**, Cloudflare asks for a **workers.dev subdomain**. It becomes part of
   the relay's public address (`https://wattdisco-strava-relay.<subdomain>.workers.dev`). Choose something neutral,
   such as `wattdisco`, not your name.

### 3. Create the Worker

1. In the dashboard, open **Workers & Pages** → **Create application** → start from the **Hello World** Worker.
2. Name it `wattdisco-strava-relay` and press **Deploy**.
3. Press **Edit code**, select all the sample code, and replace it with the whole of
   [`strava-relay.js`](strava-relay.js) from this folder. Press **Deploy**.
4. Back on the Worker's page, open **Settings** → **Variables and Secrets** → **Add**, twice, each time with the type
   **Secret**:
   - `STRAVA_CLIENT_ID`: the Client ID from step 1
   - `STRAVA_CLIENT_SECRET`: the Client Secret from step 1

   Then press **Deploy**. The dashboard hides the values from then on.
5. **Turn off logs.** New Workers keep logs by default. Under **Settings**, find **Observability** (Workers Logs) and
   switch it off, so the relay really logs nothing, as the privacy notice says. If you can't find the switch, use the
   Terminal way below, which sets it from [`wrangler.toml`](wrangler.toml).
6. Note the Worker's address, shown on its page: `https://wattdisco-strava-relay.<subdomain>.workers.dev`.

### 4. Check that it answers

Open Terminal and run, with your address:

```bash
curl -s -X POST https://wattdisco-strava-relay.<subdomain>.workers.dev/token -d '{"code":"0000000000000000000000000000000000000000"}'
```

| Answer | Meaning |
|---|---|
| `{"error":"Bad Request"}` or another message from Strava | **Working:** the relay has its secrets and reached Strava (the code was fake) |
| `{"error":"relay is not set up"}` | The secrets are missing or misspelt: check step 3.4 and deploy again |
| `{"error":"POST only"}` | You opened the address in a browser; use the command above |

### 5. Try it with the app you already have

1. Quit Watt Disco.
2. In the data folder (macOS `~/Library/Application Support/Watt Disco`, Windows `%LOCALAPPDATA%\Watt Disco`) create a
   text file named `.env` with two lines, and **no** `STRAVA_CLIENT_SECRET`:
   ```
   STRAVA_CLIENT_ID=<your Client ID>
   STRAVA_RELAY_URL=https://wattdisco-strava-relay.<subdomain>.workers.dev
   ```
   On a Mac, Finder hides names that start with a dot: in Terminal, `open ~/Library/Application\ Support/Watt\ Disco`
   opens the folder, and `nano` or TextEdit (Format → Make Plain Text) can save the file.
3. Start Watt Disco, finish a short ride, press **Upload ride** → **Connect to Strava**, and allow it on Strava's page.
   The screen should say **CONNECTED**, then upload the ride.
4. If Strava answers "invalid redirect_uri", change **Authorization Callback Domain** on the Strava page to `127.0.0.1`
   and try again: the app returns to `http://127.0.0.1:<port>/api/strava/callback`.

### 6. Build it into the app

Send the **Client ID** and the **relay address** to the maintainer of the app (both are public). They go into
`SHARED_CLIENT_ID` and `SHARED_RELAY_URL` in `src/wattdisco/export/strava.py`, and the next release connects to Strava
with one tap. Then delete the `.env` from step 5, or keep it: the same values do the same thing.

### 7. Let more riders connect

A new Strava app connects only **one athlete: you** ("single player mode"). Others get an error from Strava until you
raise it:

1. On <https://www.strava.com/settings/api>, upgrade the app to an athlete capacity of **10** (Strava's getting-started
   page describes this upgrade from the API settings).
2. For more than 10 riders, apply through Strava's Developer Program form, linked from
   <https://developers.strava.com/docs/getting-started/>. Strava reviews it and may say no.
3. Strava's rules for apps (brand guidelines, API agreement and policy) apply from the first rider on: no Strava logos
   as your own, a link to your privacy notice, and Strava's written consent before an announcement that mentions Strava.

Until then, everyone else can save the ride's `.fit` file and upload it at `strava.com/upload`.

## Privacy

[`PRIVACY.md`](../PRIVACY.md) already describes the relay: sign-in codes and tokens pass through Cloudflare
(Cloudflare, Inc., USA, EU–US Data Privacy Framework) on their way to Strava, and nothing is stored. Keep logs off
(step 3.5), and add the same to the website's privacy page.

## The Terminal way (optional)

Instead of steps 3.1 to 3.5, with [Node.js](https://nodejs.org) installed, from this folder:

```bash
npx wrangler login
npx wrangler deploy
npx wrangler secret put STRAVA_CLIENT_ID
npx wrangler secret put STRAVA_CLIENT_SECRET
```

`wrangler secret put` asks for each value without showing it. Deploying from here also applies `wrangler.toml`, which
switches the logs off.

## Changing the relay later

- **New secret** (if it ever leaked): on Strava's API page, generate a new Client Secret, then replace
  `STRAVA_CLIENT_SECRET` in the Worker (step 3.4). Riders may have to connect to Strava again.
- **New code:** paste the new `strava-relay.js` in **Edit code** and press **Deploy**.
- **Usage:** the Worker's page shows how many requests it gets a day.
