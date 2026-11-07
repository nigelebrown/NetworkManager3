"""Helpers that limit exposure of client identifiers."""


def mask_mac(mac: str) -> str:
    """Keep only the vendor (OUI) part: 'aa:bb:cc:xx:xx:xx'."""
    parts = mac.split(":")
    return ":".join(parts[:3] + ["xx", "xx", "xx"]) if len(parts) == 6 else mac
