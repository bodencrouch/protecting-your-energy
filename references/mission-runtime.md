# Mission runtime

`scripts/mission_control.py` runs a private local queue using Python's standard
library, SQLite, Linux process identities and `flock`. It does not install a
scheduler. A parent process must arrange recurring bounded ticks. This is separate
from the existing RAM-only drafting route: mission task packets, selected source
text, prompts, model output, checkpoints and check evidence persist on disk.

## Submit and control work

Use an absolute script path from cron or systemd. Every public command returns
JSON; invalid input returns exit 2. A successful `tick` reports scheduling, not
mission completion. Inspect `status` and `events` for the outcome.

```sh
python3 /absolute/plugin/scripts/mission_control.py create --file /private/mission.json
python3 /absolute/plugin/scripts/mission_control.py list
python3 /absolute/plugin/scripts/mission_control.py status MISSION_ID
python3 /absolute/plugin/scripts/mission_control.py events MISSION_ID --after 0
python3 /absolute/plugin/scripts/mission_control.py pause MISSION_ID
python3 /absolute/plugin/scripts/mission_control.py tick --once --max-workers 2 --max-seconds 1800
python3 /absolute/plugin/scripts/mission_control.py resume MISSION_ID
python3 /absolute/plugin/scripts/mission_control.py cancel MISSION_ID
python3 /absolute/plugin/scripts/mission_control.py doctor
```

`pause` and `cancel` persist immediately. A running wrapper observes the change,
terminates only its own process group and retains evidence. A paused mission
cannot start a new attempt, including through another tick. Resume requires
cleanup to finish; it never resets the budget. Cancellation, completion and
exhaustion are terminal. Create a new authorized mission to continue exhausted
work; its `next_action` retains the unfinished step. A paused in-flight review
resumes that review rather than silently skipping it.

All commands accept `--state-dir /private/local/path` **before** the command.
By default state lives at
`$XDG_STATE_HOME/protect-your-energy/missions`, falling back to
`~/.local/state/protect-your-energy/missions`. The directory has mode 0700;
new files use umask 0077. Use a local filesystem supporting SQLite WAL and flock,
not a network share. No database or runtime log belongs in the project repository.

## Immutable task packet

Example input (replace the paths and criterion with the actual project contract):

```json
{
  "idempotency_key": "project-increment-2026-10-26",
  "goal": "Implement the authorized increment and verify its behavior",
  "project_dir": "/absolute/project",
  "resource_key": "/absolute/shared-resource",
  "task": "Concrete instructions, intended artifacts, and remaining work",
  "authorized_scope": "Only the named project files and local scratch checks",
  "forbidden_actions": ["Do not modify deployed services"],
  "context_paths": [
    {"path": "/private/acquired/selected-requirements.md", "citation": "source session, turn, and extraction location"}
  ],
  "source_receipts": [
    {"path": "/private/acquired/source-manifest.json", "disposition": "Acquired locally; selected requirements retained"}
  ],
  "acceptance_criteria": [
    {"id": "behavior", "description": "Describe observable behavior that proves the goal"}
  ],
  "checks": [
    {
      "id": "behavior-probe",
      "criteria": ["behavior"],
      "argv": ["/usr/bin/python3", "-c", "print('Replace with a meaningful inline behavior assertion')"],
      "timeout_seconds": 60,
      "expected_exit": 0,
      "artifacts": [{"path": "relative/output.py"}]
    }
  ],
  "execution_candidates": [{"backend": "codex", "profile": "worker"}],
  "limits": {
    "max_attempts": 12,
    "run_seconds": 900,
    "max_output_bytes": 2097152,
    "lease_seconds": 30,
    "retry_seconds": 10
  }
}
```

The example check is a placeholder, **not** a proof of implementation. Write
checks that assert the criterion against actual artifacts and behavior. Every
criterion must map to an immutable check containing argv or an expected SHA256
artifact hash. File existence alone cannot authorize completion. Artifact paths
must resolve inside the project; declared artifacts must remain unchanged during
review/checks. Artifact hashing accepts regular files up to 64 MiB each. The
caller authors and trusts these fixed argv checks: they can execute code and must
respect the same authorized scope. Model output cannot add or replace checks.
There is no shell interpolation or `shell=True`.

The absolute project directory is canonicalized. Identical project paths always
serialize, even if callers supply different resource keys. Identical resource
keys also serialize across different projects. Worktrees or projects sharing
other mutable assets must explicitly share a resource key. This is coordination
among this queue's missions; unrelated tools do not acquire its locks.

Acquire and inventory history **locally before submission**, using the existing
reconstruction tools. Pass only selected text files, at most 64 KiB per file,
128 KiB total; directories are rejected. Context is snapshotted with source path,
SHA256 and citation, and explicitly marked historical evidence rather than
current authorization. A source receipt can point to the full local coverage
manifest without loading its contents into a model. This runtime does not perform
archive-wide acquisition or infer permissions from history. The goal, task,
authorized scope, sources, acceptance criteria, candidates, limits and checks are
stored as an immutable packet with a hash. A changed contract needs a new mission.
Reusing an idempotency key with changed content fails.

## Execution and review

Default execution uses the installed `codex exec -p worker`, stdin for a plain
task, JSONL events, a JSON result schema and a last-message file. Installed help
was inspected on Codex 0.164.0. Execution selects `workspace-write`; an independent
fresh review selects `read-only`; both set `approval_policy="never"`. No blanket
sandbox bypass is used. Work requiring writable paths outside the project needs
a future explicit scoped adapter; the runtime does not silently broaden access.
Normal configured project/user rules remain active. The installed `worker`
profile currently disables plugins, hooks, apps and memories; this runtime does
not override those profile settings.

Each configured candidate may add `model` and `provider` (the latter names an
already installed Codex provider), and optionally another installed `profile`.
There are no invented providers, executable templates or automatic credential
changes. Doctor proves CLI/config presence only, not provider health. Execution
or malformed-result failures rotate through the configured candidates with
exponential backoff, capped at one hour. Attempts are finite across **both**
execution and review; each phase launch consumes one attempt. Fallback never
expands tool permissions. All candidate choices and run identities persist.

Execution session IDs are captured from observed `thread.started` events.
Continuation on the same candidate uses `codex exec resume EXACT_ID -` with
cwd set to the project and documented config overrides. Resume help has no
`-p`, `-C` or `-s`; it inherits the existing session's profile. A failed execution
clears the mission's resumable pointer, retaining the observed ID on its run;
the next run starts fresh using the last valid checkpoint and packet. A provider
change also starts fresh. Reviews always start a fresh session. No `--last`,
ephemeral session or fabricated checkpoint/session ID is used.

Exit zero only permits structured-result validation. Schema, mission ID, run ID,
phase, verdict and one assessment per criterion must match the current run.
Malformed output remains in evidence and causes a bounded retry. An execution
result of `incomplete` queues further execution with the saved next action. A
`ready` result queues review; it cannot mark completion. Independent review must
inspect actual artifacts and supply criterion evidence. Fixed checks then run
against those artifacts, and recorded hashes must remain stable across review
and checks. Any review/check failure queues a scoped fix with the report and
retained evidence. Completion requires both review pass and all fixed checks.
The quality of caller-written checks still determines how convincingly criteria
are established; a model's assertions and a trivial command do not prove a goal.

Each attempt has a private evidence directory containing packet/schema snapshots,
input prompt, stdout/stderr, observed session ID in SQLite, final JSON, receipt,
and (for reviews) check reports, command output and artifact hashes. Status shows
the evidence directory, actual phase, candidate, attempt budget, lease/heartbeat,
worker/group identity, next retry and remaining action. Heartbeats mean the
wrapper is responsive, not that the model is making useful progress.

## Ownership and recovery

One tick holds an exclusive supervisor flock. A duplicate tick returns
`already_owned` and launches nothing. SQLite `BEGIN IMMEDIATE` transactions claim
work atomically and exclude active projects/resource keys. A bounded tick drains
ready phases while time remains; it does not wait for future retry times. The
concurrency cap counts all unresolved active claims, including orphaned work.
The default tick budget is 1,800 seconds; the default attempt budget is 900
seconds. Leave additional room for cleanup and review. Custom attempt budgets
need a correspondingly larger cycle. The cycle deadline also caps in-flight runs: a short `--max-seconds` can interrupt
a longer `run_seconds` attempt. Set the cycle budget for the intended bounded
turns (for example 1800 seconds for 900-second runs), or deliberately select
shorter per-mission runs. Interrupted phases consume attempts and retry later.

Each wrapper has its own Linux session/process group. Runs record boot ID, PID
and `/proc` start identity. Wrappers monitor the supervisor's identity and stop
when it disappears. Deadline, output overflow, pause and cancel stop only owned
children, with TERM then KILL when needed. Runtime limits include checks. Stdout
and stderr share a bounded byte budget. A supervisor stop also cleans up its own
children; external sessions are never killed. No process is signaled based only
on a stored PID.

After a crash, a later tick waits for the lease **and** confirms the registered
wrapper and its remaining process-group members are dead before retrying. A live
orphan blocks its resource even after lease expiry, preventing duplicate writers.
Registration and retirement are transactional, so an old unregistered worker
cannot launch after its claim is replaced. Runs and evidence survive recovery;
intentional pause/cancel cannot be undone by recovery. Run `tick` again after the
lease expires if a dead claim remains visible. Inspect expired active runs in
`doctor` if a wrapper/group remains alive. The queue does not kill preexisting
sessions to make room.

## Trigger integration and limits

The parent can call an absolute Python/script path as an `ExecStart` or cron job:

```sh
/usr/bin/python3 /absolute/plugin/scripts/mission_control.py tick --once --max-workers 2 --max-seconds 1800
```

Use a private local XDG_STATE_HOME and explicit PATH for Codex and its tools.
For systemd use a user oneshot service, `UMask=0077`, `KillMode=control-group`,
`TimeoutStopSec` above the worker cleanup window, and a recurring timer. Verify
actual triggering separately. Login lifetime and lingering affect user timers.
Installation, enablement, provider authentication and live project acceptance
are the parent's responsibility; this implementation changes no services.

This is a Linux, same-user reliability boundary, not a security sandbox against
malicious agents. Same-user programs can alter SQLite/evidence. Workspace-write
is supplemented by explicit task boundaries, not a per-file OS access policy.
Processes deliberately detaching into a different session, independently started
external writers, escaped subprocesses, uninterruptible kernel I/O, disk failures
and hostile symlink races need stronger isolation/cgroups. systemd control-group
cleanup is recommended. Check argv execute with the caller's local permissions,
not Codex's read-only sandbox. Review hash stability covers declared artifacts;
checks must cover other relevant behavior and files. Retention/pruning, migrations
beyond schema v1, dependency probes, credential repair and automatic schedule
installation are not supplied.

For mechanics verification only, `create --allow-fixture` accepts candidate
`{"backend":"fixture"}`. Optional fixture `scenario` values are `success`,
`review_fail_once`, `always_fail`, `malformed_once` and `slow`; `artifact` defaults
to `fixture-result.txt`, `content` to `fixture verified\n`. Fixture outputs are
explicitly labeled and cannot establish real model/provider completion. Never
use a fixture candidate in a live mission.
