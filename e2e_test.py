"""End-to-end smoke test for the Ghost Font CAPTCHA service.

Run with the app NOT already running (this uses Flask's in-process test client
so it can share the in-memory token store):

    python e2e_test.py

Exercises the full no-API-key flow:
  1. GET challenge -> challenge_id + video_url
  2. GET video     -> 200 + mp4 bytes
  3. verify with correct code (peeked from the in-memory store) -> token
  4. siteverify with token -> success
  5. siteverify with same token again -> fail (single-use)
  6. verify with wrong code -> 401
  7. challenge with disallowed origin -> 403
"""

from __future__ import annotations

import sys

import app as _app
import store as _store
from config import ALLOWED_ORIGINS
from pool import pool as _pool

BASE = "http://localhost.localdomain"


def run() -> int:
    # Start the pool so challenges can be served.
    _pool.start()
    client = _app.app.test_client()

    origin = "http://localhost:5000"
    failures: list[str] = []

    def json_resp(r):
        return r.status_code, r.get_json()

    # 1. Challenge
    r = client.get(f"/api/captcha/challenge?origin={origin}")
    code, ch = json_resp(r)
    assert code == 200, f"challenge: expected 200, got {code}: {ch}"
    challenge_id = ch["challenge_id"]
    print(f"[1] challenge ok: id={challenge_id[:8]}…")

    # 1b. Peek the code from the shared in-memory store.
    challenge = _store.store.peek_challenge(challenge_id)
    assert challenge is not None, "could not peek challenge from store"
    real_code = challenge.code
    print(f"[1b] peeked code from store: {real_code}")

    # 2. Video stream
    r = client.get(f"/api/captcha/video?id={challenge_id}")
    is_mp4 = r.status_code == 200 and len(r.data) > 1000
    if not is_mp4:
        failures.append(f"video: expected 200 + mp4 bytes, got {r.status_code} len={len(r.data)}")
    else:
        print(f"[2] video ok: {r.status_code}, {len(r.data)} bytes")

    # 3. Verify with the CORRECT code -> should mint a token
    r = client.post(
        "/api/captcha/verify",
        json={"origin": origin, "challenge_id": challenge_id, "code": real_code},
    )
    code, resp = json_resp(r)
    if code != 200 or not resp.get("success") or not resp.get("token"):
        failures.append(f"verify(correct): expected 200 + token, got {code}: {resp}")
    else:
        token = resp["token"]
        print(f"[3] verify ok: token={token[:8]}…")

        # 4. siteverify with token -> success
        r = client.post("/api/siteverify", json={"response": token})
        code, resp = json_resp(r)
        if code != 200 or not resp.get("success"):
            failures.append(f"siteverify(token): expected 200 success, got {code}: {resp}")
        else:
            print(f"[4] siteverify ok: success={resp['success']} hostname={resp.get('hostname')}")

        # 5. siteverify with SAME token again -> must fail (single-use)
        r = client.post("/api/siteverify", json={"response": token})
        code, resp = json_resp(r)
        if code == 200 and resp.get("success"):
            failures.append("siteverify(replay): token was accepted twice (single-use violated!)")
        else:
            print(f"[5] replay blocked ok: {code} {resp.get('error-codes')}")

    # 6. New challenge + verify with WRONG code -> 401
    r = client.get(f"/api/captcha/challenge?origin={origin}")
    code, ch = json_resp(r)
    assert code == 200
    challenge_id2 = ch["challenge_id"]
    r = client.post(
        "/api/captcha/verify",
        json={"origin": origin, "challenge_id": challenge_id2, "code": "WRONG"},
    )
    code, resp = json_resp(r)
    if code != 401 or resp.get("success"):
        failures.append(f"verify(wrong): expected 401 failure, got {code}: {resp}")
    else:
        print(f"[6] wrong code rejected ok: {code} {resp.get('error-codes')}")

    # 7. challenge with disallowed origin (only if ALLOWED_ORIGINS is set)
    if ALLOWED_ORIGINS:
        r = client.get(f"/api/captcha/challenge?origin=https://evil.com")
        code, resp = json_resp(r)
        if code != 403:
            failures.append(f"challenge(evil origin): expected 403, got {code}: {resp}")
        else:
            print(f"[7] disallowed origin blocked ok: {code}")
    else:
        print("[7] ALLOWED_ORIGINS not set; skipping disallowed-origin test")

    # 8. /api.js serves the widget script
    r = client.get("/api.js")
    if r.status_code != 200 or b"ghostCaptcha" not in r.data:
        failures.append(f"/api.js: expected 200 with ghostCaptcha, got {r.status_code}")
    else:
        print(f"[8] /api.js ok: {r.status_code}, {len(r.data)} bytes")

    # 9. /widget renders
    r = client.get(f"/widget?origin={origin}")
    if r.status_code != 200 or b"captchaVideo" not in r.data:
        failures.append(f"/widget: expected 200 with widget, got {r.status_code}")
    else:
        print(f"[9] /widget ok: {r.status_code}")

    # 10. CORS preflight for widget API endpoints
    for path in ("/api/captcha/challenge", "/api/captcha/verify"):
        r = client.options(path)
        if r.status_code != 204:
            failures.append(f"OPTIONS {path}: expected 204, got {r.status_code}")
        else:
            print(f"[10] OPTIONS {path} ok: {r.status_code}")

    print("\n" + "=" * 50)
    if failures:
        print(f"FAILED ({len(failures)} issue(s)):")
        for f in failures:
            print(f"  - {f}")
        _pool.stop()
        return 1
    print("ALL END-TO-END TESTS PASSED")
    _pool.stop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
