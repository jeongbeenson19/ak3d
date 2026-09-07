#!/usr/bin/env python3
"""Step 2 - Send captures from the laptop to the reconstruction workstation.

The laptop records (scripts/record.py) and the Linux workstation reconstructs, so
every capture has to cross the network first. A 60 s 720p/NFOV capture is several
GB, which means a plain `scp` that dies halfway costs you the whole transfer.

This module streams the file over a single ssh connection:

    ssh ws "mkdir -p DIR && cat >> DIR/capture.mkv.part"   <- local bytes on stdin

Because *we* feed stdin, we control the offset: on a retry we ask the workstation
how many bytes already landed and resume from exactly there. When the last byte is
in, the sha256 is compared on both sides and the `.part` file is renamed to its
final name - so a half-written recording can never be mistaken for a good one.

Only stock OpenSSH is required (Windows ships ssh.exe in System32); nothing extra
is installed on either machine. Key-based auth is strongly recommended - otherwise
every ssh invocation prompts for a password (`transfer.py check` explains setup).

Configuration lives in transfer.json (see transfer.example.json); every field can
be overridden by an AK3D_WS_* environment variable or a command-line flag.

Usage:
    python scripts/transfer.py check                 # test the connection
    python scripts/transfer.py send --latest         # newest capture
    python scripts/transfer.py send --all-pending    # everything not sent yet
    python scripts/transfer.py send captures/20260820_162603
    python scripts/transfer.py list                  # sent / pending overview
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_NAME = "transfer.json"
STATE_NAME = ".sent.json"          # written inside a capture dir once it lands
PART_SUFFIX = ".part"

# Small files worth copying next to the recording; the .mkv is handled separately.
SIDECAR_GLOBS = ("*.json", "*.txt", "*.md")


class TransferError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
ENV_PREFIX = "AK3D_WS_"


@dataclass
class Config:
    host: str = ""
    user: str = ""
    port: int = 0                  # 0 = whatever ~/.ssh/config says (or 22)
    identity_file: str = ""
    remote_root: str = ""
    local_root: str = "captures"
    verify: bool = True
    auto_send: bool = False
    delete_local_after_send: bool = False
    chunk_mb: int = 8
    retries: int = 3
    cipher: str = "aes128-gcm@openssh.com"
    ssh_options: list = field(default_factory=list)

    @classmethod
    def load(cls, path=None) -> "Config":
        path = Path(path) if path else (REPO_ROOT / CONFIG_NAME)
        raw = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                sys.exit(f"{path} is not valid JSON: {e}")
        known = set(cls.__dataclass_fields__)
        # "_"-prefixed keys are comments (JSON has none), not settings.
        unknown = {k for k in raw if k not in known and not k.startswith("_")}
        if unknown:
            print(f"warning: ignoring unknown keys in {path.name}: "
                  f"{', '.join(sorted(unknown))}", file=sys.stderr)
        cfg = cls(**{k: v for k, v in raw.items() if k in known})

        for name in known:                       # environment wins over the file
            env = os.environ.get(ENV_PREFIX + name.upper())
            if env is None:
                continue
            current = getattr(cfg, name)
            if isinstance(current, bool):
                setattr(cfg, name, env.strip().lower() in ("1", "true", "yes", "on"))
            elif isinstance(current, int):
                setattr(cfg, name, int(env))
            elif isinstance(current, list):
                setattr(cfg, name, shlex.split(env))
            else:
                setattr(cfg, name, env)
        return cfg

    def require_target(self) -> None:
        missing = [n for n in ("host", "remote_root") if not getattr(self, n)]
        if missing:
            sys.exit(
                f"transfer target incomplete (missing: {', '.join(missing)}).\n"
                f"Create {CONFIG_NAME} in {REPO_ROOT} - copy transfer.example.json "
                f"and fill in your workstation - or set "
                f"{', '.join(ENV_PREFIX + m.upper() for m in missing)}."
            )

    @property
    def destination(self) -> str:
        """user@host, or a bare ~/.ssh/config Host alias when no user is set."""
        return f"{self.user}@{self.host}" if self.user else self.host


# --------------------------------------------------------------------------- #
# ssh plumbing
# --------------------------------------------------------------------------- #
def ssh_exe() -> str:
    """Prefer the Windows OpenSSH client over the Git-for-Windows one.

    Both work, but System32\\OpenSSH\\ssh.exe reads %USERPROFILE%\\.ssh, which is
    where ssh-keygen from a normal PowerShell session puts the key.
    """
    system32 = (Path(os.environ.get("SystemRoot", r"C:\Windows"))
                / "System32" / "OpenSSH" / "ssh.exe")
    if system32.exists():
        return str(system32)
    found = shutil.which("ssh")
    if not found:
        sys.exit("no ssh client found on PATH (install the OpenSSH client).")
    return found


def ssh_command(cfg: Config, batch: bool = False) -> list:
    cmd = [ssh_exe()]
    if cfg.port:                       # unset -> do not override ~/.ssh/config
        cmd += ["-p", str(cfg.port)]
    if cfg.identity_file:
        cmd += ["-i", str(Path(cfg.identity_file).expanduser())]
    if cfg.cipher:
        cmd += ["-c", cfg.cipher]
    cmd += [
        "-o", "Compression=no",            # .mkv is already compressed
        "-o", "ServerAliveInterval=15",    # notice a dead link during a long send
        "-o", "ServerAliveCountMax=6",
    ]
    if batch:
        cmd += ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    cmd += list(cfg.ssh_options)
    cmd.append(cfg.destination)
    return cmd


def ssh_run(cfg: Config, remote_cmd: str, batch: bool = False,
            check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command on the workstation and capture its output."""
    proc = subprocess.run(
        ssh_command(cfg, batch=batch) + [remote_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if check and proc.returncode != 0:
        raise TransferError(
            f"remote command failed (exit {proc.returncode}): {remote_cmd}\n"
            f"{proc.stderr.strip()}"
        )
    return proc


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.0f} B" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _progress(label: str, done: int, total: int, started: float) -> None:
    if not sys.stderr.isatty():
        return
    elapsed = max(time.monotonic() - started, 1e-6)
    rate = done / elapsed
    pct = 100 * done / total if total else 100.0
    eta = (total - done) / rate if rate > 0 else 0
    sys.stderr.write(f"\r  {label}: {pct:5.1f}%  {human(done)}/{human(total)}  "
                     f"{human(rate)}/s  ETA {duration(eta)}   ")
    sys.stderr.flush()


def _progress_done(label: str, total: int, started: float) -> None:
    elapsed = max(time.monotonic() - started, 1e-6)
    line = (f"  {label}: done  {human(total)} in {duration(elapsed)} "
            f"({human(total / elapsed)}/s)")
    sys.stderr.write(("\r" + line + " " * 20 + "\n") if sys.stderr.isatty()
                     else (line + "\n"))
    sys.stderr.flush()


def sha256_file(path: Path, chunk: int = 8 << 20, label: str = "hashing") -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    started = time.monotonic()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
            done += len(block)
            _progress(label, done, total, started)
    _progress_done(label, total, started)
    return digest.hexdigest()


def remote_join(*parts: str) -> str:
    """Join POSIX path segments, preserving a leading slash."""
    cleaned = [p.strip("/") for p in parts if p not in (None, "")]
    joined = "/".join(c for c in cleaned if c)
    return ("/" + joined) if str(parts[0]).startswith("/") else joined


# --------------------------------------------------------------------------- #
# capture discovery / state
# --------------------------------------------------------------------------- #
def local_root(cfg: Config) -> Path:
    root = Path(cfg.local_root)
    return root if root.is_absolute() else (REPO_ROOT / root)


def capture_dirs(cfg: Config) -> list:
    root = local_root(cfg)
    if not root.exists():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and any(d.glob("*.mkv")))


def read_state(capture_dir: Path) -> dict:
    state_path = capture_dir / STATE_NAME
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_state(capture_dir: Path, entry: dict) -> None:
    state = read_state(capture_dir)
    state.setdefault("files", {})
    state["files"][entry["name"]] = entry
    state["destination"] = entry["remote_path"].rsplit("/", 1)[0]
    (capture_dir / STATE_NAME).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def is_sent(capture_dir: Path, mkv: Path, remote_dir: str = "") -> bool:
    """True when this exact file is recorded as landed at this exact destination.

    Point remote_root somewhere else and nothing counts as sent any more - the new
    workstation really does not have the file.
    """
    entry = read_state(capture_dir).get("files", {}).get(mkv.name)
    if not entry or entry.get("bytes") != mkv.stat().st_size:
        return False
    if remote_dir and entry.get("remote_path") != remote_join(remote_dir, mkv.name):
        return False
    return True


def resolve_target(cfg: Config, target: str) -> Path:
    """Accept a capture directory, an .mkv path, or a bare timestamp name."""
    path = Path(target)
    for cand in (path, REPO_ROOT / path, local_root(cfg) / target):
        if cand.is_dir():
            return cand.resolve()
        if cand.is_file() and cand.suffix == ".mkv":
            return cand.resolve().parent
    sys.exit(f"no capture found for '{target}' (looked in {local_root(cfg)})")


# --------------------------------------------------------------------------- #
# the actual upload
# --------------------------------------------------------------------------- #
def probe_remote(cfg: Config, remote_dir: str, name: str) -> tuple:
    """Return (final_size, part_size, free_bytes); final_size is -1 when absent."""
    final = shlex.quote(remote_join(remote_dir, name))
    part = shlex.quote(remote_join(remote_dir, name + PART_SUFFIX))
    rdir = shlex.quote(remote_dir)
    probe = (
        f"mkdir -p {rdir} && "
        f"printf '%s %s %s\\n' "
        f'"$(stat -c %s {final} 2>/dev/null || echo -1)" '
        f'"$(stat -c %s {part} 2>/dev/null || echo 0)" '
        f'"$(df -Pk {rdir} | tail -1 | awk \'{{print $4}}\')"'
    )
    out = ssh_run(cfg, probe).stdout.split()
    if len(out) != 3:
        raise TransferError(f"unexpected probe output: {out!r}")
    return int(out[0]), int(out[1]), int(out[2]) * 1024


def stream_upload(cfg: Config, local: Path, remote_dir: str, offset: int,
                  hash_while_sending: bool):
    """Append local[offset:] to <remote_dir>/<name>.part over one ssh pipe."""
    total = local.stat().st_size
    part = shlex.quote(remote_join(remote_dir, local.name + PART_SUFFIX))
    rdir = shlex.quote(remote_dir)

    proc = subprocess.Popen(
        ssh_command(cfg) + [f"mkdir -p {rdir} && cat >> {part}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Drain stderr in the background: a chatty remote must not deadlock the pipe.
    stderr_chunks = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()),
                             daemon=True)
    drain.start()

    digest = hashlib.sha256() if hash_while_sending else None
    chunk = max(cfg.chunk_mb, 1) << 20
    sent = offset
    started = time.monotonic()
    broken = False
    try:
        with local.open("rb") as fh:
            fh.seek(offset)
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                proc.stdin.write(block)
                if digest is not None:
                    digest.update(block)
                sent += len(block)
                _progress("sending", sent, total, started)
    except OSError:                            # remote died mid-stream
        broken = True
    finally:
        try:
            proc.stdin.close()
        except OSError:
            broken = True

    rc = proc.wait()
    drain.join(timeout=5)
    stderr = b"".join(c for c in stderr_chunks if c).decode(errors="replace").strip()
    if rc != 0 or broken:
        raise TransferError(f"ssh exited {rc} during upload"
                            + (f":\n{stderr}" if stderr else ""))
    _progress_done("sending", total, started)
    if stderr:
        print(f"  remote said: {stderr}", file=sys.stderr)
    return digest.hexdigest() if digest is not None else None


def _entry(local: Path, remote_dir: str, sha: str, verified: bool) -> dict:
    return {
        "name": local.name,
        "bytes": local.stat().st_size,
        "sha256": sha,
        "verified": verified,
        "remote_path": remote_join(remote_dir, local.name),
        "sent_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def remote_sha256(cfg: Config, remote_path: str) -> str:
    started = time.monotonic()
    if sys.stderr.isatty():
        sys.stderr.write("  verifying on workstation ...")
        sys.stderr.flush()
    out = ssh_run(cfg, f"sha256sum {shlex.quote(remote_path)}").stdout.split()
    if sys.stderr.isatty():
        sys.stderr.write(f"\r  verifying on workstation ... "
                         f"{duration(time.monotonic() - started)}      \n")
    if not out:
        raise TransferError(f"sha256sum returned nothing for {remote_path}")
    return out[0]


def send_file(cfg: Config, local: Path, remote_dir: str, verify=None) -> dict:
    """Upload one file with resume + verification. Returns the state entry."""
    verify = cfg.verify if verify is None else verify
    total = local.stat().st_size
    attempts = max(cfg.retries, 1)
    final_remote = shlex.quote(remote_join(remote_dir, local.name))
    part_remote = shlex.quote(remote_join(remote_dir, local.name + PART_SUFFIX))
    print(f"\n-> {local.name}  ({human(total)})")

    for attempt in range(1, attempts + 1):
        final_size, part_size, free = probe_remote(cfg, remote_dir, local.name)

        if final_size == total:
            print("  already on the workstation with the same size.")
            if not verify:
                return _entry(local, remote_dir, "", verified=False)
            local_hash = sha256_file(local)
            if remote_sha256(cfg, remote_join(remote_dir, local.name)) == local_hash:
                print(f"  sha256 ok  {local_hash[:16]}...")
                return _entry(local, remote_dir, local_hash, verified=True)
            print("  checksum differs - sending it again.", file=sys.stderr)
            ssh_run(cfg, f"rm -f {final_remote}")
            continue

        if part_size > total:                  # stale or corrupt leftover
            print("  remote partial is larger than the source - restarting it.")
            ssh_run(cfg, f"rm -f {part_remote}")
            part_size = 0

        needed = total - part_size
        if free < needed:
            raise TransferError(f"workstation has {human(free)} free in {remote_dir} "
                                f"but {human(needed)} is needed.")
        if part_size:
            print(f"  resuming at {human(part_size)} "
                  f"({100 * part_size / total:.1f}% already there)")

        try:
            streamed_hash = stream_upload(cfg, local, remote_dir, part_size,
                                          hash_while_sending=part_size == 0)
        except TransferError as e:
            if attempt >= attempts:
                raise
            wait = min(2 ** attempt, 30)
            print(f"  {e}\n  attempt {attempt}/{attempts} failed - "
                  f"resuming in {wait}s ...", file=sys.stderr)
            time.sleep(wait)
            continue

        local_hash = ""
        if verify:
            local_hash = streamed_hash or sha256_file(local)
            if remote_sha256(cfg, remote_join(remote_dir,
                                              local.name + PART_SUFFIX)) != local_hash:
                ssh_run(cfg, f"rm -f {part_remote}")
                if attempt >= attempts:
                    raise TransferError(f"checksum mismatch for {local.name} "
                                        f"after {attempts} attempts")
                print("  checksum mismatch - resending from scratch.", file=sys.stderr)
                continue
            print(f"  sha256 ok  {local_hash[:16]}...")

        # Only now does the file get its real name, so the reconstruction scripts
        # on the workstation can never pick up a half-written recording.
        ssh_run(cfg, f"mv -f {part_remote} {final_remote}")
        return _entry(local, remote_dir, local_hash, verified=verify)

    raise TransferError(f"giving up on {local.name} after {attempts} attempts")


def send_capture(cfg: Config, capture_dir: Path, force: bool = False,
                 delete_local=None) -> bool:
    """Send every .mkv (plus small sidecars) in one capture directory."""
    delete_local = cfg.delete_local_after_send if delete_local is None else delete_local
    remote_dir = remote_join(cfg.remote_root, capture_dir.name)
    mkvs = sorted(capture_dir.glob("*.mkv"))
    if not mkvs:
        print(f"{capture_dir.name}: no .mkv inside - nothing to send.")
        return False

    print(f"\n=== {capture_dir.name} -> {cfg.destination}:{remote_dir} ===")
    sent_any = False
    for mkv in mkvs:
        if not force and is_sent(capture_dir, mkv, remote_dir):
            print(f"\n-> {mkv.name}: already sent (per {STATE_NAME}); "
                  f"use --force to resend.")
            continue
        write_state(capture_dir, send_file(cfg, mkv, remote_dir))
        sent_any = True

    for pattern in SIDECAR_GLOBS:              # metadata is tiny; always refresh
        for side in sorted(capture_dir.glob(pattern)):
            if side.name == STATE_NAME:
                continue
            write_state(capture_dir, send_file(cfg, side, remote_dir, verify=False))

    if delete_local and sent_any:
        for mkv in mkvs:
            if is_sent(capture_dir, mkv, remote_dir):
                mkv.unlink()
                print(f"  removed local {mkv.name} "
                      f"(the verified copy is on the workstation)")

    print(f"\nDone. On the workstation:\n"
          f"  python scripts/extract_mkv.py --input "
          f"{remote_join(remote_dir, mkvs[0].name)} --output data/myscan --every 1")
    return sent_any


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #
def cmd_check(cfg: Config, args) -> int:
    cfg.require_target()
    root = cfg.remote_root
    print(f"target: {cfg.destination}:{root}"
          + (f" (port {cfg.port})" if cfg.port else " (port from ~/.ssh/config)"))
    proc = ssh_run(cfg, f"echo ok && uname -sr && mkdir -p {shlex.quote(root)} && "
                        f"df -Ph {shlex.quote(root)} | tail -1",
                   batch=True, check=False)
    if proc.returncode != 0:
        print(proc.stderr.strip(), file=sys.stderr)
        print("\nkey-based login is not working yet. From PowerShell on this laptop:\n"
              "  ssh-keygen -t ed25519 -C ak3d-laptop\n"
              f"  type $env:USERPROFILE\\.ssh\\id_ed25519.pub | ssh {cfg.destination} "
              '"mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"\n'
              "Then run this check again.", file=sys.stderr)
        return 1
    lines = proc.stdout.strip().splitlines()
    print("connection: ok")
    if len(lines) > 1:
        print(f"remote:     {lines[1]}")
    if len(lines) > 2:
        print(f"free space: {lines[2]}")
    print("remote root ready.")
    return 0


def cmd_list(cfg: Config, args) -> int:
    dirs = capture_dirs(cfg)
    if not dirs:
        print(f"no captures under {local_root(cfg)}")
        return 0
    print(f"{'capture':<20} {'size':>10}  status")
    for d in dirs:
        state = read_state(d).get("files", {})
        for mkv in sorted(d.glob("*.mkv")):
            entry = state.get(mkv.name)
            if entry and entry.get("bytes") == mkv.stat().st_size:
                status = f"sent {entry.get('sent_at', '')} -> {entry.get('remote_path')}"
            elif entry:
                status = "stale (the local file changed since it was sent)"
            else:
                status = "pending"
            print(f"{d.name:<20} {human(mkv.stat().st_size):>10}  {status}")
    return 0


def cmd_send(cfg: Config, args) -> int:
    cfg.require_target()
    dirs = capture_dirs(cfg)
    if args.targets:
        selected = [resolve_target(cfg, t) for t in args.targets]
    elif args.latest:
        if not dirs:
            sys.exit(f"no captures under {local_root(cfg)}")
        selected = [dirs[-1]]
    elif args.all_pending:
        selected = [d for d in dirs
                    if any(not is_sent(d, m, remote_join(cfg.remote_root, d.name))
                           for m in d.glob("*.mkv"))]
        if not selected:
            print("nothing pending - every capture is already on the workstation.")
            return 0
    else:
        sys.exit("pick what to send: a path, --latest, or --all-pending")

    failures = 0
    for capture_dir in selected:
        try:
            send_capture(cfg, capture_dir, force=args.force,
                         delete_local=args.delete_local)
        except TransferError as e:
            failures += 1
            print(f"\nFAILED {capture_dir.name}: {e}", file=sys.stderr)
            print("  the partial upload is kept on the workstation - "
                  "rerun the same command to resume.", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help=f"path to {CONFIG_NAME}")
    ap.add_argument("--host")
    ap.add_argument("--user")
    ap.add_argument("--port", type=int)
    ap.add_argument("--identity-file")
    ap.add_argument("--remote-root")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify the ssh connection and remote root")
    sub.add_parser("list", help="show which captures are sent / pending")

    send = sub.add_parser("send", help="upload capture(s) to the workstation")
    send.add_argument("targets", nargs="*",
                      help="capture dir, .mkv path, or timestamp name")
    send.add_argument("--latest", action="store_true", help="send the newest capture")
    send.add_argument("--all-pending", action="store_true",
                      help="send every capture not recorded as sent")
    send.add_argument("--force", action="store_true", help="resend even if marked sent")
    send.add_argument("--no-verify", dest="verify", action="store_false", default=None,
                      help="skip the sha256 comparison (faster, less safe)")
    send.add_argument("--delete-local", dest="delete_local", action="store_true",
                      default=None,
                      help="delete the local .mkv once the copy is verified")
    send.add_argument("--keep-local", dest="delete_local", action="store_false",
                      help="keep the local .mkv (overrides the config default)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    for name in ("host", "user", "port", "identity_file", "remote_root"):
        value = getattr(args, name, None)
        if value:
            setattr(cfg, name, value)
    if getattr(args, "verify", None) is False:
        cfg.verify = False

    handler = {"check": cmd_check, "list": cmd_list, "send": cmd_send}[args.command]
    try:
        return handler(cfg, args)
    except TransferError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted - rerun the same command to resume where it stopped.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
