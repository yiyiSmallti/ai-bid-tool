"""Process-boundary failure cases defined before the B05 launcher implementation.

Fail closed on root, unsupported host/architecture, unsafe binary ownership or
streams, unavailable privilege/seccomp controls and failed resource limits. A real
Linux child must observe zero core dumps, one process, 20s CPU and 512MiB allocation
limits; socket/fork/io_uring creation must fail without contacting any service.
On macOS only explicit unsupported-host rejection is verified. Raw Rust protocol
checks are separate and do not prove operating-system containment.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).parents[1] / "app/providers/annotation_process.py"
LINUX_NONROOT = sys.platform == "linux" and os.geteuid() != 0


def test_unsupported_host_or_root_never_executes_binary(tmp_path):
    if LINUX_NONROOT:
        pytest.skip("Real Linux sandbox probes cover the supported non-root host")
    output = subprocess.run(
        [sys.executable, "-I", "-S", str(HELPER), "--binary", "/usr/bin/true"],
        input=b"synthetic protocol",
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert output.returncode == 78
    assert output.stdout == b""
    assert b"annotation_sandbox_unavailable" in output.stderr
    assert b"unsupported_host" in output.stderr or b"root_not_allowed" in output.stderr
    (tmp_path / "sandbox-rejection.json").write_text(
        json.dumps(
            {
                "platform": sys.platform,
                "exit_code": output.returncode,
                "diagnostic": output.stderr.decode().strip(),
                "no_output": True,
            }
        )
    )


@pytest.mark.skipif(not LINUX_NONROOT, reason="Requires non-root Linux seccomp runtime")
def test_linux_real_child_network_fork_and_memory_limits(tmp_path):
    # Execute the real guard in a fresh process, then probe denied syscalls. No socket
    # reaches a network: creation itself must return EPERM before any connect occurs.
    script = f"""
import ctypes, errno, importlib.util, json, mmap, os, resource, sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('annotation_process',{str(HELPER)!r})
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.enter_linux_sandbox()
limits={{name:resource.getrlimit(getattr(resource,'RLIMIT_'+name)) for name in ['CPU','AS','CORE','NPROC']}}
libc=ctypes.CDLL(None,use_errno=True)
network=libc.socket(2,1,0);network_errno=ctypes.get_errno()
io_uring=libc.syscall(425,1,0);io_uring_errno=ctypes.get_errno()
fork=libc.fork();fork_errno=ctypes.get_errno()
if fork==0:os._exit(99)
try:mmap.mmap(-1,512*1024*1024);memory_denied=False
except (MemoryError,OSError):memory_denied=True
print(json.dumps({{'limits':limits,'network_result':network,'network_errno':network_errno,'fork_result':fork,'fork_errno':fork_errno,'io_uring_result':io_uring,'io_uring_errno':io_uring_errno,'memory_denied':memory_denied}}))
"""
    output = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script],
        input=b"",
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert output.returncode == 0, output.stderr.decode()
    value = json.loads(output.stdout)
    assert value["limits"] == {
        "CPU": [20, 20],
        "AS": [536870912, 536870912],
        "CORE": [0, 0],
        "NPROC": [1, 1],
    }
    assert value["network_result"] == value["fork_result"] == -1
    assert value["network_errno"] == value["fork_errno"] == 1
    assert value["io_uring_result"] == -1
    assert value["io_uring_errno"] == 1
    assert value["memory_denied"] is True
    (tmp_path / "linux-process-boundary.json").write_text(json.dumps(value, indent=2))


@pytest.mark.skipif(not LINUX_NONROOT, reason="Requires non-root Linux seccomp runtime")
def test_linux_rejects_user_writable_binary(tmp_path):
    binary = tmp_path / "unsafe-renderer"
    binary.write_bytes(b"synthetic non-executable fixture")
    binary.chmod(0o755)
    output = subprocess.run(
        [sys.executable, "-I", "-S", str(HELPER), "--binary", str(binary)],
        input=b"",
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert output.returncode == 78
    assert output.stdout == b""
    assert b"unsafe_binary" in output.stderr


@pytest.mark.skipif(not LINUX_NONROOT, reason="Requires non-root Linux seccomp runtime")
def test_linux_execve_preserves_pipes_with_protected_binary(tmp_path):
    binary = Path("/usr/bin/true").resolve()
    output = subprocess.run(
        [sys.executable, "-I", "-S", str(HELPER), "--binary", str(binary)],
        input=b"",
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert output.returncode == 0, output.stderr.decode()
    assert output.stdout == b""
    assert output.stderr == b""
    (tmp_path / "execve.json").write_text(
        json.dumps({"exit_code": 0, "stdout_bytes": 0, "stderr_bytes": 0})
    )
