"""Read Chainlink price feeds directly from Ethereum mainnet (JSON-RPC eth_call, no key).

The agent uses these onchain prices as an independent check on Gemini's spot price. If the two
disagree by more than the guardrail, the agent halts instead of trading on a bad price.
"""

from __future__ import annotations

import requests

RPC = "https://ethereum-rpc.publicnode.com"

# Chainlink aggregator proxies on Ethereum mainnet (8 decimals)
FEEDS = {
    "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
    "ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
    "SOL": "0x4ffC43a60e009B551865A93d232E33Fce9f01507",
}
LATEST_ROUND_DATA = "0xfeaf968c"  # latestRoundData()
DESCRIPTION = "0x7284e416"        # description()


def _call(to: str, data: str) -> str:
    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                 "params": [{"to": to, "data": data}, "latest"]}, timeout=15)
    r.raise_for_status()
    return r.json()["result"]


def _decode_string(hexdata: str) -> str:
    b = bytes.fromhex(hexdata[2:])
    n = int.from_bytes(b[32:64], "big")
    return b[64:64 + n].decode()


def chainlink_price(asset: str) -> dict | None:
    """{'price', 'updated_at', 'round_id', 'feed', 'description'} or None if there is no feed."""
    feed = FEEDS.get(asset)
    if feed is None:
        return None
    return {**decode_latest_round(_call(feed, LATEST_ROUND_DATA)), "feed": feed,
            "description": _decode_string(_call(feed, DESCRIPTION))}


def decode_latest_round(hexdata: str) -> dict:
    """Decode latestRoundData() → (roundId, answer, startedAt, updatedAt, answeredInRound); answer has 8 decimals."""
    raw = bytes.fromhex(hexdata[2:])
    round_id, answer, _, updated_at, _ = (int.from_bytes(raw[i:i + 32], "big", signed=True) for i in range(0, 160, 32))
    return {"price": answer / 1e8, "updated_at": updated_at, "round_id": round_id}
