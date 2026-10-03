"""Isolation acceptance subset against the real gVisor sandbox node (contract items 3, 4, 6, 7).

Requires the Colima profile `bsx` from docs/guides/sandbox-runtime.md, the caller's mTLS
environment, a canary written to /etc/bid-sandbox-canary and /home/bidsbx/canary inside the VM
(its value in data/work/sandbox-verification/canary.txt), a VM listener writing
/run/bid-egress-hits.json and a host listener writing host-hits.json in the output directory.
Run from the repository root:
  PYTHONPATH=server .venv/bin/python scripts/sandbox_colima_acceptance.py
Writes results.json to data/work/sandbox-verification/acceptance. Every case reports pass/fail
with its evidence; it is a verification driver, not a production entrypoint.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.providers.browser import SocketBrowserProvider
from app.providers.sandbox_runtime import RunDescriptor, RuntimeConfig, SandboxFailure

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "data/work/sandbox-verification/acceptance"
CANARY = (ROOT / "data/work/sandbox-verification/canary.txt").read_text().strip()
COLIMA = ["/opt/homebrew/bin/colima", "ssh", "-p", "bsx", "--"]
ENV = {
    **os.environ,
    "COLIMA_HOME": str(ROOT / "data/work/bsx"),
    "PATH": "/opt/homebrew/bin:" + os.environ["PATH"],
}


def vm(*command: str) -> str:
    done = subprocess.run([*COLIMA, *command], capture_output=True, text=True, env=ENV, timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"VM command failed: {command[:3]} {done.stderr[-200:]}")
    return done.stdout.strip()


def inventory() -> list[str]:
    output = vm(
        "sudo", "docker", "ps", "-a", "--filter", "label=bid.sandbox=1", "--format", "{{.Names}}"
    )
    return [line for line in output.splitlines() if line]


def egress_hits() -> dict:
    vm_hits = json.loads(vm("cat", "/run/bid-egress-hits.json") or "{}")
    host_hits = json.loads((HERE / "host-hits.json").read_text())
    return {"vm": vm_hits, "host": host_hits}


def descriptor(html: bytes, wall_ms: int | None = None) -> RunDescriptor:
    return RunDescriptor(
        uuid4(),
        uuid4(),
        uuid4(),
        hashlib.sha256(html).hexdigest(),
        "prototype_offline",
        budget_wall_ms=wall_ms,
        budget_cpu_ms=wall_ms,
    )


async def render(provider, html: str, wall_ms: int | None = None):
    data = html.encode()
    started = time.monotonic()
    try:
        result = await provider.render_prototype(descriptor(data, wall_ms), data)
        return {"ok": True, "seconds": round(time.monotonic() - started, 2), "result": result}
    except SandboxFailure as exc:
        return {
            "ok": False,
            "code": exc.code,
            "cleanup": getattr(exc, "cleanup_state", None),
            "seconds": round(time.monotonic() - started, 2),
        }


FILE_ATTACK = """<!doctype html><html><body><pre id="out">start</pre>
<iframe src="file:///etc/bid-sandbox-canary"></iframe>
<iframe src="file:///home/bidsbx/canary"></iframe>
<iframe src="file:///proc/self/environ"></iframe>
<img src="file:///etc/passwd">
<script>
const out = document.getElementById("out");
const targets = ["file:///etc/bid-sandbox-canary", "file:///home/bidsbx/canary",
  "file:///proc/self/environ", "file:///proc/1/environ", "file:///etc/hostname",
  "file:///var/run/docker.sock", "file:///run/bid-sandbox/control.sock"];
for (const t of targets) {
  try { const x = new XMLHttpRequest(); x.open("GET", t, false); x.send();
        out.textContent += "\\n" + t + " => " + x.responseText.slice(0, 200); }
  catch (e) { out.textContent += "\\n" + t + " blocked " + e.name; }
}
for (const f of document.querySelectorAll("iframe")) {
  try { out.textContent += "\\niframe => " + f.contentDocument.body.innerText.slice(0, 200); }
  catch (e) { out.textContent += "\\niframe blocked " + e.name; }
}
</script></body></html>"""

NET_ATTACK = """<!doctype html><html><head>
<link rel="stylesheet" href="http://192.168.5.1:18080/a.css">
<link rel="stylesheet" href="http://192.168.5.2:18081/b.css">
<link rel="preload" as="font" href="https://192.168.5.1:18443/f.woff2" crossorigin>
<script src="http://172.17.0.1:18080/c.js"></script>
</head><body>
<img src="http://192.168.5.1:18080/i.png"><img src="http://192.168.5.2:18081/i.png">
<img src="http://bid-egress.test:18080/dns.png"><img src="http://169.254.169.254/latest/meta-data/">
<iframe src="http://192.168.5.1:18080/frame"></iframe>
<script>
const urls = ["http://192.168.5.1:18080/x", "http://192.168.5.2:18081/x", "http://172.17.0.1:18080/x",
  "https://192.168.5.1:18443/x", "http://[::1]:18080/x", "http://127.0.0.1:18080/x"];
for (const u of urls) {
  try { fetch(u, {mode: "no-cors"}).catch(() => {}); } catch (e) {}
  try { navigator.sendBeacon(u, "beacon"); } catch (e) {}
  try { new EventSource(u); } catch (e) {}
  try { const x = new XMLHttpRequest(); x.open("GET", u, false); x.send(); } catch (e) {}
}
try { new WebSocket("ws://192.168.5.1:18080/ws"); } catch (e) {}
try { new WebSocket("ws://192.168.5.2:18081/ws"); } catch (e) {}
try {
  const pc = new RTCPeerConnection({iceServers: [{urls: "stun:192.168.5.1:18053"}]});
  pc.createDataChannel("x"); pc.createOffer().then(o => pc.setLocalDescription(o));
} catch (e) {}
try { navigator.serviceWorker.register("http://192.168.5.1:18080/sw.js"); } catch (e) {}
try { window.open("http://192.168.5.2:18081/popup"); } catch (e) {}
document.body.insertAdjacentHTML("beforeend", "<p>network attack page</p>");
</script></body></html>"""

CPU_LOOP = "<!doctype html><html><body><script>while (true) {}</script></body></html>"
MEMORY_BOMB = """<!doctype html><html><body><script>
const hoard = []; while (true) { hoard.push(new Float64Array(16 * 1024 * 1024).fill(1)); }
</script></body></html>"""
BASELINE = "<!doctype html><html><body><h1>baseline after attack</h1></body></html>"


async def main():
    provider = SocketBrowserProvider(RuntimeConfig.from_env())
    config = RuntimeConfig.from_env()
    results: dict[str, Any] = {"runtime": config.runtime, "image": config.image}

    def record(name, passed, **evidence):
        results[name] = {"pass": bool(passed), **evidence}
        print(name, "PASS" if passed else "FAIL", json.dumps(evidence, default=str)[:300])

    outcome = await render(provider, FILE_ATTACK)
    dom = b""
    if outcome["ok"]:
        dom = next(a.data for a in outcome["result"].artifacts if a.kind == "rendered_html")
    text = dom.decode(errors="replace")
    record(
        "file_and_secret_boundary",
        outcome["ok"] and CANARY not in text and "BID_SANDBOX" not in text and "DOCKER" not in text,
        rendered=outcome["ok"],
        canary_found=CANARY in text,
        env_names_found="BID_SANDBOX" in text,
        inventory_after=inventory(),
    )

    before = egress_hits()
    outcome = await render(provider, NET_ATTACK)
    await asyncio.sleep(3)
    after = egress_hits()
    record(
        "zero_network_egress",
        before == after,
        rendered=outcome["ok"],
        issues=list(outcome["result"].issues) if outcome["ok"] else outcome.get("code"),
        hits_before=before,
        hits_after=after,
    )

    outcome = await render(provider, CPU_LOOP, wall_ms=10_000)
    record(
        "cpu_loop_stops_at_deadline",
        not outcome["ok"] and outcome["seconds"] < 30 and not inventory(),
        **{k: v for k, v in outcome.items() if k != "result"},
        inventory_after=inventory(),
    )

    outcome = await render(provider, MEMORY_BOMB, wall_ms=30_000)
    record(
        "memory_bomb_is_contained",
        not outcome["ok"] and not inventory(),
        **{k: v for k, v in outcome.items() if k != "result"},
        inventory_after=inventory(),
        vm_memory=vm("free", "-m").splitlines()[1] if vm("free", "-m") else None,
    )

    outcome = await render(provider, BASELINE)
    record("healthy_after_exhaustion", outcome["ok"], seconds=outcome["seconds"])

    task = asyncio.create_task(render(provider, CPU_LOOP, wall_ms=30_000))
    await asyncio.sleep(4)
    running = inventory()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    cleared_after = None
    for second in range(1, 16):
        await asyncio.sleep(1)
        if not inventory():
            cleared_after = second
            break
    record(
        "caller_disconnect_cleans_up",
        bool(running) and cleared_after is not None and cleared_after <= 12,
        running_during=running,
        cleared_after_seconds=cleared_after,
    )

    task = asyncio.create_task(render(provider, CPU_LOOP, wall_ms=30_000))
    await asyncio.sleep(4)
    running = inventory()
    vm("sudo", "systemctl", "restart", "bid-sandbox-supervisor")
    outcome = await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(5)
    after_restart = inventory()
    baseline = await render(provider, BASELINE)
    record(
        "supervisor_restart_reaps_orphans",
        bool(running) and not after_restart and baseline["ok"],
        running_during=running,
        inventory_after_restart=after_restart,
        interrupted_run=str(outcome[0])[:120],
        baseline_after=baseline["ok"],
    )

    results["all_pass"] = all(v["pass"] for v in results.values() if isinstance(v, dict))
    (HERE / "results.json").write_text(json.dumps(results, indent=2, default=str))
    print("ALL_PASS" if results["all_pass"] else "SOME_FAILED")


asyncio.run(main())
