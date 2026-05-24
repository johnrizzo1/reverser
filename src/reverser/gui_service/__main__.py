"""Service entry point.

Mints a per-launch token, binds a free localhost port, runs uvicorn,
and emits a single JSON handshake line to stdout once uvicorn is
actually accepting connections. Stdout after the handshake is free-form
logs (forwarded from uvicorn).
"""
import argparse
import asyncio
import json
import os
import secrets
import socket
import sys

import uvicorn

from .app import create_app
from .config import ServiceConfig


def _find_free_port(host: str) -> int:
    """Bind a temporary socket to pick a free port, then release it.

    There is a small race window between releasing and uvicorn binding
    that we accept — collisions on a single-user machine are vanishingly
    unlikely, and uvicorn will fail loudly if it does happen.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _serve(server: uvicorn.Server, handshake: dict) -> None:
    """Run the server and emit the handshake once it's actually accepting.

    Without this, the handshake races uvicorn's socket bind — the
    supervisor reads it, tells the renderer the port is ready, and the
    renderer's first fetch arrives before the listen socket is open,
    producing ERR_CONNECTION_REFUSED in the renderer console.
    """
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.01)
    print(json.dumps(handshake), flush=True)
    await task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reverser.gui_service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 = pick a free port (default)")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)

    host = args.host
    port = args.port or _find_free_port(host)
    token = secrets.token_hex(32)
    project_root = os.path.abspath(args.project_root)

    config = ServiceConfig(host=host, port=port, token=token, project_root=project_root)
    app = create_app(config)

    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="info", access_log=False,
    ))
    handshake = {
        "type": "ready",
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "version": "0.1.0",
    }
    asyncio.run(_serve(server, handshake))
    return 0


if __name__ == "__main__":
    sys.exit(main())
