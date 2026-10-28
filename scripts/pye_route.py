#!/usr/bin/env python3
"""Route one rough request through the installed continuity drafter.

This is a small, reversible adapter over ``continuation_infer.py``.  It owns
only routing metadata: request bodies stay in memory and the private receipt
contains hashes and execution facts, never the cue, corrections, source text or
generated artifact.  The adapter does not read, write or rebuild the archive or
semantic index.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
INFER = PLUGIN_ROOT / "scripts" / "continuation_infer.py"
WORKFLOW = PLUGIN_ROOT / "scripts" / "continuation_workflow.py"
RECEIPT_ROOT = Path.home() / "brain-data/upar-dip/derived/protect-your-energy/route-receipts"
INPUT_LIMIT = 262_144
CUE_LIMIT = 4_096
CORRECTION_LIMIT = 4_096


# These are craft instructions, not a user profile.  They let the existing
# inference route produce the requested kind of result without requiring the
# caller to know the individual skill names.
ROUTES: dict[str, dict[str, str]] = {
    "reclaim": {
        "skill": "thought-reconstruction/reclaim",
        "description": "Recover an earlier idea and continue it into a useful artifact.",
        "profile": (
            "Route the current request as idea reclamation. Recover the earlier idea's "
            "reasoning, intended behavior, constraints, changes and unresolved gaps "
            "from qualified context, then produce a concrete continuation. Keep source, "
            "inference and proposal separate; do not stop at a retrieval summary."
        ),
    },
    "make-coherent": {
        "skill": "semantic-expression/make-coherent",
        "description": "Turn a rough explanation into clear, meaning-preserving prose.",
        "profile": (
            "Route the current request as coherent expression. Preserve the speaker's "
            "meaning, stakes, voice and necessary detail while removing repetition and "
            "unhelpful jumps. Produce the usable draft for the implied audience, not a "
            "description of how to rewrite it."
        ),
    },
    "contextual-reply": {
        "skill": "semantic-expression/contextual-reply",
        "description": "Draft the relevant next message in an ongoing conversation.",
        "profile": (
            "Route the current request as a contextual reply. Recover only the earlier "
            "reasoning that changes this conversational move, preserve the speaker's "
            "actual intent and social stakes, and produce the next message ready to send. "
            "Do not answer a different earlier question."
        ),
    },
    "choose-next": {
        "skill": "protecting-your-energy/choose-next",
        "description": "Choose one useful next action and a concrete completion point.",
        "profile": (
            "Route the current request as next-action selection. Compare the real candidate "
            "actions by goal benefit, readiness, dependencies, proof needed, changed timing "
            "and interruption cost. Choose one useful action with a concrete completion "
            "condition and preserve deferred work. Do not invent urgency or capacity."
        ),
    },
    "finish-loop": {
        "skill": "protecting-your-energy/finish-loop",
        "description": "Reassess expanding or repeatedly failing work and finish the valid increment.",
        "profile": (
            "Route the current request as a finish-loop reassessment. Bind the requested "
            "result, current artifact, actual proof, owner, dependencies and latest "
            "observation. Reassess the changed remaining effort, preserve a precise "
            "resumption point, and deliver the smallest valid in-scope increment or the "
            "exact blocker without silently shrinking the goal."
        ),
    },
    "recurring-friction": {
        "skill": "protecting-your-energy/recurring-friction",
        "description": "Remove a repeated obstacle with the smallest reusable intervention.",
        "profile": (
            "Route the current request as recurring-friction relief. Recover distinct "
            "episodes, attempted remedies, corrections and outcomes; identify the "
            "evidenced cause and implement the smallest reusable intervention. State its "
            "trigger, permitted action, useful result, correction behavior, fallback and "
            "the smallest observation that demonstrates it works."
        ),
    },
    "opportunity-radar": {
        "skill": "protecting-your-energy/opportunity-radar",
        "description": "Connect a verified new development to an earlier idea and readiness.",
        "profile": (
            "Route the current request as opportunity radar. Connect an evidenced earlier "
            "idea to a verified current development, distinguish external facts from fit "
            "inference, account for present assets and blockers, and produce one feasible "
            "next action or a clearly conditioned watch item. Do not manufacture urgency."
        ),
    },
}


# Ordered from the most conversationally specific to the broad default.  Scores
# are deliberately modest: this is a routing hint, not a semantic verdict.
HINTS: dict[str, tuple[tuple[re.Pattern[str], int], ...]] = {
    "contextual-reply": (
        (re.compile(r"\b(reply|respond|response|message|email|say to|answer)\b", re.I), 4),
        (re.compile(r"\b(conversation|follow[- ]?up|recipient|what should i say)\b", re.I), 5),
    ),
    "make-coherent": (
        (re.compile(r"\b(rewrite|rephrase|clarify|coherent|concise|summari[sz]e|word this)\b", re.I), 5),
        (re.compile(r"\b(less spammy|clean this up|make this clearer)\b", re.I), 4),
    ),
    "finish-loop": (
        (re.compile(r"\b(stuck|blocked|overrun|taking too long|failed repeatedly|keeps expanding)\b", re.I), 5),
        (re.compile(r"\b(finish|complete|resume|remaining work|what is left)\b", re.I), 2),
    ),
    "recurring-friction": (
        (re.compile(r"\b(every time|again|repeated|keeps|manual|friction|overhead|automate)\b", re.I), 5),
        (re.compile(r"\b(doesn't work|does not work|have to explain)\b", re.I), 3),
    ),
    "opportunity-radar": (
        (re.compile(r"\b(opportunity|timely|trend|viral|relevant now|new release|what's happening)\b", re.I), 5),
        (re.compile(r"\b(launch|visibility|market|opening|window)\b", re.I), 2),
    ),
    "choose-next": (
        (re.compile(r"\b(what should i do|what next|next action|priorit(?:y|ize)|today|which should)\b", re.I), 5),
        (re.compile(r"\b(decide|choose|competing|schedule)\b", re.I), 2),
    ),
    "reclaim": (
        (re.compile(r"\b(reclaim|earlier|forgot|old idea|remember|resume|continue|past thinking)\b", re.I), 4),
        (re.compile(r"\b(idea|thought|brainstorm|in its entirety)\b", re.I), 2),
    ),
}


class RouteError(ValueError):
    pass


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def process_tree(root_pid: int, expected_start: str | None = None) -> dict[int, str]:
    """Return descendant PID -> Linux start ticks, including a setsid child."""
    parents: dict[int, tuple[int, str]] = {}
    try:
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                fields = stat[stat.rfind(")") + 2:].split()
                parents[int(entry.name)] = (int(fields[1]), fields[19])
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        return {}
    if root_pid not in parents or (expected_start is not None and parents[root_pid][1] != expected_start):
        return {}
    found = {}
    frontier = [root_pid]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, started) in parents.items():
            if ppid == parent and pid not in found and pid != root_pid:
                found[pid] = started
                frontier.append(pid)
    found[root_pid] = parents[root_pid][1]
    return found


def stop_process_tree(proc: subprocess.Popen, grace: float = 2.0) -> None:
    """Stop the router child and descendants even if a descendant called setsid."""
    tracked = process_tree(proc.pid)
    root_start = tracked.get(proc.pid)

    def signal_tracked(sig: int) -> None:
        # Guard every signal against PID reuse; session IDs are not sufficient
        # because the native Claude process creates a separate session.
        for pid, started in sorted(tracked.items(), reverse=True):
            current = process_tree(pid).get(pid)
            if current == started:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass

    signal_tracked(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        tracked.update(process_tree(proc.pid, root_start))
        if not any(process_tree(pid).get(pid) == started for pid, started in tracked.items()):
            break
        time.sleep(0.05)
    signal_tracked(signal.SIGKILL)
    proc.wait()


def choose_route(cue: str, requested: str) -> tuple[str, str, dict[str, int]]:
    if requested != "auto":
        return requested, "explicit", {requested: 0}
    scores = {name: 0 for name in ROUTES}
    for name, patterns in HINTS.items():
        scores[name] = sum(weight for pattern, weight in patterns if pattern.search(cue))
    best = max(scores.values(), default=0)
    if best <= 0:
        return "reclaim", "default", scores
    # Stable order follows HINTS, so equal signals do not fluctuate between runs.
    selected = next(name for name in HINTS if scores[name] == best)
    return selected, "lexical-hint", scores


def parse_json_input(raw: bytes) -> dict:
    if len(raw) > INPUT_LIMIT:
        raise RouteError("input-over-limit")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, UnicodeError) as error:
        raise RouteError("input-json-invalid") from error
    if not isinstance(value, dict) or set(value) - {"cue", "corrections", "current_artifact"}:
        raise RouteError("input-schema-invalid")
    return value


def normalize(cue: str, corrections: list[str]) -> tuple[str, list[str]]:
    if not isinstance(cue, str):
        raise RouteError("cue-invalid")
    cue = cue.strip()
    if not cue or len(cue) > CUE_LIMIT:
        raise RouteError("cue-outside-bounds")
    # One correction slot is reserved for the selected capability profile.
    if not isinstance(corrections, list) or len(corrections) > 7:
        raise RouteError("corrections-outside-bounds")
    normalized: list[str] = []
    for correction in corrections:
        if not isinstance(correction, str) or not correction.strip() or len(correction) > CORRECTION_LIMIT:
            raise RouteError("correction-outside-bounds")
        normalized.append(correction.strip())
    return cue, normalized


def write_receipt(receipt: dict) -> str:
    RECEIPT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if RECEIPT_ROOT.is_symlink() or not RECEIPT_ROOT.is_dir():
        raise RouteError("receipt-directory-invalid")
    os.chmod(RECEIPT_ROOT, 0o700)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = RECEIPT_ROOT / f"route-{stamp}-{uuid.uuid4().hex[:8]}.json"
    encoded = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.NamedTemporaryFile(mode="wb", dir=RECEIPT_ROOT, prefix=".route-", delete=False) as stream:
        os.chmod(stream.name, 0o600)
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
    return str(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cue", nargs="*", help="rough request; use --input-stdin for a JSON envelope")
    parser.add_argument("--route", choices=("auto", *ROUTES), default="auto")
    parser.add_argument("--input-stdin", action="store_true", help="read bounded JSON {cue, corrections, current_artifact}")
    parser.add_argument("--dry-run", action="store_true", help="emit the route plan without calling the drafter")
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--execution-harness")
    parser.add_argument("--execution-context-id")
    parser.add_argument("--query", action="append", default=[])
    parser.add_argument("--unit-id", action="append", default=[])
    parser.add_argument("--scope-unit-id")
    parser.add_argument("--message-rerank", action="store_true")
    parser.add_argument("--correction", action="append", default=[])
    parser.add_argument("--include-analysis", action="store_true")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--native-timeout", type=float, default=300)
    return parser


def invoke(args, cue: str, corrections: list[str], route: str) -> tuple[dict, int, str | None]:
    profile = ROUTES[route]["profile"]
    # The route profile supplies craft, while caller corrections remain the
    # latest authority and therefore follow it in the existing contract.
    payload = {"cue": cue, "corrections": [profile, *corrections]}
    # Preserve a caller-supplied proposal in RAM for the existing input contract.
    if args.input_stdin and getattr(args, "current_artifact", None) is not None:
        payload["current_artifact"] = args.current_artifact
    command = [sys.executable, str(INFER), "--input-stdin"]
    if args.direct:
        command.append("--direct")
    if args.task_id:
        command += ["--task-id", args.task_id]
    if args.execution_harness:
        command += ["--execution-harness", args.execution_harness]
    if args.execution_context_id:
        command += ["--execution-context-id", args.execution_context_id]
    for query in args.query:
        command += ["--query", query]
    for unit_id in args.unit_id:
        command += ["--unit-id", unit_id]
    if args.scope_unit_id:
        command += ["--scope-unit-id", args.scope_unit_id]
    if args.message_rerank:
        command.append("--message-rerank")
    if args.include_analysis:
        command.append("--include-analysis")
    command += ["--timeout", str(args.timeout), "--native-timeout", str(args.native_timeout)]
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            stdout, stderr = child.communicate(
                input=json.dumps(payload, ensure_ascii=False).encode(),
                timeout=max(30.0, min(1800.0, args.timeout + 2 * args.native_timeout + 30)))
        except subprocess.TimeoutExpired:
            stop_process_tree(child)
            return ({"version": 1, "kind": "continuation-inference", "status": "not-delivered",
                     "artifact": None, "error": "router-child-timeout"}, 124, started)
        completed = child
        exit_code = completed.returncode
        try:
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            result = {"version": 1, "kind": "continuation-inference", "status": "not-delivered",
                      "artifact": None, "error": "router-child-invalid-output"}
    except OSError:
        return ({"version": 1, "kind": "continuation-inference", "status": "not-delivered",
                 "artifact": None, "error": "router-child-launch-failed"}, 127, started)
    return result, exit_code, started


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        incoming = parse_json_input(sys.stdin.buffer.read(INPUT_LIMIT + 1)) if args.input_stdin else {}
        if incoming.get("cue") is not None:
            if args.cue or not isinstance(incoming["cue"], str):
                raise RouteError("cue-input-conflict")
            cue = incoming["cue"]
        else:
            cue = " ".join(args.cue)
        incoming_corrections = incoming.get("corrections", [])
        if incoming_corrections is None:
            incoming_corrections = []
        if not isinstance(incoming_corrections, list):
            raise RouteError("corrections-input-invalid")
        cue, corrections = normalize(cue, [*args.correction, *incoming_corrections])
        args.current_artifact = incoming.get("current_artifact")
        if args.current_artifact is not None and (not isinstance(args.current_artifact, str) or not args.current_artifact):
            raise RouteError("current-artifact-invalid")
        route, selection_basis, scores = choose_route(cue, args.route)
        profile = ROUTES[route]
        plan = {"version": 1, "kind": "protect-your-energy-route", "status": "planned",
                "route": route, "selection_basis": selection_basis,
                "candidate_scores": scores, "skill": profile["skill"],
                "description": profile["description"], "executor": str(INFER),
                "executor_sha256": source_hash(INFER), "cue_sha256": digest(cue),
                "corrections_sha256": digest(corrections), "body_storage": "not-retained-by-router"}
        if args.dry_run:
            plan["receipt"] = write_receipt({**plan, "status": "planned", "created_at": dt.datetime.now(dt.timezone.utc).isoformat()})
            print(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        result, exit_code, started = invoke(args, cue, corrections, route)
        finished = dt.datetime.now(dt.timezone.utc).isoformat()
        receipt = {**plan, "status": result.get("status", "not-delivered"),
                   "started_at": started, "finished_at": finished, "exit_code": exit_code,
                   "child_kind": result.get("kind"), "child_error": result.get("error"),
                   "validation_code": result.get("validation_code"),
                   "artifact_delivered": bool(result.get("artifact")),
                   "receipt_scope": "router metadata only; cue, corrections, source text and artifact omitted"}
        receipt_path = write_receipt(receipt)
        result["route"] = {"name": route, "selection_basis": selection_basis,
                            "skill": profile["skill"], "receipt": receipt_path,
                            "candidate_scores": scores}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        return 0 if exit_code == 0 and result.get("status") == "draft-produced" else 1
    except (RouteError, OSError, TypeError, ValueError, json.JSONDecodeError):
        print(json.dumps({"version": 1, "kind": "protect-your-energy-route",
                          "status": "not-delivered", "error": "router-input-or-runtime-invalid"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
