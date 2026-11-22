// Watt Disco Strava relay (Cloudflare Worker).
//
// Strava's sign-in needs the app's client secret to turn a sign-in code into tokens,
// and again to refresh them. A desktop download can't keep a secret, so riders' apps
// send the code here, and this Worker adds the secret and asks Strava.
//
// It stores nothing and logs nothing. Ride data never comes here: apps upload
// straight to Strava with the access token.
//
//   POST /token    {"code": "..."}           -> {access_token, refresh_token, expires_at, athlete}
//   POST /refresh  {"refresh_token": "..."}  -> {access_token, refresh_token, expires_at}
//
// Secrets (set with `wrangler secret put`): STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET.

const STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token";
const TOKEN_LIKE = /^[A-Za-z0-9]{20,100}$/; // Strava codes and refresh tokens are 40 hex characters
const MAX_BODY = 1024;

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method !== "POST") return reply(405, { error: "POST only" });
    if (!env.STRAVA_CLIENT_ID || !env.STRAVA_CLIENT_SECRET) return reply(500, { error: "relay is not set up" });

    const text = await request.text();
    if (text.length > MAX_BODY) return reply(413, { error: "request too large" });
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      return reply(400, { error: "send JSON" });
    }

    let grant;
    if (path === "/token" && TOKEN_LIKE.test(body?.code ?? "")) {
      grant = { grant_type: "authorization_code", code: body.code };
    } else if (path === "/refresh" && TOKEN_LIKE.test(body?.refresh_token ?? "")) {
      grant = { grant_type: "refresh_token", refresh_token: body.refresh_token };
    } else if (path === "/token" || path === "/refresh") {
      return reply(400, { error: "missing or malformed code" });
    } else {
      return reply(404, { error: "unknown path" });
    }

    let answer, data;
    try {
      answer = await fetch(STRAVA_TOKEN_URL, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ client_id: env.STRAVA_CLIENT_ID, client_secret: env.STRAVA_CLIENT_SECRET, ...grant }),
      });
      data = await answer.json();
    } catch {
      return reply(502, { error: "Strava did not answer" });
    }
    if (!answer.ok) {
      const status = answer.status === 429 ? 429 : answer.status >= 500 ? 502 : 400;
      return reply(status, { error: data?.message || "Strava refused the request" });
    }

    // Pass on only what the app needs.
    const out = { access_token: data.access_token, refresh_token: data.refresh_token, expires_at: data.expires_at };
    if (data.athlete) out.athlete = { id: data.athlete.id, firstname: data.athlete.firstname, lastname: data.athlete.lastname };
    return reply(200, out);
  },
};

function reply(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
