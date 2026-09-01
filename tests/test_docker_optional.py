"""Optional Docker files must not be a back door for secrets. spec §10.C"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerignore_keeps_env_and_vendor_out_of_the_build_context():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in (".env", "vendor", ".venv"):
        assert pattern in text, f".dockerignore missing {pattern}"


def test_dockerfile_does_not_copy_dotenv():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in text
    assert "FR_SECRET_KEY=" not in text


def test_compose_does_not_bind_http_to_all_interfaces_by_default():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1" in text
    assert "0.0.0.0:8765" not in text
