#!/usr/bin/env python3
"""Stateless SSH dispatcher for staged, retry-safe remote I/O jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class Worker:
    name: str
    target: str
    work_root: str


def run(argv: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        return subprocess.CompletedProcess(argv, 124, stdout, stderr or "timed out")


def ssh(worker: Worker, script: str, connect_timeout: int, process_timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return run([
        "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}",
        worker.target, f"sh -lc {shlex.quote(script)}",
    ], timeout=process_timeout or connect_timeout + 5)


def load_workers(path: Path) -> list[Worker]:
    payload = json.loads(path.read_text())
    workers = []
    for item in payload.get("workers", []):
        if not item.get("enabled", True):
            continue
        root = str(item["work_root"])
        if not root.startswith("/") or any(c in root for c in "\n\r\0"):
            raise ValueError(f"unsafe work_root for {item.get('name')}")
        workers.append(Worker(str(item["name"]), str(item["target"]), root.rstrip("/")))
    if not workers:
        raise ValueError("no enabled workers")
    return workers


def probe(worker: Worker, timeout: int) -> dict[str, object]:
    started = time.monotonic()
    root = shlex.quote(worker.work_root)
    result = ssh(worker, f"test -d {root} && printf '%s\\n' \"$(hostname)\" && df -Pk {root} | awk 'NR == 2 {{print $4}}' && cat /proc/loadavg && command -v flock", timeout)
    elapsed = round((time.monotonic() - started) * 1000)
    lines = result.stdout.strip().splitlines()
    record: dict[str, object] = {
        "name": worker.name,
        "target": worker.target,
        "reachable": result.returncode == 0 and len(lines) >= 4,
        "latency_ms": elapsed,
    }
    if record["reachable"]:
        record.update(hostname=lines[0], free_bytes=int(lines[1]) * 1024, load_1m=float(lines[2].split()[0]), flock=lines[3])
    else:
        record["error"] = (result.stderr.strip() or f"ssh exit {result.returncode}")[-500:]
    return record


def source_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def job_key(source: Path, command: list[str], supplied: str | None) -> str:
    if supplied:
        if not SAFE_KEY.fullmatch(supplied):
            raise ValueError("job key must contain only letters, digits, dot, underscore, and hyphen")
        return supplied
    return uuid.uuid4().hex[:24]


def stage(worker: Worker, source: Path, key: str, connect_timeout: int, transfer_timeout: int) -> tuple[bool, str]:
    remote = f"{worker.work_root}/{key}"
    lock = shlex.quote(worker.work_root + "/.io.lock")
    prep = ssh(worker, f"flock {lock} sh -c {shlex.quote(f'umask 077; mkdir -p {remote}/input {remote}/output')}", connect_timeout)
    if prep.returncode:
        return False, prep.stderr.strip()
    src = str(source) + ("/" if source.is_dir() else "")
    result = run([
        "ionice", "-c3", "nice", "-n", "19", "rsync", "-a", "--partial",
        "--delete", "--protect-args", "-e", f"ssh -o BatchMode=yes -o ConnectTimeout={connect_timeout}",
        "--rsync-path", f"flock {shlex.quote(worker.work_root + '/.io.lock')} rsync",
        src, f"{worker.target}:{remote}/input/",
    ], timeout=transfer_timeout)
    return result.returncode == 0, result.stderr.strip()


def execute(worker: Worker, key: str, command: list[str], connect_timeout: int, queue_timeout: int, command_timeout: int) -> subprocess.CompletedProcess[str]:
    remote = f"{worker.work_root}/{key}"
    limited_command = f"timeout --signal=TERM --kill-after=30s {command_timeout}s {shlex.join(command)}"
    script = (
        f"cd {shlex.quote(remote)} && "
        f"REMOTE_IO_INPUT={shlex.quote(remote + '/input')} "
        f"REMOTE_IO_OUTPUT={shlex.quote(remote + '/output')} "
        + limited_command
        + f" > {shlex.quote(remote + '/stdout.pipe')} 2> {shlex.quote(remote + '/stderr.pipe')}; "
        + "status=$?; "
        + f"wait; tail -c 4000 {shlex.quote(remote + '/stdout.log')}; "
        + f"tail -c 4000 {shlex.quote(remote + '/stderr.log')} >&2; exit $status"
    )
    setup = f"rm -f {shlex.quote(remote + '/stdout.pipe')} {shlex.quote(remote + '/stderr.pipe')} && mkfifo {shlex.quote(remote + '/stdout.pipe')} {shlex.quote(remote + '/stderr.pipe')} || exit 70; tail -c 1048576 < {shlex.quote(remote + '/stdout.pipe')} > {shlex.quote(remote + '/stdout.log')} & tail -c 1048576 < {shlex.quote(remote + '/stderr.pipe')} > {shlex.quote(remote + '/stderr.log')} & "
    script = setup + script
    locked = f"flock -E 75 -w {queue_timeout} {shlex.quote(worker.work_root + '/.io.lock')} sh -lc {shlex.quote(script)}"
    return ssh(worker, locked, connect_timeout, queue_timeout + command_timeout + 40)


def fetch(worker: Worker, key: str, output: Path, connect_timeout: int, transfer_timeout: int) -> subprocess.CompletedProcess[str]:
    if output.exists() and any(output.iterdir() if output.is_dir() else [output]):
        return subprocess.CompletedProcess([], 64, "", "output destination must be absent or empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    remote = f"{worker.work_root}/{key}/output/"
    result = run([
        "rsync", "-a", "--partial", "--protect-args", "-e",
        f"ssh -o BatchMode=yes -o ConnectTimeout={connect_timeout}",
        "--rsync-path", f"flock {shlex.quote(worker.work_root + '/.io.lock')} rsync",
        f"{worker.target}:{remote}", str(temporary) + "/",
    ], timeout=transfer_timeout)
    if result.returncode == 0:
        if output.exists():
            output.rmdir()
        temporary.replace(output)
    else:
        shutil.rmtree(temporary)
    return result


def cleanup(worker: Worker, key: str, timeout: int) -> bool:
    remote = f"{worker.work_root}/{key}"
    lock = shlex.quote(worker.work_root + "/.io.lock")
    return ssh(worker, f"flock {lock} rm -rf -- {shlex.quote(remote)}", timeout).returncode == 0


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=here / "hosts.json")
    parser.add_argument("--connect-timeout", type=int, default=6)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("probe")
    check = sub.add_parser("self-check")
    check.set_defaults(self_check=True)
    launch = sub.add_parser("run")
    launch.add_argument("--source", required=True, type=Path)
    launch.add_argument("--output", required=True, type=Path)
    launch.add_argument("--replicas", type=int, default=2)
    launch.add_argument("--min-free-gib", type=float, default=20)
    launch.add_argument("--source-bytes", type=int, help="trusted source-size estimate; avoids a second directory walk")
    launch.add_argument("--expected-output-bytes", type=int, required=True)
    launch.add_argument("--allow-degraded", action="store_true")
    launch.add_argument("--idempotent", action="store_true")
    launch.add_argument("--job-key")
    launch.add_argument("--keep-remote", action="store_true")
    launch.add_argument("--queue-timeout", type=int, default=86400)
    launch.add_argument("--command-timeout", type=int, default=604800)
    launch.add_argument("--transfer-timeout", type=int, default=86400)
    launch.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.action == "self-check":
        assert SAFE_KEY.fullmatch("archive-index.v1")
        assert not SAFE_KEY.fullmatch("../unsafe")
        assert shlex.join(["printf", "%s", "a b"]) == "printf %s 'a b'"
        emit({"ok": True})
        return 0

    for binary in ("ssh", "rsync"):
        if shutil.which(binary) is None:
            parser.error(f"required executable not found: {binary}")
    workers = load_workers(args.config)
    with ThreadPoolExecutor(max_workers=min(8, len(workers))) as pool:
        probes = list(pool.map(lambda worker: probe(worker, args.connect_timeout), workers))
    if args.action == "probe":
        emit({"workers": probes})
        return 0 if any(item["reachable"] for item in probes) else 2

    source = args.source.resolve()
    if not source.exists():
        parser.error(f"source does not exist: {source}")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run requires a command after --")
    if args.replicas < 1:
        parser.error("replicas must be at least one")
    if args.idempotent and not args.job_key:
        parser.error("--idempotent requires a stable --job-key")
    if args.queue_timeout < 1 or args.command_timeout < 1 or args.transfer_timeout < 1:
        parser.error("queue, command, and transfer timeouts must be positive")

    size = args.source_bytes if args.source_bytes is not None else source_size(source)
    if size < 0 or args.expected_output_bytes < 0:
        parser.error("source and expected output bytes cannot be negative")
    required = max(int((size + args.expected_output_bytes + 2 * 1048576) * 1.2), int(args.min_free_gib * GIB))
    by_name = {worker.name: worker for worker in workers}
    eligible = [item for item in probes if item["reachable"] and int(item["free_bytes"]) >= required]
    eligible.sort(key=lambda item: (-int(item["free_bytes"]), float(item["load_1m"]), int(item["latency_ms"])))
    candidates = [by_name[str(item["name"])] for item in eligible]
    if len(candidates) < args.replicas and not args.allow_degraded:
        emit({"status": "blocked", "reason": "insufficient healthy replicas", "requested_replicas": args.replicas, "staged_replicas": 0, "degraded": True, "available": len(candidates), "source_bytes": size, "probes": probes})
        return 3
    if not candidates:
        emit({"status": "blocked", "reason": "no eligible worker", "requested_replicas": args.replicas, "staged_replicas": 0, "degraded": True, "source_bytes": size, "probes": probes})
        return 3

    key = job_key(source, command, args.job_key)
    storage_key = f"{key}.{uuid.uuid4().hex[:12]}"
    staged: list[Worker] = []
    stage_errors: dict[str, str] = {}
    for worker in candidates:
        ok, error = stage(worker, source, storage_key, args.connect_timeout, args.transfer_timeout)
        if ok:
            staged.append(worker)
            if len(staged) == args.replicas:
                break
        else:
            stage_errors[worker.name] = error[-500:]
    if len(staged) < args.replicas and not args.allow_degraded:
        emit({"status": "blocked", "reason": "replication failed", "job_key": key, "remote_storage_key": storage_key, "requested_replicas": args.replicas, "staged_replicas": len(staged), "degraded": True, "staged": [w.name for w in staged], "stage_errors": stage_errors})
        return 4

    attempts = []
    winner: Worker | None = None
    for index, worker in enumerate(staged):
        result = execute(worker, storage_key, command, args.connect_timeout, args.queue_timeout, args.command_timeout)
        attempts.append({"worker": worker.name, "exit_code": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]})
        if result.returncode == 0:
            winner = worker
            break
        if (not args.idempotent and result.returncode != 75) or index + 1 == len(staged):
            break
    if winner is None:
        emit({"status": "failed", "job_key": key, "remote_storage_key": storage_key, "requested_replicas": args.replicas, "staged_replicas": len(staged), "degraded": len(staged) < 2, "staged": [w.name for w in staged], "attempts": attempts, "remote_preserved": True})
        return 5

    fetch_errors = []
    for _ in range(3):
        retrieved = fetch(winner, storage_key, args.output.resolve(), args.connect_timeout, args.transfer_timeout)
        if retrieved.returncode == 0:
            break
        fetch_errors.append(retrieved.stderr[-1000:])
        time.sleep(1)
    if retrieved.returncode:
        emit({"status": "failed", "reason": "output retrieval failed", "job_key": key, "remote_storage_key": storage_key, "requested_replicas": args.replicas, "staged_replicas": len(staged), "degraded": len(staged) < 2, "winner": winner.name, "attempts": attempts, "fetch_errors": fetch_errors, "remote_preserved": True})
        return 6
    cleaned = {}
    if not args.keep_remote:
        cleaned = {worker.name: cleanup(worker, storage_key, args.connect_timeout) for worker in staged}
    degraded = len(staged) < 2
    emit({"status": "complete_degraded" if degraded else "complete", "job_key": key, "remote_storage_key": storage_key if args.keep_remote else None, "source_bytes": size, "expected_output_bytes": args.expected_output_bytes, "requested_replicas": args.replicas, "staged_replicas": len(staged), "degraded": degraded, "replicas": [w.name for w in staged], "winner": winner.name, "attempts": attempts, "output": str(args.output.resolve()), "cleanup": cleaned})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
