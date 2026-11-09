"""Device configuration writers (Phase 6). Only the change executor may call `apply`/`rollback`.

Every writer implements the same contract:
  render(op)      -> exact request(s)/commands that WOULD be sent (shown to approvers, no I/O)
  backup()        -> full running configuration (bytes) for the vault
  checkpoint()    -> device-side restore point id (or a captured prior-state token)
  apply(op)       -> performs ONE validated typed operation
  readback(op)    -> (verified: bool, observed: dict) read from the device after apply
  rollback(cp)    -> restores the checkpoint; must be followed by a readback of the whole config
  save()          -> persists running -> startup where the platform separates them
"""
from __future__ import annotations

from dataclasses import dataclass, field


class WriterError(Exception):
    """A device refused or failed an operation. `category` feeds the execution record."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


@dataclass
class Credentials:
    host: str
    username: str
    password: str
    ssh_fingerprint: str | None = None
    port: int | None = None
    extra: dict = field(default_factory=dict)
