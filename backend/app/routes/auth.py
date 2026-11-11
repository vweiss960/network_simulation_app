from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.user import User
from app.auth.service import authenticate_user, create_access_token, hash_password
from app.auth.dependencies import get_current_user, require_admin
from app.auth.config import get_ldap_config

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: str | None = None
    display_name: str | None = None
    is_admin: bool | None = None


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    is_admin: bool
    is_local: bool
    auth_source: str

    class Config:
        from_attributes = True


@router.post("/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(body.username, body.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(data={"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
        },
    }


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return {
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


@router.get("/users", response_model=list[UserOut])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserOut)
def create_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        display_name=body.display_name or body.username,
        is_admin=body.is_admin,
        is_local=True,
        auth_source="local",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/ldap-status")
def ldap_status(admin: User = Depends(require_admin)):
    config = get_ldap_config()
    return {
        "enabled": config.get("enabled", False),
        "server": config.get("server", ""),
    }


@router.get("/ldap-test")
def ldap_test(admin: User = Depends(require_admin)):
    """Test connectivity to the configured LDAP/AD domain controller."""
    import socket
    import ssl as _ssl
    import ldap3

    config = get_ldap_config()
    if not config.get("enabled", False):
        return {"status": "disabled", "message": "LDAP is not enabled in ldap_config.yaml"}

    server_url = config.get("server", "")
    port = config.get("port", 389)
    use_ssl = config.get("use_ssl", False)
    ca_cert = config.get("ca_cert", "")
    results = {"server": server_url, "port": port, "use_ssl": use_ssl, "ca_cert": ca_cert or "(none - skip verify)"}

    # Extract hostname from URL
    hostname = server_url.replace("ldaps://", "").replace("ldap://", "").strip("/")

    # Step 1: DNS resolution
    try:
        ip = socket.gethostbyname(hostname)
        results["dns"] = {"status": "ok", "resolved_ip": ip}
    except socket.gaierror as e:
        results["dns"] = {"status": "fail", "error": str(e)}
        return {"status": "fail", "message": f"DNS resolution failed for {hostname}", **results}

    # Step 2: TCP connectivity
    try:
        sock = socket.create_connection((hostname, port), timeout=5)
        sock.close()
        results["tcp"] = {"status": "ok"}
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        results["tcp"] = {"status": "fail", "error": str(e)}
        return {"status": "fail", "message": f"TCP connection to {hostname}:{port} failed", **results}

    # Step 3: TLS certificate check (for LDAPS)
    if use_ssl:
        try:
            import os
            if ca_cert and os.path.exists(ca_cert):
                tls = ldap3.Tls(validate=_ssl.CERT_REQUIRED, ca_certs_file=ca_cert)
                results["tls"] = {"status": "ok", "mode": "verified", "ca_cert": ca_cert}
            else:
                tls = ldap3.Tls(validate=_ssl.CERT_NONE)
                results["tls"] = {"status": "ok", "mode": "unverified (CERT_NONE)", "warning": "No CA cert configured or file not found"}
        except Exception as e:
            results["tls"] = {"status": "fail", "error": str(e)}
            return {"status": "fail", "message": f"TLS setup failed: {e}", **results}
    else:
        tls = None

    # Step 4: LDAP bind test
    try:
        server = ldap3.Server(server_url, port=port, use_ssl=use_ssl, tls=tls, get_info=ldap3.ALL, connect_timeout=5)
        conn = ldap3.Connection(
            server,
            user=config.get("bind_dn", ""),
            password=config.get("bind_password", ""),
            auto_bind=True,
            receive_timeout=5,
        )
        server_info = str(server.info) if server.info else "connected"
        conn.unbind()
        results["ldap_bind"] = {"status": "ok", "info": server_info[:500]}
    except Exception as e:
        results["ldap_bind"] = {"status": "fail", "error": str(e)}
        return {"status": "fail", "message": f"LDAP bind failed: {e}", **results}

    return {"status": "ok", "message": "All checks passed", **results}
