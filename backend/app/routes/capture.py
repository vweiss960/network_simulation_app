"""Packet capture on lab links (services/packet_capture.py).

REST (bearer auth): start / list / stop / delete, one packet's details, a
display filter, the pcapng download. WebSocket (?token=): live packet rows.
"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.models.database import get_db
from app.models.topology import Topology
from app.routes.lab_console import _authenticate
from app.services import packet_capture as pc

router = APIRouter(prefix="/api/topologies/{topology_id}", tags=["capture"])
ws_router = APIRouter(prefix="/api/topologies/{topology_id}/lab", tags=["capture"])


class StartIn(BaseModel):
    filter: str = Field("", max_length=500)


def _cap(topology_id: int, capture_id: str) -> pc.Capture:
    try:
        return pc.get(capture_id, topology_id)
    except KeyError:
        raise HTTPException(404, "Capture not found (captures end when the backend restarts)")


@router.post("/links/{link_id}/capture")
def start_capture(topology_id: int, link_id: int, body: StartIn, user=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    if not db.get(Topology, topology_id):
        raise HTTPException(404, "Topology not found")
    try:
        cap = pc.start(db, topology_id, link_id, getattr(user, "username", ""), body.filter)
    except pc.CaptureError as e:
        raise HTTPException(400, str(e))
    return cap.summary()


@router.get("/captures")
def list_captures(topology_id: int):
    return [c.summary() for c in pc.list_for(topology_id)]


@router.get("/captures/{capture_id}")
def get_capture(topology_id: int, capture_id: str):
    return _cap(topology_id, capture_id).summary()


@router.post("/captures/{capture_id}/stop")
def stop_capture(topology_id: int, capture_id: str):
    cap = _cap(topology_id, capture_id)
    pc.stop(cap)
    return cap.summary()


@router.delete("/captures/{capture_id}")
def delete_capture(topology_id: int, capture_id: str):
    pc.delete(_cap(topology_id, capture_id))
    return {"ok": True}


@router.get("/captures/{capture_id}/packets/{number}")
def packet_detail(topology_id: int, capture_id: str, number: int):
    try:
        return pc.packet_detail(_cap(topology_id, capture_id), number)
    except pc.CaptureError as e:
        raise HTTPException(400, str(e))


@router.get("/captures/{capture_id}/match")
def match(topology_id: int, capture_id: str, filter: str):
    try:
        return {"numbers": pc.match(_cap(topology_id, capture_id), filter)}
    except pc.CaptureError as e:
        raise HTTPException(400, str(e))


@router.get("/captures/{capture_id}/download")
def download(topology_id: int, capture_id: str):
    cap = _cap(topology_id, capture_id)
    if not cap.path.exists():
        raise HTTPException(404, "The capture file is gone")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(cap.started))
    name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in cap.side.replace(" ", "_"))
    return FileResponse(cap.path, media_type="application/vnd.tcpdump.pcap",
                        filename=f"{name}-{stamp}.pcapng")


@ws_router.websocket("/captures/{capture_id}/stream")
async def stream(ws: WebSocket, topology_id: int, capture_id: str):
    """Rows as they arrive: {"rows": [...], "capture": summary} batches,
    starting with everything captured so far."""
    if not await _authenticate(ws):
        return
    await ws.accept()
    try:
        cap = pc.get(capture_id, topology_id)
    except KeyError:
        await ws.send_json({"error": "Capture not found"})
        await ws.close()
        return
    sent = 0            # absolute row index (dropped + position)
    last_state = None
    idle = 0
    try:
        while True:
            total = cap.dropped + len(cap.rows)
            if sent < cap.dropped:
                sent = cap.dropped
            if total > sent or cap.state != last_state:
                start = sent - cap.dropped
                rows = cap.rows[start:start + 5000]
                sent += len(rows)
                last_state = cap.state
                await ws.send_json({"rows": rows, "capture": cap.summary()})
                if len(rows) == 5000:
                    continue
            if cap.state != "running" and sent >= cap.dropped + len(cap.rows):
                break               # finished and everything sent
            idle += 1
            if idle % 40 == 0:      # notice a browser that went away
                await ws.send_json({"ping": True})
            await asyncio.sleep(0.25)
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass
