# Strava relay

Lets riders connect Watt Disco to Strava without creating their own Strava API app.

Strava's sign-in needs the app's client secret twice: to turn the sign-in code into tokens, and to refresh them every few hours. A downloaded app can't keep a secret, so the secret lives in this small Cloudflare Worker instead. The app sends it the code (or the refresh token), the Worker adds the secret, asks Strava and passes back only the tokens and the rider's name.

It stores nothing and logs nothing. Rides never pass through it: the app uploads them straight to Strava.

## Set up (once, free)

1. **Shared Strava app.** At <https://www.strava.com/settings/api> create the app "Watt Disco" with Authorization Callback Domain `localhost`. Note its Client ID and Client Secret.
2. **Cloudflare account.** The free Workers plan is enough (100,000 requests a day).
3. **Deploy**, from this folder:
   ```bash
   npx wrangler login
   npx wrangler secret put STRAVA_CLIENT_ID
   npx wrangler secret put STRAVA_CLIENT_SECRET
   npx wrangler deploy
   ```
   Wrangler prints the Worker's address, for example `https://wattdisco-strava-relay.<account>.workers.dev`.
4. **Point the app at it.** Put the Client ID and the address into `SHARED_CLIENT_ID` and `SHARED_RELAY_URL` in `src/wattdisco/export/strava.py`. Both are public; only the secret is private. To try it first without changing the code, set them in `.env`:
   ```
   STRAVA_CLIENT_ID=12345
   STRAVA_RELAY_URL=https://wattdisco-strava-relay.<account>.workers.dev
   ```
   (no `STRAVA_CLIENT_SECRET`, or the app uses its own keys directly).
5. **Ask Strava for more athletes.** A new Strava app connects only one athlete (you). Apply through Strava's Developer Program form for a higher athlete capacity. Until it is approved, other riders can use their own Strava API app (the advanced option) or upload the saved .fit file by hand.

## Check it

```bash
curl -s -X POST https://wattdisco-strava-relay.<account>.workers.dev/token -d '{"code":"0000000000000000000000000000000000000000"}'
```
should answer `{"error":"..."}` from Strava (the code is fake), which shows the Worker is up and has its secrets.

## Privacy

Add the relay to the website's Datenschutz page: sign-in codes and tokens pass through Cloudflare (Cloudflare, Inc., USA, EU–US Data Privacy Framework) on their way to Strava; nothing is stored.
