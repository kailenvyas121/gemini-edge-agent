"""Run the agent from the terminal.

    python run.py            # one scan, prints the agent's paper orders
    python run.py --loop 5   # scan every 5 minutes until stopped
"""

import argparse
import time

from edge import agent


def scan() -> None:
    rep = agent.run_once()
    orders = [d for d in rep.decisions if d.action != "hold"]
    print(f"\n{rep.started_at}  priced {len(rep.decisions)} markets, {len(orders)} paper orders, "
          f"${rep.committed_usd} of budget committed")
    for a, o in rep.oracle.items():
        cl = f"Chainlink ${o['chainlink']:,.4f} ({o['divergence_bp']} bp apart)" if o.get("chainlink") else "no Chainlink feed"
        print(f"  {a}: Gemini ${o['gemini_spot']:,.4f} · {cl}" + (f" · BLOCKED: {rep.blocked[a]}" if a in rep.blocked else ""))
    for d in orders:
        print(f"  {d.action:8s} {d.title} | {d.label:20s} fair {d.fair:.2f} bid {d.bid} ask {d.ask} "
              f"edge {100 * d.edge:+.1f}¢ stake ${d.size_usd}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--loop", type=float, help="minutes between scans")
    a = p.parse_args()
    while True:
        scan()
        if not a.loop:
            break
        time.sleep(60 * a.loop)
