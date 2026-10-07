"""Fail-closed Linux process boundary for the trusted B05 renderer.

This stdlib-only launcher changes limits only in a fresh child, then execve's the
pinned executable. The async adapter still owns framed I/O, wall time, cancellation
and reaping. macOS raw-protocol tests are not OS sandbox acceptance.
"""

import argparse
import ctypes
import errno
import os
import platform
import stat
import sys
from pathlib import Path

CPU_SECONDS = 20
MEMORY_BYTES = 512 * 1024 * 1024
CONFIGURATION_EXIT = 78


class AnnotationProcessUnavailable(Exception):
    """A deployment configuration error; never permit an unsandboxed retry."""

    code = "annotation_sandbox_unavailable"
    retryable = False
    exit_code = 4

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{self.code}: {reason}")


def require_supported_host() -> str:
    if sys.platform != "linux":
        raise AnnotationProcessUnavailable("unsupported_host")
    if os.getuid() == 0 or os.geteuid() == 0:
        raise AnnotationProcessUnavailable("root_not_allowed")
    machine = platform.machine().lower()
    if machine not in {"x86_64", "aarch64", "arm64"}:
        raise AnnotationProcessUnavailable("unsupported_architecture")
    return "aarch64" if machine == "arm64" else machine


def launch_arguments(binary: Path, *, describe: bool = False) -> list[str]:
    """Return the fixed isolated launcher invocation; perform no parent limit changes."""
    require_supported_host()
    arguments = [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--binary", str(binary)]
    if describe:
        arguments.append("--describe")
    return arguments


class _Filter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint),
    ]


class _Program(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_Filter))]


def _linux_filter(machine: str) -> list[tuple[int, int, int, int]]:
    # Linux UAPI audit architectures and syscall numbers; foreign/x32 ABIs are killed
    # before matching native numbers. Deny io_uring as an alternative network path.
    if machine == "x86_64":
        arch, open_calls = 0xC000003E, [(2, 24), (257, 32)]
        denied = set(range(41, 56)) | {
            56,
            57,
            58,
            76,
            77,
            82,
            83,
            84,
            85,
            86,
            87,
            88,
            90,
            91,
            92,
            93,
            94,
            101,
            155,
            161,
            165,
            166,
            258,
            259,
            260,
            261,
            263,
            264,
            265,
            266,
            268,
            272,
            280,
            288,
            298,
            299,
            307,
            308,
            310,
            311,
            316,
            321,
            323,
        }
    else:
        arch, open_calls = 0xC00000B7, [(56, 32)]
        denied = set(range(198, 213)) | {
            33,
            34,
            35,
            36,
            37,
            38,
            39,
            40,
            41,
            45,
            46,
            51,
            52,
            53,
            54,
            55,
            88,
            97,
            117,
            220,
            241,
            242,
            243,
            268,
            269,
            270,
            271,
            276,
            280,
            282,
        }
    denied |= {
        424,
        425,
        426,
        427,
        434,
        435,
        437,
        438,
        452,
    }  # io_uring, pidfd, clone3, openat2, fchmodat2
    load, equal, bit_set, ret = 0x20, 0x15, 0x45, 0x06
    deny, kill, allow = 0x00050000 | errno.EPERM, 0x80000000, 0x7FFF0000
    instructions = [(load, 0, 0, 4), (equal, 1, 0, arch), (ret, 0, 0, kill), (load, 0, 0, 0)]
    if machine == "x86_64":
        instructions.extend([(bit_set, 0, 1, 0x40000000), (ret, 0, 0, kill)])
    for number in sorted(denied):
        instructions.extend([(equal, 0, 1, number), (ret, 0, 0, deny)])
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    write_flags |= getattr(os, "O_TMPFILE", 0) & ~os.O_DIRECTORY
    for number, argument_offset in open_calls:
        instructions.extend(
            [
                (equal, 0, 3, number),
                (load, 0, 0, argument_offset),
                (bit_set, 0, 1, write_flags),
                (ret, 0, 0, deny),
                (load, 0, 0, 0),
            ]
        )
    instructions.append((ret, 0, 0, allow))
    return instructions


def _install_seccomp(machine: str) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        if prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            raise AnnotationProcessUnavailable("no_new_privs_unavailable")
        values = _linux_filter(machine)
        array = (_Filter * len(values))(*(_Filter(*item) for item in values))
        program = _Program(len(values), array)
        address = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
        if address is None or prctl(22, 2, address, 0, 0) != 0:  # PR_SET_SECCOMP, FILTER
            raise AnnotationProcessUnavailable("seccomp_unavailable")
    except (AttributeError, OSError, ValueError) as exc:
        raise AnnotationProcessUnavailable("seccomp_unavailable") from exc


def enter_linux_sandbox() -> None:
    """Set non-increasable limits and deny network/fork/write syscalls in this child."""
    machine = require_supported_host()
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_FSIZE, (40 * 1024 * 1024, 40 * 1024 * 1024))
    except (OSError, ValueError) as exc:
        raise AnnotationProcessUnavailable("resource_limits_unavailable") from exc
    _install_seccomp(machine)


def _protected_binary(path: str) -> int:
    # Walk each directory through pinned descriptors. A writable ancestor or symlink
    # would let the worker replace a supposedly trusted executable after admission.
    supplied = Path(path)
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise AnnotationProcessUnavailable("unsafe_binary")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in supplied.parts[1:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
            info = os.fstat(directory)
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise AnnotationProcessUnavailable("unsafe_binary")
        descriptor = os.open(
            supplied.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
        )
        info = os.fstat(descriptor)
        if (
            info.st_uid != 0
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o6022
            or not info.st_mode & 0o111
        ):
            os.close(descriptor)
            raise AnnotationProcessUnavailable("unsafe_binary")
        return descriptor
    except OSError as exc:
        raise AnnotationProcessUnavailable("unsafe_binary") from exc
    finally:
        os.close(directory)


def _fixed_streams_and_fds(keep: int) -> None:
    if any(not stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (0, 1, 2)):
        raise AnnotationProcessUnavailable("unsafe_streams")
    try:
        descriptors = os.listdir("/proc/self/fd")
        if len(descriptors) > 4096:
            raise AnnotationProcessUnavailable("descriptor_limit")
        for item in descriptors:
            descriptor = int(item)
            if descriptor > 2 and descriptor != keep:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise
    except OSError as exc:
        raise AnnotationProcessUnavailable("descriptor_isolation_unavailable") from exc


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--describe", action="store_true")
    try:
        options = parser.parse_args(arguments)
        require_supported_host()
        descriptor = _protected_binary(options.binary)
        _fixed_streams_and_fds(descriptor)
        enter_linux_sandbox()
        target = [options.binary, "--annotation-describe"] if options.describe else [options.binary]
        # The inode is pinned through the exec boundary; no shell or output path exists.
        os.execve(f"/proc/self/fd/{descriptor}", target, {"LANG": "C", "LC_ALL": "C"})
    except AnnotationProcessUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return CONFIGURATION_EXIT
    except OSError:
        print("annotation_sandbox_unavailable: execve_unavailable", file=sys.stderr)
        return CONFIGURATION_EXIT
    return CONFIGURATION_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
