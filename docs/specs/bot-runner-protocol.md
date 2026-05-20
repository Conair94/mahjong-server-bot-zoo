# Spec 4 — Bot-runner protocol

How the `BotRunnerAdapter` (one of the seat adapter implementations from [seat-port.md](seat-port.md)) speaks to a bot running as a subprocess. The wire format mirrors the **Botzone local-judge protocol** so any bot that runs on Botzone runs here without modification — that's the S1 exit criterion.

The Botzone wire format itself is defined by the [Botzone CSM wiki](https://wiki.botzone.org.cn/index.php?title=Chinese-Standard-Mahjong/en). This spec covers everything *around* it: process lifecycle, framing details Botzone leaves implementation-defined, time-budget enforcement, sandboxing, bot manifests, error surfacing.

Builds on [seat-port.md](seat-port.md) (the `BotRunnerAdapter`'s `decide`/`observe`/lifecycle hooks) and [record-format.md](record-format.md) (events the adapter emits for the record).

**Status:** draft, pre-S0.

## Goals

- **Botzone bots are first-class.** A reference bot pulled from [sample-bot-Botzone](https://github.com/ailab-pku/Chinese-Standard-Mahjong) runs without any wrapper. This is what makes our records judge-acceptable in S1 and lets us use prior-year Botzone entries as a held-out opponent set for AI evaluation.
- **Long-running by default, short-running supported.** A bot subprocess stays alive across the turns of one hand for amortized startup cost (loading model weights once, not 50+ times); short-running mode (subprocess per turn) is supported for bots that need it.
- **One subprocess per bot per hand.** Subprocesses are torn down at hand end. No process reuse across hands — keeps memory leaks bounded and means a crashed bot doesn't poison the next game.
- **Hard time and resource budgets.** A bot cannot exceed its per-turn time budget, its memory limit, or its file-descriptor count. Violations terminate the bot (not just the turn) and the seat falls through to the standard error model from [seat-port.md](seat-port.md).
- **No network access by default.** Bots are sandboxed away from the network. They can read their own bundled files; they cannot reach the internet. This is a hobby server; the cost of an exfiltrating or backdoored bot is borne by the host (you) — close that hole at the protocol layer.

## Non-goals

- **Not a build system.** Bot binaries / scripts must already exist on disk when registered; this protocol doesn't compile, package, or fetch them.
- **Not a deployment system.** Cross-host bot distribution, versioning, and rollback are out of scope. A bot is a directory of files registered with the server.
- **No language-specific support.** The protocol is wire-level (stdin/stdout JSON over a pipe). Whatever the bot is written in — Python, C++, Rust, shell — it just needs to speak the wire format.
- **No protocol versioning beyond Botzone's.** We don't add fields to the Botzone request/response shapes. The wiki's format is the contract; extensions go in the manifest layer or in our internal records, never on the wire.

## Process lifecycle

```
  [register]                             [hand starts]
       |                                       |
       v                                       v
  manifest on disk  --->  BotRunnerAdapter.seated  --->  spawn subprocess
                                                              |
                                                              v
                                                       startup handshake
                                                              |
                                                              v
                                                          warm idle
                                                              ^
                                                              |
   [each prompt]   --->   BotRunnerAdapter.decide   --->   write request, read response
                                                              |
                                                              v
                                                          warm idle
                                                              ^
                                                              |
   [hand ends]     --->   BotRunnerAdapter.left     --->   SIGTERM, wait, SIGKILL, reap
```

### Spawn

Triggered by `BotRunnerAdapter.seated`. The runner:

1. Reads the bot's manifest (see below).
2. Constructs the command (`manifest.command`) with arguments (`manifest.args`).
3. Applies resource limits (`setrlimit` on the child; on Linux, additionally a cgroup if configured).
4. Spawns with `cwd = manifest.directory`, `stdin = PIPE`, `stdout = PIPE`, `stderr = PIPE`, **no inherited environment** beyond a whitelist (`PATH`, `LANG`, plus manifest-declared vars).
5. Starts a stderr drain task that logs every line at DEBUG level prefixed with `bot:<bot_id>:stderr`. Stderr is *never* parsed for protocol meaning — it's diagnostic-only.

The spawn must complete within `manifest.spawn_deadline_ms` (default 5000ms). Slow spawns count as `seated`-time failure → seat replaced with `AutoPassAdapter` per the seat-port error model.

### Startup handshake

Immediately after spawn, the runner sends a single line on stdin:

```
{"kind":"HELLO","seat":0,"wind":"F1","ruleset":"mcr-2006","format":"botzone-csm","mode":"long_running"}
```

The bot must respond with a single line on stdout within `manifest.handshake_deadline_ms` (default 1000ms):

```
{"kind":"HELLO","bot_id":"b_rule_v1","version":"0.1.0","ack_mode":"long_running"}
```

`ack_mode` may downgrade to `"short_running"` if the bot can't sustain long-running mode. The runner respects the downgrade.

**The HELLO handshake is our addition**, not part of Botzone's protocol. It lives outside the per-turn request/response loop and never reaches the Botzone-format wire. Its only purpose is to confirm the subprocess started cleanly and to negotiate mode. A bot that can't handle the HELLO line (a vanilla Botzone bot that wasn't packaged for our runner) is run in a wrapper mode: the runner sees no response within the handshake deadline, assumes vanilla Botzone, and skips the handshake.

**Why a handshake at all:** a bot subprocess that died at startup (missing dependency, wrong working directory) is very common in development. The handshake surfaces the failure in <1s instead of waiting for the first decision deadline.

### Per-turn request

When `BotRunnerAdapter.decide` is called, the runner:

1. Serializes the accumulated history buffer into the Botzone request format (see the wiki — it's a sequence of typed lines: `0`/`1`/`2`/`3` followed by content). The history buffer was populated by every `observe` call since the hand started.
2. Writes the request to stdin, followed by a single LF, then `flush()`.
3. Reads exactly one response line from stdout under a deadline of `min(prompt.deadline, manifest.budget_ms_per_turn)`.
4. Parses the response as a Botzone action string (`PASS`, `PLAY W3`, `PENG B5`, `CHI W3 W4 W5` — note Botzone CHI format is "claimed tile" followed by "middle tile"; our internal `Action` shape needs the three tiles, so the runner reconstructs).
5. Returns the parsed `Action` from `decide`, or raises `SeatError` on parse/framing failure.

In **long-running mode**, after writing the response the bot continues running, waiting for the next request on stdin. In **short-running mode**, the bot exits after each response; the runner re-spawns for the next turn (significantly slower, used as a fallback).

### Teardown

Triggered by `BotRunnerAdapter.left`. The runner:

1. Sends `SIGTERM` to the subprocess.
2. Waits up to `manifest.teardown_grace_ms` (default 2000ms) for the process to exit.
3. Sends `SIGKILL` if still alive.
4. `wait()`s on the child to reap it (no zombies).
5. Logs the exit code and any final stderr lines at DEBUG.

Teardown is best-effort and never raises. A subprocess that refuses to die after SIGKILL is a kernel-level concern; we log it and move on.

## Wire framing

Three details Botzone's wiki leaves under-specified that we lock here for our runner:

1. **Encoding: UTF-8.** Always. The bot's stdin and stdout are decoded as UTF-8 with `errors="strict"`. Anything else is a framing violation.
2. **Line terminator: LF (`\n`), single character.** The runner sends LF, expects LF. CRLF responses are tolerated on read (stripped) but the runner never emits them.
3. **Request and response are each exactly one logical "message" per turn.** The request is one Botzone-format payload (which itself contains multiple newline-separated lines describing the history). It ends with a sentinel line: `>>>BOTZONE_REQUEST_END<<<`. The bot writes its response, then a sentinel line: `>>>BOTZONE_RESPONSE_END<<<`. The runner reads until that sentinel.

The sentinel-line convention is the bit of "implementation-defined Botzone" we pick a flavor of. We document it in the bot SDK (a small Python helper bundled with the project) so writing a bot for our runner is a copy-paste exercise.

**Why sentinels and not byte-length framing:** sentinels survive whitespace mistakes in bot code (a stray trailing newline doesn't desync the stream) and are debuggable by eye when watching the stream raw. Length-prefix framing would be marginally faster and is correct-by-construction, but the debuggability win is worth the trade at this scale.

## Bot manifest

Each registered bot has a manifest on disk:

```
bots/
  b_rule_v1/
    manifest.json
    bot.py
    weights/...
```

`manifest.json`:

```json
{
  "bot_id":               "b_rule_v1",
  "version":              "0.1.0",
  "display_name":         "Rule-based v1",
  "directory":            "./",
  "command":              ["python", "-u", "bot.py"],
  "args":                 [],
  "env":                  {"PYTHONUNBUFFERED": "1"},
  "runtime_mode":         "long_running",
  "spawn_deadline_ms":    5000,
  "handshake_deadline_ms":1000,
  "budget_ms_per_turn":   1000,
  "teardown_grace_ms":    2000,
  "limits": {
    "memory_mb":          512,
    "cpu_seconds":        300,
    "max_fds":            64,
    "max_processes":      1,
    "network":            "deny"
  },
  "ruleset_supported":    ["mcr-2006"],
  "format_supported":     ["botzone-csm"],
  "notes":                "Reference rule-based bot. Strong baseline."
}
```

- `command` and `args` are passed to `subprocess.Popen` verbatim. No shell, no expansion — if you want shell features, write a wrapper script.
- `env` is whitelisted onto the child; nothing else from the parent's environment leaks through except the hardcoded whitelist (`PATH`, `LANG`).
- `limits.memory_mb` is enforced via `setrlimit(RLIMIT_AS, ...)`. Realistic ML bots (a small neural net) fit comfortably in 512MB; large bots can request more in their manifest, but the table-manager-side cap (configurable per server) overrides if the bot asks for too much.
- `limits.cpu_seconds` is `RLIMIT_CPU` — total CPU seconds across the whole subprocess lifetime, not per turn. The per-turn cap is `budget_ms_per_turn` and is enforced via the runner's read deadline.
- `limits.max_processes` is `RLIMIT_NPROC`; default 1 means the bot can't fork. Bots that need worker processes (parallel MCTS) raise this in their manifest.
- `limits.network` is `"deny"` (default) or `"allow"`. **Deny is enforced via a network namespace** on Linux (the subprocess is spawned in a netns with no interfaces). On macOS (dev only), the deny is best-effort — we log a warning that network sandboxing isn't enforceable.
- `ruleset_supported` / `format_supported` are checked at registration time. A bot that doesn't claim `mcr-2006` can't be seated at an MCR table.

**Manifest validation runs at registration**, not at spawn. A malformed manifest blocks the bot from being registered, with a clear error pointing at the offending field.

## Time-budget enforcement

Per-turn budget is `min(prompt.deadline, manifest.budget_ms_per_turn)`. The runner enforces it as:

1. Write the request, flush.
2. `asyncio.wait_for(read_response(), timeout=budget_seconds)`.
3. If the wait times out: the runner sends `SIGTERM` to the subprocess (because a Botzone bot that misses its budget is presumed wedged — there's no resuming a partial response cleanly), the `decide` call raises `SeatTimeout`, the seat-port error model takes over (default action + strike), the adapter's `seated` is *not* re-run — the next `decide` will spawn a fresh subprocess.

**Per-turn timeout terminates the subprocess; it does not just abandon the read.** Reasoning: if the bot is hung, leaving it running consumes CPU and may leak memory; if the bot is just slow, the next turn would still be slow. Either way, restart is the right move. The cost is one extra spawn (~hundreds of ms) per timeout, which is acceptable given timeouts should be rare.

**Botzone documents ~1s for C++ and longer for Python**; the wiki is the source of truth for current numbers. The manifest's `budget_ms_per_turn` defaults to 1000ms; bots that need more declare it in their manifest, capped by the server-side maximum (configurable, default 5000ms).

## Sandboxing

Process-level isolation, layered:

1. **Drop privileges.** The subprocess runs as a dedicated unprivileged user (e.g., `mahjong-bot`), never as `mahjong` (the server user) and never as `root`. On the dev laptop this is a single uid; in production it can be one uid per concurrent bot for stronger isolation.
2. **No environment leak.** Hardcoded whitelist (`PATH`, `LANG`) + manifest `env`. Nothing else.
3. **`RLIMIT_AS`** caps virtual memory. OOM in the bot's address space kills the bot, not the host.
4. **`RLIMIT_CPU`** caps total CPU time.
5. **`RLIMIT_NOFILE`** caps file descriptors at `limits.max_fds`.
6. **`RLIMIT_NPROC`** caps fork count.
7. **Network namespace** on Linux denies network unless the manifest opts in.
8. **Filesystem:** the bot's `cwd` is `manifest.directory`; we don't currently chroot or use a bind-mount jail. **Open question:** is a directory bind-mount worth it? Working answer: defer until we accept untrusted bots. For S1 we run only bots we've reviewed.

**Why this layering:** each layer catches a different failure mode. `RLIMIT_AS` catches accidental OOM; the unprivileged user catches "bot tries to read `/etc/passwd`"; netns catches "bot tries to phone home." Together they fail closed.

**macOS limitation:** netns doesn't exist; `RLIMIT_NPROC` is per-uid not per-process, which makes it unreliable when multiple bots share a uid. On a macOS dev host the runner logs warnings at registration time about which sandboxing layers are inactive. We don't fail-closed because development on macOS is the primary daily workflow.

## Error surfacing into records

Each failure mode produces a specific marker in the resulting record event (see [record-format.md](record-format.md) for the event shape):

| Failure | `decide` outcome | Event markers |
| --- | --- | --- |
| Read timeout (bot didn't respond) | `SeatTimeout` | `timeout: true, bot_error: "read_timeout"` |
| Stdout closed (bot exited) | `SeatError` | `bot_error: "process_exit", exit_code: N` |
| Parse failure (malformed response) | `SeatError` | `bot_error: "parse_error", raw_response: "…"` |
| Sentinel violation (no `>>>BOTZONE_RESPONSE_END<<<`) | `SeatError` | `bot_error: "framing_error", bytes_read: N` |
| Illegal action (parsed but not in `legal_actions`) | normal return; engine rejects | `illegal: true, attempted_action: …` (per seat-port spec) |
| OOM kill (RLIMIT_AS) | `SeatError` (process_exit with signal) | `bot_error: "oom_kill"` |
| CPU-limit kill (RLIMIT_CPU) | `SeatError` | `bot_error: "cpu_limit"` |

The `bot_error` field on the record event is the diagnostic — it's what we grep for when reviewing why a bot underperformed. The seat-port error model already handles the strike counting and `AutoPassAdapter` substitution; this spec just adds the bot-specific labels.

**`raw_response` is truncated to 1KB.** A bot that floods stdout with junk shouldn't bloat records.

## Worked example: a long-running bot for one decision

Bot is at warm-idle. `BotRunnerAdapter.decide(prompt)` is called.

Runner sends to bot's stdin (UTF-8, LF-terminated):

```
1 0 0
14 W1 W3 W5 B2 B7 T1 T1 T6 T9 F1 F1 J2 J3 W7
>>>BOTZONE_REQUEST_END<<<
```

(That's a Botzone-format history: `1 0 0` = "I am dealer, seat 0, round 0", followed by a draw of 14 tiles. Real histories are longer; this is the first turn.)

Bot processes (under its `budget_ms_per_turn`), writes to stdout:

```
PLAY F4
>>>BOTZONE_RESPONSE_END<<<
```

Runner reads up to the sentinel, parses `PLAY F4`, returns `{"type": "PLAY", "tile": "F4"}` from `decide`. Bot goes back to warm-idle, blocked on stdin read, until next request.

If the bot instead wrote:

```
CHI W3 W4
>>>BOTZONE_RESPONSE_END<<<
```

…the runner parses CHI (Botzone format: claimed tile, middle tile of the run), reconstructs the three tiles (`["W3","W4","W5"]`), checks against `prompt.legal_actions`. If present, returns `{"type":"CHI","tiles":["W3","W4","W5"]}`. If not present (e.g., the prompt was a `DISCARD`, not a `CLAIM`), the engine rejects → `illegal: true` per the seat-port error model.

## Alternatives considered

**Stdin/stdout JSON vs. a richer IPC (Unix domain socket, gRPC).**

- Considered: a typed RPC layer for cleaner request/response semantics.
- Chose stdin/stdout because (a) every Botzone bot speaks it natively — adopting anything else means writing a wrapper for every existing bot, (b) it's debuggable with `cat` and `tee`, (c) the failure modes are well-understood (pipe-closed, EPIPE, etc.), (d) gRPC would require a `.proto` and a runtime in every bot's language. The performance cost is unmeasurable at one decision per second.

**Sentinel-line framing vs. length-prefix framing.**

- Considered: 4-byte big-endian length prefix on each message.
- Chose sentinels because (a) they survive accidental whitespace mistakes in bot code (the most common dev-time failure), (b) they're human-debuggable in a terminal, (c) Botzone's reference local-judge tooling uses a sentinel convention. Length-prefix is the right answer if we ever ship a "production protocol mode" — not now.

**One subprocess across hands vs. fresh per hand.**

- Considered: keep the subprocess warm across hands of the same match (saves model-load time).
- Chose fresh per hand because (a) it bounds memory growth (no accumulating leaks), (b) it isolates failures (a corrupted bot state doesn't poison the next hand), (c) the hand-end → next-hand-start gap is already on the order of seconds (record finalization, deal, prompts), so spawn cost is hidden. The savings would only matter at competition scale, not for friends-and-family play.

**Hard kill on per-turn timeout vs. cooperative abandon.**

- Considered: leaving the subprocess running and just ignoring the late response.
- Chose hard kill because (a) a hung bot consumes resources indefinitely, (b) a slow bot's next turn is also slow — restart is the right escalation, (c) "did the bot exit cleanly?" is a binary signal in the record; "the bot is still running but we abandoned the read" is a state with no good representation. Restart is the simpler model.

**Sandboxing strictness on macOS.**

- Considered: refuse to run bots on macOS at all (forcing Linux dev VMs).
- Chose warn-and-run because the daily dev workflow is macOS-native; making it inaccessible for dev would hurt iteration speed far more than the residual risk of trusted-bots-on-dev costs. The warning surface is loud; production-grade isolation lives behind the Linux host (S7).

**HELLO handshake vs. cold start.**

- Considered: skip the handshake, just send the first turn's history and time out if no response.
- Chose handshake because the cost (~1ms) is negligible and the diagnostic value is high — a bot that fails to start (missing dep, syntax error, wrong cwd) surfaces in <1s with a clean stderr capture, instead of at the first decision deadline with an opaque "subprocess died" message.

## Verification fixtures this spec implies

These extend the seat-port fixtures with bot-runner-specific cases.

1. **Reference-bot round-trip.** Spawn the official Botzone Python reference bot (`sample-bot-Botzone`); play a full hand against three `CannedAdapter`s; the bot's actions parse, the hand completes, the resulting record exports cleanly to Botzone log format. **This is the S1 exit artifact** from the server plan.
2. **HELLO handshake success.** A bot with our SDK wrapper completes the handshake within deadline; mode negotiation works (request long, ack long; request long, ack short → runner uses short).
3. **HELLO handshake skip (vanilla Botzone bot).** A vanilla bot that ignores the HELLO line and reads its first request normally completes a hand without the runner getting wedged.
4. **Spawn failure.** A manifest pointing at a non-existent command fails registration (not spawn). A manifest pointing at a script that exits immediately fails at `seated` with `bot_error: "process_exit"` and the seat gets `AutoPassAdapter`'d. Hand still completes.
5. **Per-turn timeout.** A bot scripted to `sleep(10)` against a 1s budget triggers SIGTERM, `decide` raises `SeatTimeout`, the event has `bot_error: "read_timeout"`. Next turn spawns a fresh subprocess.
6. **OOM kill.** A bot scripted to allocate beyond `RLIMIT_AS` gets killed; `decide` raises `SeatError`; event has `bot_error: "oom_kill"`. (Linux only; on macOS the test is skipped with a documented xfail.)
7. **Network deny.** A bot scripted to open a socket fails on Linux (netns has no interfaces). On macOS the test runs in a documented best-effort mode (warn, don't fail).
8. **Framing violations.** A bot that omits the `>>>BOTZONE_RESPONSE_END<<<` sentinel times out at the read deadline; event has `bot_error: "framing_error"`. A bot that emits CRLF responses works (we tolerate on read).
9. **Illegal-action surfacing.** A bot that responds `PENG B5` when its `legal_actions` doesn't include a PENG on B5 produces `illegal: true, attempted_action: {"type":"PENG","tile":"B5"}, bot_error: null` (the parsed action was syntactically valid; the engine rejected it — distinct error class from `bot_error`).
10. **Manifest validation.** A manifest missing `bot_id` is rejected at registration with a field-specific error. A manifest with `limits.memory_mb` over the server max is rejected at registration, not silently capped.

## Open questions

- **Filesystem isolation.** Do we bind-mount-jail bots into their manifest directory, or trust them to stay there? Working answer: trust for S1 (we review all bots); add bind-mount jail when we accept untrusted bots. Reconsider if a future research-ideas item involves running unknown bots.
- **GPU access.** A neural-net bot wants the GPU. Currently no manifest field for it; the bot inherits whatever GPU access the host gives the `mahjong-bot` user. Working answer: defer; pin a `limits.gpu` field when the first neural bot lands (v2 of the AI plan).
- **Cross-platform `cpu_seconds` semantics.** `RLIMIT_CPU` on Linux counts wall CPU time; on macOS it's similar but the kill semantics differ slightly. The 300s default is generous enough that platform drift doesn't matter; document and revisit if a bot legitimately needs to fine-grain this.
- **Botzone "rendering" output channel.** Botzone bots can optionally emit a third channel (visualization data) alongside their action. Working answer: ignore for v1; if a bot emits it, we drop it. Pin a "render line" event in the record format if we ever want to surface it.
- **Multiple-process bots (parallel MCTS).** A bot that wants `limits.max_processes > 1` raises new questions: do child processes inherit the netns? the unprivileged uid? Working answer: yes to both — cgroup-style isolation applies to the process tree, not the leader. Document and add a fixture when the first multi-process bot is registered.
- **Subprocess "warm pool" across hands.** Performance optimization; explicitly rejected for v1 (see Alternatives). Worth revisiting only if measured bot-spawn-time becomes a meaningful fraction of inter-hand latency.
- **`>>>BOTZONE_REQUEST_END<<<` sentinel choice.** Worth documenting whether this exact string is used by the official local-judge tooling (so we're not gratuitously different). Verify against the wiki / sample-bot repo before S1 spawns the first reference bot; adjust if the canonical sentinel is different.
