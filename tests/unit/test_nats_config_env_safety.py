"""NATS environment interpolation startup regression (before any Docker builds).

NATS parses unquoted $PASSWORD references; passwords beginning with numbers
can be mistaken for numeric config tokens and abort the whole compose stack.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_ci_generated_nats_credentials_are_parser_safe():
    text = (ROOT / ".github/workflows/phase1-ci.yml").read_text(encoding="utf-8")
    assert '"INM_NATS_API_PASSWORD": "nats" + secrets.token_hex(24),' in text
    assert '"INM_NATS_WORKER_PASSWORD": "nats" + secrets.token_hex(24),' in text


def test_centos_installer_nats_credentials_start_with_letter_and_remain_random():
    text = (ROOT / "deployment/centos9/common.sh").read_text(encoding="utf-8")
    assert "INM_NATS_API_PASSWORD=nats$(rand 40)" in text
    assert "INM_NATS_WORKER_PASSWORD=nats$(rand 40)" in text
    assert 'if [[ -f "$ENV_FILE" ]]; then' in text  # preserve existing deployed credentials
