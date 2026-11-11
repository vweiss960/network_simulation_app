"""WebSocket endpoints for in-browser console + log streaming.

Two endpoints:

  WS /api/topologies/{topology_id}/lab/console/{node_id}?token=<jwt>
    Bidirectional PTY: keystrokes from browser → docker exec; output back.

  WS /api/topologies/{topology_id}/lab/logs/{node_id}?token=<jwt>
    One-way log tail: `docker logs --follow` streamed to browser.

Both use a token-in-query-string auth scheme because browsers don't send
Authorization headers on WebSocket upgrades reliably.
"""

import asyncio
import json
from typing import Optional

import docker
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status
from sqlalchemy.orm import Session

from app.auth.service import decode_token
from app.models.database import get_db
from app.services.lab_view import compute_lab_view


router = APIRouter(prefix="/api/topologies/{topology_id}/lab", tags=["lab"])


def _container_name_for_node(db: Session, topology_id: int, node_id: int) -> Optional[str]:
    """Resolve the node id → clab container name via the lab view."""
    try:
        view = compute_lab_view(db, topology_id)
    except Exception:
        return None
    for n in view.get("nodes", []):
        if n.get("id") == node_id:
            return n.get("lab_container_name")
    return None


def _cli_for_node(db: Session, topology_id: int, node_id: int) -> list[str]:
    """CLI entrypoint for `docker exec`, from the device kind registry."""
    from app.services.device_kinds import get_kind
    try:
        view = compute_lab_view(db, topology_id)
    except Exception:
        return ["sh"]
    for n in view.get("nodes", []):
        if n.get("id") != node_id:
            continue
        kind = n.get("lab_kind") or ""
        if kind == "linux" and "frr" in (n.get("lab_image") or "").lower():
            return ["vtysh"]
        return list(get_kind(kind).exec_cli)
    return ["sh"]


async def _authenticate(ws: WebSocket) -> Optional[dict]:
    """Validate the JWT passed as ?token=. Closes the WS on failure."""
    token = ws.query_params.get("token") or ""
    try:
        payload = decode_token(token)
        if payload:
            return payload
    except Exception:
        pass
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    return None


@router.websocket("/console/{node_id}")
async def console_ws(
    ws: WebSocket,
    topology_id: int,
    node_id: int,
    cli: str = "",  # override entrypoint via ?cli=sh, etc.
    db: Session = Depends(get_db),
):
    """Browser ↔ docker exec PTY bridge.

    Frames from the browser are either raw bytes (keystrokes) or short JSON
    control messages:
        {"resize": {"cols": 80, "rows": 24}}
    Frames from docker are sent as raw text to the browser (UTF-8).
    """
    await ws.accept()
    if not await _authenticate(ws):
        return

    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        await ws.send_text(f"\x1b[31m[plabs] No container for node {node_id}\x1b[0m\r\n")
        await ws.close()
        return

    entrypoint = [cli] if cli else _cli_for_node(db, topology_id, node_id)

    client = docker.from_env()
    try:
        ctr = client.containers.get(cname)
    except Exception as e:
        await ws.send_text(f"\x1b[31m[plabs] Container {cname} not found: {e}\x1b[0m\r\n")
        await ws.close()
        return
    if ctr.status != "running":
        await ws.send_text(f"\x1b[31m[plabs] Container {cname} is {ctr.status} — start it first.\x1b[0m\r\n")
        await ws.close()
        return

    # XRd Control Plane's /pkg/bin/xr_cli is a one-shot command wrapper —
    # it doesn't have a built-in REPL. Wrap it in a small bash loop so the
    # user gets a prompt and can type commands one at a time. For full
    # interactive XR CLI (config mode, tab completion, history), SSH to
    # the node — clab/clab@123 by default.
    if entrypoint == ["/pkg/bin/xr_cli"]:
        cmd_list = [
            "bash", "-c",
            (
                "echo '[plabs] xr_cli wrapper. Each line runs xr_cli \"<cmd>\".';"
                "echo '[plabs] For real config mode + tab completion, SSH to the device.';"
                "echo;"
                "while IFS= read -r -e -p 'RP/0/RP0/CPU0:xrd# ' cmd; do "
                "  case \"$cmd\" in exit|quit) exit 0;; \"\") continue;; esac;"
                "  /pkg/bin/xr_cli \"$cmd\";"
                "done"
            ),
        ]
    else:
        cmd_list = entrypoint

    # Create the exec, then open a socket for it
    api = client.api
    try:
        exec_id = api.exec_create(
            cname, cmd_list,
            tty=True, stdin=True, stdout=True, stderr=True,
        )["Id"]
    except Exception as e:
        await ws.send_text(f"\x1b[31m[plabs] exec_create failed: {e}\x1b[0m\r\n")
        await ws.close()
        return

    sock = api.exec_start(exec_id, tty=True, stream=False, socket=True)
    # docker SDK wraps the socket; the actual fd is at ._sock on linux
    raw_sock = sock._sock if hasattr(sock, "_sock") else sock
    raw_sock.setblocking(False)

    await ws.send_text(f"\x1b[32m[plabs] Connected to {cname} ({' '.join(entrypoint)}). Press Enter if no prompt appears.\x1b[0m\r\n")

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    async def docker_to_ws():
        """Read from docker socket, push to browser."""
        while not stop_event.is_set():
            try:
                data = await loop.run_in_executor(None, _read_some, raw_sock)
                if data is None:
                    break
                if data:
                    try:
                        await ws.send_text(data.decode("utf-8", errors="replace"))
                    except Exception:
                        break
                else:
                    await asyncio.sleep(0.02)
            except Exception:
                break
        stop_event.set()

    async def ws_to_docker():
        """Read from browser, push to docker socket (and handle resize)."""
        while not stop_event.is_set():
            try:
                msg = await ws.receive()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")
            data = msg.get("bytes")

            if text is not None:
                # Control message?
                if text.startswith("{") and '"resize"' in text:
                    try:
                        parsed = json.loads(text)
                        rs = parsed.get("resize")
                        if rs:
                            try:
                                api.exec_resize(exec_id, height=int(rs.get("rows", 24)), width=int(rs.get("cols", 80)))
                                continue
                            except Exception:
                                continue
                    except Exception:
                        pass
                payload = text.encode("utf-8")
            elif data is not None:
                payload = data
            else:
                continue
            try:
                await loop.run_in_executor(None, _write_all, raw_sock, payload)
            except Exception:
                break
        stop_event.set()

    try:
        await asyncio.gather(docker_to_ws(), ws_to_docker())
    finally:
        try:
            raw_sock.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


def _read_some(sock) -> Optional[bytes]:
    """Non-blocking read of up to 4096 bytes. None = remote closed, b'' = no data right now."""
    try:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        return chunk
    except BlockingIOError:
        return b""
    except OSError:
        return None


def _write_all(sock, data: bytes) -> None:
    """Blocking-style write that handles partial sends."""
    view = memoryview(data)
    while view:
        try:
            n = sock.send(view)
            if n <= 0:
                return
            view = view[n:]
        except BlockingIOError:
            # tiny backoff and retry — write buffer full
            import time
            time.sleep(0.005)


@router.websocket("/logs/{node_id}")
async def logs_ws(
    ws: WebSocket,
    topology_id: int,
    node_id: int,
    db: Session = Depends(get_db),
):
    """Stream `docker logs --follow` for the node's container."""
    await ws.accept()
    if not await _authenticate(ws):
        return

    cname = _container_name_for_node(db, topology_id, node_id)
    if not cname:
        await ws.send_text(f"[plabs] No container for node {node_id}\n")
        await ws.close()
        return

    client = docker.from_env()
    try:
        ctr = client.containers.get(cname)
    except Exception as e:
        await ws.send_text(f"[plabs] Container {cname} not found: {e}\n")
        await ws.close()
        return

    await ws.send_text(f"\x1b[32m[plabs] Tailing logs for {cname}\x1b[0m\n")

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _stream_logs():
        try:
            for chunk in ctr.logs(stream=True, follow=True, tail=200):
                if stop_event.is_set():
                    return
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                # Schedule the send back on the asyncio loop
                future = asyncio.run_coroutine_threadsafe(ws.send_text(chunk), loop)
                try:
                    future.result(timeout=5)
                except Exception:
                    return
        except Exception:
            return

    task = loop.run_in_executor(None, _stream_logs)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        try:
            await ws.close()
        except Exception:
            pass
        try:
            task.cancel()
        except Exception:
            pass
