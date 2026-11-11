from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy.orm import Session
import ldap3
from ldap3.core.exceptions import LDAPException
import ssl

from app.auth.config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_MINUTES, get_ldap_config
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


def authenticate_local(username: str, password: str, db: Session) -> User | None:
    """Try to authenticate against local user database."""
    user = db.query(User).filter(User.username == username, User.is_local == True).first()
    if user and user.hashed_password and verify_password(password, user.hashed_password):
        return user
    return None


def authenticate_ldap(username: str, password: str) -> dict | None:
    """Try to authenticate against LDAP/AD server. Returns user info dict on success."""
    config = get_ldap_config()
    if not config.get("enabled"):
        return None

    server_url = config.get("server", "")
    port = config.get("port", 636 if config.get("use_ssl", True) else 389)
    use_ssl = config.get("use_ssl", True)
    bind_dn = config.get("bind_dn", "")
    bind_password = config.get("bind_password", "")
    search_base = config.get("search_base", "")
    search_filter = config.get("search_filter", "(sAMAccountName={username})")
    user_attr = config.get("user_attr", "sAMAccountName")
    display_name_attr = config.get("display_name_attr", "displayName")

    if not server_url or not search_base:
        return None

    ca_cert = config.get("ca_cert", "")

    try:
        if use_ssl:
            if ca_cert:
                tls = ldap3.Tls(
                    validate=ssl.CERT_REQUIRED,
                    ca_certs_file=ca_cert,
                )
            else:
                tls = ldap3.Tls(validate=ssl.CERT_NONE)
        else:
            tls = None
        server = ldap3.Server(server_url, port=port, use_ssl=use_ssl, tls=tls, get_info=ldap3.NONE)

        # Bind with service account to search for user
        conn = ldap3.Connection(server, user=bind_dn, password=bind_password, auto_bind=True)
        user_filter = search_filter.replace("{username}", ldap3.utils.conv.escape_filter_chars(username))
        conn.search(search_base, user_filter, attributes=[user_attr, display_name_attr])

        if not conn.entries:
            conn.unbind()
            return None

        user_entry = conn.entries[0]
        user_dn = user_entry.entry_dn
        display_name = str(getattr(user_entry, display_name_attr, username))
        conn.unbind()

        # Attempt to bind as the user to verify password
        user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()

        return {
            "username": username,
            "display_name": display_name,
        }
    except (LDAPException, Exception):
        return None


def authenticate_user(username: str, password: str, db: Session) -> User | None:
    """Authenticate user: local first, then LDAP fallback."""
    # 1. Try local authentication
    local_user = authenticate_local(username, password, db)
    if local_user:
        return local_user

    # 2. Try LDAP authentication
    ldap_info = authenticate_ldap(username, password)
    if ldap_info:
        # Upsert LDAP user into local database
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.display_name = ldap_info.get("display_name", username)
            user.auth_source = "ldap"
            user.updated_at = datetime.utcnow()
        else:
            user = User(
                username=username,
                hashed_password=None,
                display_name=ldap_info.get("display_name", username),
                is_admin=False,
                is_local=False,
                auth_source="ldap",
            )
            db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return None
