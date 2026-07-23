import ipaddress
import os
import sys


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_loopback_host(host):
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def server_config(environ=None):
    environ = os.environ if environ is None else environ
    host = str(environ.get("FOOTBALL_HOST", "127.0.0.1")).strip()
    try:
        port = int(environ.get("FOOTBALL_PORT", "5000"))
    except (TypeError, ValueError) as exc:
        raise ValueError("FOOTBALL_PORT 必须是有效端口") from exc
    if not 1 <= port <= 65535:
        raise ValueError("FOOTBALL_PORT 必须在 1 到 65535 之间")
    if not _is_loopback_host(host) and not environ.get("FOOTBALL_ADMIN_TOKEN"):
        raise RuntimeError("非回环地址启动时必须配置 FOOTBALL_ADMIN_TOKEN")
    return host, port


def main():
    os.chdir(ROOT_DIR)
    if ROOT_DIR not in sys.path:
        sys.path.insert(0, ROOT_DIR)

    from web.app import app, _init_models

    host, port = server_config()
    print("Initializing models...")
    _init_models()
    print("Models ready.")

    print(f"Starting on http://{host}:{port}")
    app.run(debug=False, host=host, port=port)


if __name__ == "__main__":
    main()
