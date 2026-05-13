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
