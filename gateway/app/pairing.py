"""Gateway pairing CLI (M17 配对码).

Run on the gateway host after installing the service:

    python -m app.pairing --server http://<backend>:8000 --code XXXX-XXXX

Exchanges the one-shot pairing code for the shared API key, registers this
host as a gateway node on the backend, and persists the key into
``gateway/.env`` (creating it when absent).  Uses stdlib ``urllib`` so the
installer carries no extra dependency.

Exit codes: 0 ok · 1 usage/network error · 2 pairing rejected (bad code etc.)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _default_env_path() -> Path:
    """gateway/.env in dev; next to the service exe when frozen (packaged
    layout: MT5Gateway\\MT5Pair\\MT5Pair.exe → gateway root is its parent)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.parent / ".env"
    return Path(__file__).resolve().parent.parent / ".env"


ENV_PATH = _default_env_path()
DEFAULT_TIMEOUT = 15.0


def pair(server: str, code: str, host: str, port: int, name: str,
         timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Call the backend pair endpoint; returns the PairOut payload dict."""
    url = server.rstrip("/") + "/api/v1/gateway/pair"
    body = json.dumps(
        {"code": code, "host": host, "port": port, "name": name}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        print(f"配对被拒绝（HTTP {exc.code}）：{detail}", file=sys.stderr)
        raise SystemExit(2) from None
    except OSError as exc:
        # Catches URLError (refused / DNS / timeout) *and* the bare OSError
        # subclasses urllib does not wrap.  urllib wraps failures from send()
        # but NOT from getresponse() — so a backend that drops the connection
        # mid-request (restart, firewall, RST) surfaces as a raw
        # ConnectionResetError/ConnectionAbortedError.  Those escaped this
        # handler and printed a traceback instead of the message below.
        reason = getattr(exc, "reason", exc)
        print(f"无法连接后端 {server}：{reason}", file=sys.stderr)
        raise SystemExit(1) from None


def write_env_key(api_key: str, env_path: Path = ENV_PATH) -> Path:
    """Persist GATEWAY_API_KEY into the gateway .env (idempotent)."""
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("GATEWAY_API_KEY="):
            lines[i] = f"GATEWAY_API_KEY={api_key}"
            replaced = True
            break
    if not replaced:
        lines.insert(0, f"GATEWAY_API_KEY={api_key}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.pairing",
        description="将本机网关与 MT5 量化平台后端配对绑定（一次性配对码）。",
    )
    parser.add_argument("--server", required=True, help="后端地址，如 http://192.168.1.10:8000")
    parser.add_argument("--code", required=True, help="网页上生成的 8 位配对码（XXXX-XXXX）")
    parser.add_argument("--gateway-host", default="127.0.0.1",
                        help="本网关对外地址（默认 127.0.0.1）")
    parser.add_argument("--gateway-port", type=int, default=8090, help="本网关端口（默认 8090）")
    parser.add_argument("--name", default="MT5 网关", help="节点显示名称")
    parser.add_argument("--env", default=str(ENV_PATH), help="gateway .env 路径")
    args = parser.parse_args(argv)

    print(f"正在与 {args.server} 配对 …")
    result = pair(args.server, args.code, args.gateway_host,
                  args.gateway_port, args.name)
    env_path = write_env_key(result["api_key"], Path(args.env))
    print("配对成功。")
    print(f"  节点 ID   : {result['node_id']}")
    print(f"  节点名称  : {result['name']}")
    print(f"  后端版本  : {result['backend_version']}")
    print(f"  密钥已写入: {env_path}")
    print("请重启网关服务使密钥生效（install_service.ps1 或手动重启）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
