"""临时: 等后端起来 -> 重建 XAUUSD 模拟盘."""
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jose import jwt

sys.path.insert(0, ".")
from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
USER_ID = "dfbbf32b-c2a0-4f7d-b34c-d2e31ff28ea5"
token = jwt.encode(
    {"sub": USER_ID, "sid": str(uuid.uuid4()), "type": "access",
     "iat": datetime.now(timezone.utc),
     "exp": datetime.now(timezone.utc) + timedelta(hours=2),
     "iss": settings.TOTP_ISSUER},
    settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
)
H = {"Authorization": f"Bearer {token}"}
C = httpx.Client(timeout=60)
for _ in range(30):
    try:
        C.get(f"{BASE}/paper", headers=H)
        break
    except Exception:
        time.sleep(1)
print("backend up")
runs = C.get(f"{BASE}/paper", headers=H, params={"page": 1, "page_size": 20}).json()["items"]
src = next((t for t in runs if t["symbol"] == "XAUUSD" and t["status"] == "failed"), None)
if src:
    body = {
        "strategy_id": src["strategy_id"], "account_id": src["account_id"],
        "version": src["strategy_version"], "symbol": src["symbol"],
        "timeframe": src["timeframe"], "initial_deposit": src["initial_deposit"],
        "leverage": src["leverage"], "execution_mode": src["execution_mode"],
        "spread_mode": src["spread_mode"], "warmup_bars": src["warmup_bars"],
        "parameters": src["parameters"], "assumption_ack": True,
    }
    r = C.post(f"{BASE}/paper", headers=H, json=body)
    print("recreate:", r.status_code, (r.json().get("id") or "")[:8] if r.status_code == 201 else r.text[:200])
else:
    print("no failed XAUUSD run found")
