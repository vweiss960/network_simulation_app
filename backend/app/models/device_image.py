"""DeviceImage — uploaded .qcow2 VM images that get wrapped into vrnetlab
Docker images for use in lab deploys.

Lifecycle of a row:
    uploaded → building → built     (happy path)
    uploaded → building → failed    (vrnetlab make exited non-zero)
    uploaded → (Build never clicked) — left as-is

The qcow file itself lives at QCOW_DIR/<filename>; the build log lives at
QCOW_LOG_DIR/<id>.log. `built_image_tag` is the Docker image reference
produced by `make docker-image` (e.g. vrnetlab/vr-csr:17.03.06) — that's
what NodeTemplate.image_ref ends up pointing at.
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, func
from app.models.database import Base


class DeviceImage(Base):
    __tablename__ = "device_images"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)   # e.g. csr1000v-universalk9.17.03.06.qcow2
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String, default="")          # populated lazily after upload completes
    vendor = Column(String, default="")          # cisco / juniper / arista / …
    # vrnetlab "platform" — matches the subdir name under hellt/vrnetlab
    # (e.g. csr, vios, viosl2). Drives which Makefile we invoke.
    platform = Column(String, nullable=False)
    clab_kind = Column(String, default="")       # cisco_csr1000v / cisco_vios / …
    status = Column(String, default="uploaded")  # uploaded | building | built | failed
    built_image_tag = Column(String, default="") # vrnetlab/vr-csr:17.03.06
    build_log_path = Column(String, default="")  # /app/device_images/qcow_logs/<id>.log
    error_message = Column(Text, default="")
    uploaded_at = Column(DateTime, server_default=func.now())
    built_at = Column(DateTime, nullable=True)
