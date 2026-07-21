# Ghost Font CAPTCHA

A production-ready, embeddable CAPTCHA service that uses an animated
"ghost font" video challenge. **No API keys required.**

- ✅ Drop-in widget (`<script src="/api.js">` + `<div class="ghost-captcha">`)
- ✅ Token-based verification (works cross-origin, no sessions)
- ✅ Pre-generated video pool (no per-request CPU spike)
- ✅ Redis-backed token store with in-memory fallback
- ✅ Rate limiting on every endpoint
- ✅ Security headers, CSP, sandboxed iframe

## Quickstart

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

The demo page at `/` shows the widget and integration examples.

## Integration

### 1. Client side (any site)

```html
<!-- Load the widget script once -->
<script src="https://your-host/api.js" async defer></script>

<!-- Add a div where you want the widget -->
<form action="/signup" method="post">
  <div class="ghost-captcha"></div>
  <button type="submit">Sign up</button>
</form>
```

The script auto-injects a hidden `<input name="ghost-captcha-response">`
into the nearest `<form>`, so standard form submissions carry the token.
You can also use a callback:

```html
<div class="ghost-captcha" data-callback="onVerified"></div>

<script>
  function onVerified(token) {
    // token is also available via ghostCaptcha.getResponse(el)
  }
</script>
```

Supported `data-*` attributes:
- `data-callback` - global function name invoked with the token
- `data-theme` - `"dark"` (default) or `"light"`
- `data-language` - passed to the widget as `lang`

### 2. Server side (any backend)

When the form is submitted, take the `ghost-captcha-response` value and
verify it server-to-server:

```http
POST https://your-host/api/siteverify
Content-Type: application/json

{
  "response": "TOKEN_FROM_FORM"
}
```

Response (success):

```json
{
  "success": true,
  "challenge_ts": "2026-07-21T12:00:00+00:00",
  "hostname": "https://example.com",
  "metadata": {}
}
```

Response (failure):

```json
{
  "success": false,
  "error-codes": ["invalid-or-expired-token"]
}
```

### Error codes

| Code                     | Meaning                                    |
|--------------------------|--------------------------------------------|
| `missing-input-response` | No `response` token in the request.        |
| `invalid-or-expired-token` | The token is invalid, used, or expired.  |

## Restricting origins

By default, any origin can embed the widget.  In production, set
`ALLOWED_ORIGINS` to a comma-separated list of allowed origins:

```bash
ALLOWED_ORIGINS="https://example.com,https://app.example.com" python app.py
```

## Configuration

All settings are environment variables (see `.env.example`):

| Variable               | Default               | Description                          |
|------------------------|-----------------------|--------------------------------------|
| `PUBLIC_BASE_URL`      | `http://localhost:5000` | Public URL of the service.        |
| `SECRET_KEY`           | random                | Flask secret key. Set in production. |
| `REDIS_URL`            | (empty)               | Redis for tokens & rate limits.      |
| `ALLOWED_ORIGINS`      | (empty)               | Comma-separated allowed origins.     |
| `CHALLENGE_TTL`        | `180`                 | Challenge lifetime (seconds).        |
| `TOKEN_TTL`            | `120`                 | Verified-token lifetime (seconds).   |
| `POOL_MIN` / `POOL_MAX`| `8` / `32`            | Pre-generation pool bounds.          |
| `RATE_CHALLENGE`       | `30 per minute`       | Rate limit for challenge endpoint.   |
| `RATE_VERIFY`          | `30 per minute`       | Rate limit for verify endpoint.      |
| `RATE_SITEVERIFY`      | `120 per minute`      | Rate limit for siteverify endpoint.  |

## Scaling

- **Single instance:** works with zero external deps (in-memory store).
- **Multiple instances:** set `REDIS_URL` for a shared token store and
  distributed rate limiting. The pre-generation pool runs per-process.

## Architecture

```
config.py    environment configuration
pool.py      background pre-generation of videos
store.py     Redis/in-memory token store (challenges + tokens)
app.py       Flask routes (widget, challenge, verify, siteverify)
static/ghost-captcha.js   drop-in widget script
templates/widget.html     iframe widget page
templates/demo.html     integration demo page
```

## Security notes

- Tokens are **single-use**: redeemed once, then deleted.
- Challenges are **single-use**: consumed on verify or after TTL.
- The widget runs in a sandboxed iframe with `postMessage` validation.
- The video is never cached by the browser (`no-store`).
- Rate limiting protects all endpoints from abuse.
- Set `SECRET_KEY` and `REDIS_URL` in production.
