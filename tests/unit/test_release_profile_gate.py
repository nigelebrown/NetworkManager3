"""Regression: installed live/lab-readonly must pass acceptance only with writes locked."""
from types import SimpleNamespace

from backend.cli import _acceptance_profile_safe


def _settings(profile, mode, *, lock=True, allow=False, permitted=False):
    return SimpleNamespace(inm_profile=profile, mode=mode, device_write_lock=lock,
                           allow_config_changes=allow, device_writes_permitted=permitted)


def test_locked_live_hardware_readonly_profile_is_accepted():
    assert _acceptance_profile_safe(_settings("lab-readonly", "live"))


def test_locked_demo_profile_is_accepted():
    assert _acceptance_profile_safe(_settings("demo", "demo"))


def test_unapproved_profile_and_write_unlocked_rejected():
    cases = [
        _settings("demo", "live"),
        _settings("lab-readonly", "demo"),
        _settings("standalone", "live"),
        _settings("lab-readonly", "live", lock=False),
        _settings("lab-readonly", "live", allow=True),
        _settings("lab-readonly", "live", permitted=True),
    ]
    for settings in cases:
        assert not _acceptance_profile_safe(settings)
