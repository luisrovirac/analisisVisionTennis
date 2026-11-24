"""Prueba de punta a punta contra el stack de docker-compose (solo biblioteca estándar).

Uso:  python scripts/e2e.py samples/forehand.mp4 [--api http://localhost:8080] [--timeout 300]
Sale con código 0 si el análisis termina en `completed`.

Si la API tiene la autenticación activa, obtiene tokens de los usuarios de prueba del realm de
desarrollo (alice y bob) con el cliente `tennis-cli` y comprueba que bob no ve el análisis de alice.
Al final, bob (experto temporal) hace una revisión experta del caso y se consulta la calidad de la base.
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


def run(video: Path, api: str, timeout: float, stroke: str = "forehand") -> int:
    request("GET", f"{api}/v1/analyses", expect=401)
    print("0/5 sin token → 401")
    token, other = tokens_for(api)
    ticket = request("POST", f"{api}/v1/analyses", json.dumps({"stroke_type": stroke}).encode(), token=token)
    analysis_id = ticket["analysis"]["id"]
    print(f"1/5 creado {analysis_id}")
    content_type = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    request("PUT", ticket["upload_url"], video.read_bytes(), content_type)
    print(f"2/5 subido {video.name} ({video.stat().st_size / 1e6:.1f} MB)")
    if other:
        request("POST", f"{api}/v1/analyses/{analysis_id}/submit", token=other, expect=404)
        print("    bob no puede enviar el análisis de alice → 404")
    submitted = request("POST", f"{api}/v1/analyses/{analysis_id}/submit", token=token)
    print(f"3/5 enviado: {submitted['status']}")
    if other:
        request("GET", f"{api}/v1/analyses/{analysis_id}", token=other, expect=404)
        print("    bob no ve el análisis de alice → 404")
    code = poll(api, analysis_id, timeout, token)
    if code == 0:
        verify_media(api, analysis_id, token, other)
        if token and other:
            verify_expert_review(api, analysis_id, stroke, token, other)
    return code


def verify_expert_review(api: str, analysis_id: str, stroke: str, admin: str, reviewer: str) -> None:
    """Revisión experta (control de calidad de las reglas): alice (admin, dueña del video) da a bob
    el rol de experto, bob revisa el caso y luego se le devuelven sus roles originales."""
    me = request("GET", f"{api}/v1/me", token=reviewer)
    original = me.get("roles") or []

    def set_roles(roles: list[str]) -> None:
        request("PUT", f"{api}/v1/admin/users/{me['id']}/roles", json.dumps({"roles": roles}).encode(), token=admin)

    set_roles(sorted(set(original) | {"expert"}))
    try:
        rules = request("GET", f"{api}/v1/kb/strokes/{stroke}", token=reviewer)["knowledge_base"]["rules"]
        item = {"rule_id": rules[0]["id"], "rating": 3, "not_evaluable": False, "verdict": "unsure", "comment": "e2e"}
        saved = request("PUT", f"{api}/v1/kb/cases/{analysis_id}/review",
                        json.dumps({"player_level": "intermediate", "notes": "e2e", "items": [item]}).encode(),
                        token=reviewer)
        if not isinstance(saved["items"][0].get("system_fired"), bool):
            sys.exit("revisión: system_fired debe congelarse desde el reporte")
        mine = request("GET", f"{api}/v1/kb/cases/{analysis_id}/reviews", token=reviewer)["mine"]
        if not mine or mine["items"][0]["rating"] != 3:
            sys.exit("revisión: no se recupera la revisión propia")
        print(f"    revisión experta de {rules[0]['id']} guardada (sistema disparó: {saved['items'][0]['system_fired']})")
        quality = request("GET", f"{api}/v1/kb/quality?stroke={stroke}", token=admin)
        print(f"    calidad de la base {stroke}@{quality['version']}: {len(quality['rules'])} reglas, "
              f"{quality['validated_pct']:.0f}% revisadas, {quality['rules_needing_audit']} a auditar")
    finally:
        set_roles(original)


def verify_media(api: str, analysis_id: str, token: str | None, other: str | None) -> None:
    """El visor de esqueleto: URLs firmadas de video y pose descargables, y el reporte 1.2."""
    media = request("GET", f"{api}/v1/analyses/{analysis_id}/media", token=token)
    with urllib.request.urlopen(media["pose_url"], timeout=60) as resp:
        pose = json.loads(resp.read())
    with urllib.request.urlopen(urllib.request.Request(media["video_url"], headers={"Range": "bytes=0-1023"}),
                                timeout=60) as resp:
        video_bytes = len(resp.read())
    if not pose.get("frames") or video_bytes == 0:
        sys.exit("media: la pose o el video firmados están vacíos")
    print(f"5/5 media: pose con {len(pose['frames'])} fotogramas a {pose['fps']:.0f} fps "
          f"({pose.get('width')}x{pose.get('height')}), video accesible")
    if other:
        request("GET", f"{api}/v1/analyses/{analysis_id}/media", token=other, expect=404)
        print("    bob no obtiene los medios de alice → 404")
    report = request("GET", f"{api}/v1/analyses/{analysis_id}", token=token)["report"]
    if not all(m.get("landmarks") for m in report["metrics"]):
        sys.exit("reporte 1.2: faltan metrics[].landmarks")
    comparison = report.get("reference_comparison")
    print(f"    reporte 1.2: landmarks por métrica ✓ · comparación con referentes: "
          f"{comparison['status'] if comparison else 'desactivada'}")


def poll(api: str, analysis_id: str, timeout: float, token: str | None) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        analysis = request("GET", f"{api}/v1/analyses/{analysis_id}", token=token)
        if analysis["status"] in ("completed", "failed"):
            print(f"4/5 {analysis['status']}")
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
    parser.add_argument("--stroke", default="forehand", choices=["forehand", "backhand", "serve", "volley"])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # consolas de Windows
    if not args.video.is_file():
        sys.exit(f"no existe el video {args.video}")
    sys.exit(run(args.video, args.api.rstrip("/"), args.timeout, args.stroke))


if __name__ == "__main__":
    main()
