import os
import yaml

# JWT settings from environment
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "phanero-dev-secret-change-me")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", "480"))

# Admin user from environment
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# LDAP config from YAML file
LDAP_CONFIG_PATH = os.environ.get("LDAP_CONFIG_PATH", "/app/app/auth/ldap_config.yaml")


def load_ldap_config() -> dict:
    """Load LDAP configuration from YAML file."""
    try:
        with open(LDAP_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}
        return config
    except FileNotFoundError:
        return {"enabled": False}
    except Exception:
        return {"enabled": False}


def get_ldap_config() -> dict:
    """Get current LDAP config (re-reads file each time for live updates)."""
    return load_ldap_config()
