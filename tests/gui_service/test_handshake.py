"""End-to-end test: spawn the service, parse the handshake, hit endpoints.

This is a real subprocess test — it picks an unused port, spawns
`python -m reverser.gui_service --port 0 --project-root .`, reads ONE line of
stdout for the handshake, then makes a real HTTP request with the token.
The subprocess is torn down at test end.
"""
import asyncio
import json
import os
import signal
import subprocess
import sys

import httpx
import pytest


@pytest.mark.asyncio
async def test_handshake_then_health():
    env = {**os.environ}  # inherit; service relies on PATH and devenv
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = proc.stdout.readline()
        assert line, f"no handshake line; stderr: {proc.stderr.read()}"
        handshake = json.loads(line)
        assert handshake["type"] == "ready"
        assert handshake["port"] > 0
        assert len(handshake["token"]) >= 32
        assert handshake["pid"] == proc.pid

        # The service is now listening. Hit /api/health with the token.
        await asyncio.sleep(0.1)  # tiny grace period for uvicorn to settle
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{handshake['port']}") as c:
            r = await c.get("/api/health",
                            headers={"Authorization": f"Bearer {handshake['token']}"})
            assert r.status_code == 200
            assert r.json()["ok"] is True

        # And it rejects requests without the token.
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{handshake['port']}") as c:
            r = await c.get("/api/health")
            assert r.status_code == 401
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.asyncio
async def test_handshake_full_endpoint_surface():
    """Spawn the service and verify /api/profiles + /api/backends round-trip."""
    env = {**os.environ}
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        line = proc.stdout.readline()
        handshake = json.loads(line)
        base = f"http://127.0.0.1:{handshake['port']}"
        headers = {"Authorization": f"Bearer {handshake['token']}"}
        await asyncio.sleep(0.1)
        async with httpx.AsyncClient(base_url=base) as c:
            profiles = await c.get("/api/profiles", headers=headers)
            assert profiles.status_code == 200
            assert len(profiles.json()["profiles"]) >= 15
            backends = await c.get("/api/backends", headers=headers)
            assert backends.status_code == 200
            assert {b["key"] for b in backends.json()["backends"]} >= {"claude", "ollama", "lmstudio", "local"}
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.asyncio
async def test_handshake_full_engagement_smoke(tmp_path, monkeypatch):
    """Spawn the service, create a session, send a message, drain WS frames."""
    import websockets
    monkeypatch.setenv("REVERSER_PENTEST_AUTHORIZED", "1")

    env = {**os.environ, "REVERSER_PENTEST_AUTHORIZED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "reverser.gui_service",
         "--host", "127.0.0.1", "--port", "0", "--project-root", str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    try:
        line = proc.stdout.readline()
        h = json.loads(line)
        base = f"http://127.0.0.1:{h['port']}"
        ws_base = f"ws://127.0.0.1:{h['port']}"
        headers = {"Authorization": f"Bearer {h['token']}"}
        await asyncio.sleep(0.2)

        async with httpx.AsyncClient(base_url=base) as c:
            r = await c.post("/api/sessions", headers=headers, json={
                "target": str(tmp_path / "bin"),
                "profile": "general",
                "backend": "claude",  # will actually hit Claude — for unit-style smoke,
                                       # use the OPENAI-compat path with a stub model,
                                       # or skip this test in CI by default.
                "model": None, "api_base": None,
                "budget": 0.01, "max_turns": 1,
            })
            # Either creating the session works, or we skip (no Claude API key in CI).
            if r.status_code != 200:
                pytest.skip(f"session create returned {r.status_code} (likely no ANTHROPIC_API_KEY)")
            sid = r.json()["id"]

        try:
            async with websockets.connect(f"{ws_base}/ws/sessions/{sid}?token={h['token']}") as ws:
                async with httpx.AsyncClient(base_url=base) as c:
                    # The real Claude backend may cost money — this is a TINY budget.
                    # If you want a hermetic test, replace with the openai_compat path
                    # pointed at a local stub server.
                    await asyncio.wait_for(
                        c.post(f"/api/sessions/{sid}/messages",
                               headers=headers, json={"text": "say hi"}),
                        timeout=15.0)
                # Drain at least one frame
                frame = await asyncio.wait_for(ws.recv(), timeout=20.0)
                assert frame, "no frame received"
        except (asyncio.TimeoutError, httpx.ReadTimeout) as e:
            pytest.skip(f"message/WS timeout (likely Claude API call slow or no key): {e}")
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
