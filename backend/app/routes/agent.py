"""The lab agent's HTTP surface: status/models, a streaming chat (SSE), the audit log."""
import json
import queue
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.auth.dependencies import get_current_user
from app.models.agent_action import AgentAction
from app.models.agent_conversation import AgentConversation
from app.models.database import get_db
from app.models.topology import Topology
from app.services import lab_agent, llm

router = APIRouter(tags=["agent"])


@router.get("/api/ai/status")
def ai_status():
    models = llm.available_models()
    default = llm.DEFAULT_MODEL if any(m["id"] == llm.DEFAULT_MODEL for m in models) else (models[0]["id"] if models else llm.DEFAULT_MODEL)
    error = None if config.AI_ENABLED else "AI is turned off. Set PLABS_AI_ENABLED=1 in .env and recreate the backend."
    error = error or llm.config_error(default)
    return {"enabled": error is None, "error": error, "default_model": default, "models": models}


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    conversation_id: Optional[str] = None
    model: Optional[str] = None


@router.post("/api/topologies/{topology_id}/agent/chat")
def agent_chat(topology_id: int, body: ChatIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.get(Topology, topology_id):
        raise HTTPException(404, "Topology not found")
    if lab_agent.lesson_policy(topology_id) == "off":
        raise HTTPException(403, "AI assistance is off for this lesson")
    if not config.AI_ENABLED:
        raise HTTPException(400, "AI is turned off (PLABS_AI_ENABLED=0)")
    model = body.model or llm.DEFAULT_MODEL
    if model not in {m["id"] for m in llm.available_models()}:
        raise HTTPException(400, f"Model {model!r} isn't available. {llm.config_error(model) or ''}".strip())
    if body.conversation_id and db.get(AgentConversation, body.conversation_id):
        _own(db, topology_id, body.conversation_id, user)      # only its owner continues it
    username = getattr(user, "username", "")
    if body.conversation_id:
        _own_live(body.conversation_id, user)
    session = lab_agent.get_session(body.conversation_id, topology_id, model)
    session.owner = session.owner or username

    events: "queue.Queue[dict | None]" = queue.Queue()

    def worker():
        if not session.lock.acquire(blocking=False):
            # A new message while the assistant is working interrupts it: stop
            # the running request, wait for it to wind down, then run this one.
            session.cancel.set()
            events.put({"type": "status", "text": "Stopping the previous request"})
            if not session.lock.acquire(timeout=180):
                events.put({"type": "error", "message": "The previous request is still finishing a step "
                                                        "(e.g. applying a config); try again in a moment."})
                events.put(None)
                return
        # Follow this run (other tabs can attach too: …/stream). Registered
        # only now: an interrupted run's last events must not end this stream.
        lab_agent.listen(session, events)
        try:
            lab_agent.run_and_save(session, body.message, username)
        finally:
            lab_agent.broadcast(session, {"type": "done"})
            lab_agent.broadcast(session, None)
            session.lock.release()

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        yield _sse({"type": "conversation", "id": session.id, "model": model})
        while True:
            ev = events.get()
            if ev is None:
                return
            yield _sse(ev)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"


def _own_live(conversation_id: str, user) -> None:
    """An in-memory (possibly not yet saved) conversation belongs to its starter."""
    owner = lab_agent.owner_of(conversation_id)
    if owner and owner != user.username and not user.is_admin:
        raise HTTPException(404, "Conversation not found")


def _own(db: Session, topology_id: int, conversation_id: str, user) -> AgentConversation:
    c = db.get(AgentConversation, conversation_id)
    if not c or c.topology_id != topology_id or (c.username != user.username and not user.is_admin):
        raise HTTPException(404, "Conversation not found")
    return c


@router.get("/api/topologies/{topology_id}/agent/conversations")
def list_conversations(topology_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """This user's saved conversations in this lab, newest first."""
    q = (db.query(AgentConversation).filter(AgentConversation.topology_id == topology_id,
                                            AgentConversation.username == user.username)
         .order_by(AgentConversation.updated_at.desc()).limit(100))
    def running(cid: str) -> bool:
        live = lab_agent.live_session(cid)
        return bool(live and live.running)
    return [{"id": c.id, "title": c.title or "Untitled", "model": c.model,
             "updated_at": c.updated_at.isoformat() if c.updated_at else None, "running": running(c.id)} for c in q]


@router.get("/api/topologies/{topology_id}/agent/conversations/{conversation_id}")
def get_conversation(topology_id: int, conversation_id: str, user=Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """A conversation: the live transcript while it's in memory (it may be
    running), else the saved one."""
    live = lab_agent.live_session(conversation_id)
    if live and live.topology_id == topology_id:
        _own_live(conversation_id, user)
        return {"id": live.id, "title": live.title, "model": live.model,
                "messages": lab_agent.snapshot(live), "running": live.running}
    c = _own(db, topology_id, conversation_id, user)
    return {"id": c.id, "title": c.title, "model": c.model, "messages": json.loads(c.ui_json or "[]"),
            "running": False}


@router.get("/api/topologies/{topology_id}/agent/conversations/{conversation_id}/stream")
def attach_conversation(topology_id: int, conversation_id: str, user=Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Follow a conversation (SSE): a `snapshot` of the transcript so far,
    then the running request's events until it ends. Lets a reloaded page,
    or one that left the lab, pick a run back up."""
    live = lab_agent.live_session(conversation_id)
    if not live or live.topology_id != topology_id:
        c = _own(db, topology_id, conversation_id, user)
        msgs = json.loads(c.ui_json or "[]")

        def done():
            yield _sse({"type": "snapshot", "id": conversation_id, "model": c.model, "messages": msgs, "running": False})
            yield _sse({"type": "done"})
        return StreamingResponse(done(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    _own_live(conversation_id, user)
    events: "queue.Queue[dict | None]" = queue.Queue()
    msgs = lab_agent.listen(live, events)
    running = live.running

    def stream():
        try:
            yield _sse({"type": "snapshot", "id": live.id, "model": live.model, "messages": msgs, "running": running})
            if not running:
                yield _sse({"type": "done"})
                return
            while True:
                try:
                    ev = events.get(timeout=15)
                except queue.Empty:
                    if not live.running:            # ended while we weren't listening
                        yield _sse({"type": "done"})
                        return
                    yield ": keepalive\n\n"
                    continue
                if ev is None:
                    return
                yield _sse(ev)
        finally:
            lab_agent.unlisten(live, events)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/topologies/{topology_id}/agent/conversations/{conversation_id}/stop")
def stop_conversation(topology_id: int, conversation_id: str, user=Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Stop the assistant between steps (a tool already running finishes)."""
    if db.get(AgentConversation, conversation_id):
        _own(db, topology_id, conversation_id, user)
    _own_live(conversation_id, user)
    return {"ok": lab_agent.stop(conversation_id)}


@router.delete("/api/topologies/{topology_id}/agent/conversations/{conversation_id}")
def delete_conversation(topology_id: int, conversation_id: str, user=Depends(get_current_user),
                        db: Session = Depends(get_db)):
    c = db.get(AgentConversation, conversation_id)
    if c:
        _own(db, topology_id, conversation_id, user)
    _own_live(conversation_id, user)
    lab_agent.drop_session(conversation_id)
    if c:
        db.delete(c)
        db.commit()
    return {"ok": True}


@router.get("/api/topologies/{topology_id}/agent/actions")
def agent_actions(topology_id: int, limit: int = 100, db: Session = Depends(get_db)):
    q = (db.query(AgentAction).filter(AgentAction.topology_id == topology_id)
         .order_by(AgentAction.id.desc()).limit(min(limit, 500)))
    return [{"id": a.id, "tool": a.tool, "ok": a.ok, "summary": a.summary, "input": a.input_json,
             "username": a.username, "model": a.model, "conversation_id": a.conversation_id,
             "created_at": a.created_at.isoformat() if a.created_at else None} for a in q]
