from sqlalchemy import Column, Integer, String, ForeignKey
from app.models.database import Base


class SnmpCredential(Base):
    __tablename__ = "snmp_credentials"

    id = Column(Integer, primary_key=True, index=True)
    topology_id = Column(Integer, ForeignKey("topologies.id"), unique=True, nullable=False)
    name = Column(String, default="snmpv3_auth")  # auth block name in snmp.yml
    version = Column(Integer, default=3)  # 2 or 3
    # SNMPv2c
    community = Column(String, default="public")
    # SNMPv3
    security_level = Column(String, default="authPriv")  # noAuthNoPriv, authNoPriv, authPriv
    username = Column(String, default="")
    auth_password = Column(String, default="")
    auth_protocol = Column(String, default="SHA")  # MD5, SHA
    priv_password = Column(String, default="")
    priv_protocol = Column(String, default="AES")  # DES, AES
