import pytest

from run import server_config


def test_server_config_defaults_to_loopback():
    assert server_config({}) == ("127.0.0.1", 5000)


def test_non_loopback_host_requires_admin_token():
    with pytest.raises(RuntimeError, match="FOOTBALL_ADMIN_TOKEN"):
        server_config({"FOOTBALL_HOST": "0.0.0.0"})


def test_non_loopback_host_accepts_configured_token():
    assert server_config({
        "FOOTBALL_HOST": "0.0.0.0",
        "FOOTBALL_PORT": "5100",
        "FOOTBALL_ADMIN_TOKEN": "secret",
    }) == ("0.0.0.0", 5100)


@pytest.mark.parametrize("port", ["0", "65536", "invalid"])
def test_server_config_rejects_invalid_port(port):
    with pytest.raises(ValueError):
        server_config({"FOOTBALL_PORT": port})
