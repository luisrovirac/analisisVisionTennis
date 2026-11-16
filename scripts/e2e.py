"""Prueba de punta a punta contra el stack de docker-compose (solo biblioteca estándar).

Uso:  python scripts/e2e.py samples/forehand.mp4 [--api http://localhost:8080] [--timeout 300]
Sale con código 0 si el análisis termina en `completed`.

Si la API tiene la autenticación activa, obtiene tokens de los usuarios de prueba del realm de
desarrollo (alice y bob) con el cliente `tennis-cli` y comprueba que bob no ve el análisis de alice.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEV_USERS = {"alice": "alice-dev-only", "bob": "bob-dev-only"}
CLI_CLIENT_ID = "tennis-cli"


def request(method: str, url: str, body: bytes | None = None, content_type: str = "application/json",
            token: str | None = None, expect: int | None = None) -> Any:
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == expect:
            return None
        sys.exit(f"{method} {url} → {exc.code}: {exc.read().decode(errors='replace')}")
    if expect is not None:
        sys.exit(f"{method} {url}: se esperaba {expect} y respondió 2xx")
    return json.loads(raw) if raw else None


def password_token(issuer: str, username: str) -> str:
    """Password grant: solo existe en el realm de desarrollo, nunca en producción."""
    form = urllib.parse.urlencode({"grant_type": "password", "client_id": CLI_CLIENT_ID,
                                   "username": username, "password": DEV_USERS[username]}).encode()
    tokens = request("POST", f"{issuer}/protocol/openid-connect/token", form, "application/x-www-form-urlencoded")
    return str(tokens["access_token"])


def tokens_for(api: str) -> tuple[str | None, str | None]:
    config = request("GET", f"{api}/auth/config")
    if not config.get("enabled"):
        print("autenticación desactivada en la API (AUTH_DISABLED)")
        return None, None
    return password_token(config["issuer"], "alice"), password_token(config["issuer"], "bob")


def run(video: Path, api: str, timeout: float) -> int:
    request("GET", f"{api}/v1/analyses", expect=401)
    print("0/4 sin token → 401")
    token, other = tokens_for(api)
    ticket = request("POST", f"{api}/v1/analyses", json.dumps({"stroke_type": "forehand"}).encode(), token=token)
    analysis_id = ticket["analysis"]["id"]
    print(f"1/4 creado {analysis_id}")
    content_type = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    request("PUT", ticket["upload_url"], video.read_bytes(), content_type)
    print(f"2/4 subido {video.name} ({video.stat().st_size / 1e6:.1f} MB)")
    if other:
        request("POST", f"{api}/v1/analyses/{analysis_id}/submit", token=other, expect=404)
        print("    bob no puede enviar el análisis de alice → 404")
    submitted = request("POST", f"{api}/v1/analyses/{analysis_id}/submit", token=token)
    print(f"3/4 enviado: {submitted['status']}")
    if other:
        request("GET", f"{api}/v1/analyses/{analysis_id}", token=other, expect=404)
        print("    bob no ve el análisis de alice → 404")
    return poll(api, analysis_id, timeout, token)


def poll(api: str, analysis_id: str, timeout: float, token: str | None) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        analysis = request("GET", f"{api}/v1/analyses/{analysis_id}", token=token)
        if analysis["status"] in ("completed", "failed"):
            print(f"4/4 {analysis['status']}")
            print(json.dumps(analysis.get("report") or analysis.get("error"), indent=2, ensure_ascii=False))
            return 0 if analysis["status"] == "completed" else 1
        time.sleep(2)
    print("tiempo de espera agotado", file=sys.stderr)
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # consolas de Windows
    if not args.video.is_file():
        sys.exit(f"no existe el video {args.video}")
    sys.exit(run(args.video, args.api.rstrip("/"), args.timeout))


if __name__ == "__main__":
    main()
