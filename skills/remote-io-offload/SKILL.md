---
name: remote-io-offload
description: Offload I/O-heavy, idempotent batch work from slow local disks to healthy self-hosted VPS workers over Tailscale or SSH, with bounded staging, replica-aware failover, and execution receipts. Use for large archive scans, indexing, conversion, hashing, or dataset builds whose local storage is the bottleneck.
---

# Remote I/O Offload

Use this capability separately from the domain agent requesting the work. The domain agent defines inputs, command, outputs, and success checks; this skill selects and operates the remote worker.

## Dispatch

1. Inspect `tailscale status --json` and probe configured workers with `scripts/remote_io.py probe`.
2. Estimate source and expected output sizes before staging. Pass `--expected-output-bytes`; the dispatcher rejects a worker whose configured work filesystem cannot hold both plus logs and 20% headroom.
3. Use `scripts/remote_io.py run --replicas 2` for automatic failover. If fewer than two workers can stage the input, fail closed unless the user accepts `--allow-degraded`. As of 2026-09-10, only one configured worker meets the default 20 GiB reserve; two-replica dispatch is available for known-small jobs only when their explicit capacity estimate justifies a lower `--min-free-gib`.
4. Mark a command `--idempotent` only when repeating it with the same job key cannot corrupt data or duplicate an external action. Automatic failover after execution starts is forbidden otherwise.
5. Read the JSON receipt. Completion requires successful output retrieval and the caller's domain-specific verification.

The dispatcher is stateless: it uses SSH, `rsync`, and an ephemeral job directory. Inputs are staged before execution, outputs are fetched before cleanup, and no daemon or database is required. A per-worker `flock` queue serializes staging and execution so concurrent callers do not turn one physical disk into random-seek contention. Queue and execution deadlines are independent. A failed or interrupted job keeps its remote directory for recovery; successful jobs clean up by default.

## Scheduler boundary

Direct SSH dispatch is the operational backend until three healthy Nomad servers are reachable. Do not form a two-server Raft cluster. Read [references/nomad-ha.md](references/nomad-ha.md) before enabling the Nomad backend or changing a live Nomad server.

## Commands

```bash
python scripts/remote_io.py probe
python scripts/remote_io.py run --source /path/to/input --output /path/to/output --expected-output-bytes 1048576 --replicas 2 --idempotent --job-key stable-name -- command args
```

Use `--allow-degraded` only for a deliberate single-worker run. Keep secrets out of arguments and staged data unless the user explicitly authorizes transferring them.
