---
name: casi-pytest
description: Run Aletheia pytest on CASI's WSL (Linux) instead of the Mac or Fedora. Use whenever tests must run — focused tests, dev_check's pytest steps, or a bigger -m domain_* slice. Fedora's RAM is reserved for the live runtime.
---

# Run pytest on CASI

pytest never runs on the Mac (heat) or on Fedora `u7p` (14 GB, live runtime, repeated OOM on 2026-10-10).
CASI (`ssh casi`, Windows, 64 GB / 24 cores) hosts a WSL2 distro `ubuntu-runner` (Ubuntu 24.04, user
`runner`, 32 GB / 16 cores, systemd, uv/rsync/flock/unshare, Python 3.12 — the repo needs `>=3.12`).

Facts that shape the commands:
- `ssh casi` lands in **PowerShell**, and WSL has **no sshd**. Reach WSL as `ssh casi wsl -d ubuntu-runner -- <cmd>`.
- PowerShell eats quotes and `$`, so anything non-trivial goes into a **file → `scp` → run**, never inline.
- Remote output may contain NULs: pipe through `tr -d '\0'`.
- rsync's destination must be an **absolute WSL path**; a relative one lands in the Windows cwd.
- Do not install sshd or change firewall rules on CASI for this; ask the owner first.

## Run

Define and call this from the checkout you want to test (any jj workspace; uncommitted edits are sent).
Args are ordinary pytest args, e.g. `casi_pytest tests/test_x.py -q -n 4` or
`casi_pytest tests -q -n 3 -m "domain_risk or domain_execution"`.

```bash
casi_pytest() {
  local top name dir ex shim run args
  top="$(git rev-parse --show-toplevel)"; name="aletheia-$(basename "$top")"
  dir="/home/runner/remote-test/$name"
  ex="$(mktemp)"; shim="$(mktemp)"; run="$(mktemp)"
  printf '%s\n' /.git /.jj /.venv >"$ex"
  git -C "$top" ls-files -oi --exclude-standard --directory | sed 's|^|/|' >>"$ex"
  printf '#!/bin/sh\nshift; exec ssh casi wsl -d ubuntu-runner -- "$@"\n' >"$shim"; chmod +x "$shim"
  ssh casi "wsl -d ubuntu-runner -- mkdir -p /home/runner/remote-test" | tr -d '\0'
  rsync -az --delete --exclude-from="$ex" -e "$shim" "$top/" "casi:$dir/" || return
  args="$(printf '%q ' "$@")"
  cat >"$run" <<EOF
set -euo pipefail
cd '$dir'
exec 9>.remote-test.lock; flock 9                       # one run per checkout
eval "set -- $args"
uv sync --frozen --quiet
# throwaway one-commit repo (as remote_pytest.sh): tests read HEAD and git ls-files
[ -d .git ] || git init -q
git add -A
git -c user.name=remote-test -c user.email=remote-test@localhost commit -q --allow-empty --no-verify --amend -m remote-test-snapshot 2>/dev/null \\
  || git -c user.name=remote-test -c user.email=remote-test@localhost commit -q --allow-empty --no-verify -m remote-test-snapshot
tmp=\$(mktemp -d /var/tmp/remote-test.XXXXXX); trap 'rm -rf "\$tmp"' EXIT
# 2G throttle / 4G kill, private loopback, fresh data dir: never ~/.aletheia
systemd-run --user --scope --quiet -p CPUQuota=800% -p MemoryHigh=2G -p MemoryMax=4G -- \\
  unshare --user --map-root-user --net --pid --fork --mount-proc \\
  env ALETHEIA_DATA_DIR="\$tmp/data" TMPDIR="\$tmp" \\
  bash -c 'ip link set lo up && { uv run --no-sync pytest "\$@"; rc=\$?; exit \$rc; }' _ "\$@"
EOF
  scp -q "$run" "casi:C:/Users/yixian/casi-pytest-$name.sh" || return
  ssh casi "wsl -d ubuntu-runner -- bash /mnt/c/Users/yixian/casi-pytest-$name.sh" 2>&1 | tr -d '\0'
  return "${PIPESTATUS[0]}"
}
```

`ALETHEIA_TEST_HOST` stays unset: it makes `dev_check` call `scripts/dev/remote_pytest.sh`, which assumes an
sshd + rsync host and does not work against CASI. Run `dev_check` for its static steps and call
`casi_pytest` for the tests it selects.

## Check and clean up

- Verified 2026-10-10: 101 tests (candidate selection / funding / checkpoint) pass in 12 s with `-n 4`.
- The first run per checkout does `uv sync` (about a minute); later runs reuse `.venv`.
- Stale checkouts accumulate in `/home/runner/remote-test` on CASI. Remove old ones with
  `ssh casi wsl -d ubuntu-runner -- rm -rf /home/runner/remote-test/<name>` when the workspace is gone.
- CASI also runs vLLM on its GPU. The CPU quota and memory cap above keep pytest from starving it; do not raise them.
