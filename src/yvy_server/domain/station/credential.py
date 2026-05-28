from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StationCredential:
    credential_id: int | None
    station_uuid: UUID
    key_version: int
    key_hmac_hex: str
    created_at: datetime | None = None
    deactivated_at: datetime | None = None
    reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.deactivated_at is None

    @property
    def key_bytes(self) -> bytes:
        return bytes.fromhex(self.key_hmac_hex)