from backend.infrastructure.writers.emulator import CxEmulator


def test_read_cx_running_and_alternate_image_without_writes():
    emu = CxEmulator(model="CX 6200F", firmware="ML.10.14.1000")
    w = emu.writer()
    try:
        w.open()
        result = w.firmware_status()
        assert result["current_version"] == "ML.10.14.1000"
        assert result["primary_version"] == "ML.10.14.1000"
        assert result["secondary_version"] is None
        assert result["default_image"] == result["booted_image"] == "primary"
        assert result["state"] == "COLLECTED"
        assert emu.writes == 0
    finally:
        w.close()
