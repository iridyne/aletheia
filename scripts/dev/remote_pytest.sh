#!/usr/bin/env bash
# Run pytest for this checkout on a remote Linux host instead of the local machine.
#
#   scripts/dev/remote_pytest.sh tests/test_x.py -q -n 4
#   scripts/dev/remote_pytest.sh tests -q -n 4 -m "domain_execution or domain_risk"
#
# The working tree, including uncommitted edits, is rsynced to
# ~/$REMOTE_TEST_ROOT/<repo>-<checkout> on $REMOTE_TEST_HOST. uv syncs the
# dependencies there, and pytest runs inside a sandbox:
#   - a systemd user scope caps CPU and memory, so the host's own services keep priority;
#   - user+net+pid namespaces give the run a private loopback with no network
#     and no view of host processes, so a test can neither reach a live service
#     on the host nor signal one of its processes;
#   - ALETHEIA_DATA_DIR and TMPDIR point at a fresh directory, never at ~/.aletheia;
#   - the remote tree is a throwaway git repo with one snapshot commit, for code that reads
#     the commit or `git ls-files`. Tests that need the local jj workspace still run locally.
# Requirements: ssh, rsync and git locally; uv, git, systemd (user session) and unshare remotely.
set -euo pipefail

HOST="${REMOTE_TEST_HOST:-${ALETHEIA_TEST_HOST:-u7p}}"
ROOT="${REMOTE_TEST_ROOT:-remote-test}"
CPU="${REMOTE_TEST_CPU_QUOTA:-800%}"
MEM="${REMOTE_TEST_MEMORY_MAX:-4G}"

top="$(git rev-parse --show-toplevel)"
repo="$(basename "$(dirname "$(git -C "$top" rev-parse --path-format=absolute --git-common-dir)")")"
dir="$ROOT/$repo-$(basename "$top")"

# Send the tree as it is on disk, minus VCS metadata and ignored files.
# --delete carries local deletions over, so a removed test is not collected remotely.
excludes="$(mktemp)"
trap 'rm -f "$excludes"' EXIT
printf '%s\n' /.git /.jj /.venv >"$excludes"
git -C "$top" ls-files -oi --exclude-standard --directory | sed 's|^|/|' >>"$excludes"
ssh "$HOST" "mkdir -p '$dir'"
rsync -az --delete --exclude-from="$excludes" "$top/" "$HOST:$dir/"

# Hand the pytest arguments over as one base64 blob, so quoting survives the ssh hop.
args="$(printf '%q ' "$@" | base64 | tr -d '\n')"

ssh "$HOST" bash -s <<EOF
set -euo pipefail
cd '$dir'
exec 9>.remote-test.lock
flock 9
eval "set -- \$(echo '$args' | base64 -d)"
uv sync --frozen --quiet
[ -d .git ] || git init -q
git add -A
git -c user.name=remote-test -c user.email=remote-test@localhost commit -q --allow-empty --no-verify --amend -m remote-test-snapshot 2>/dev/null \\
  || git -c user.name=remote-test -c user.email=remote-test@localhost commit -q --allow-empty --no-verify -m remote-test-snapshot
run=\$(mktemp -d /var/tmp/remote-test.XXXXXX)
trap 'rm -rf "\$run"' EXIT
systemd-run --user --scope --quiet -p CPUQuota=$CPU -p MemoryMax=$MEM -- \\
  unshare --user --map-root-user --net --pid --fork --mount-proc \\
  env ALETHEIA_DATA_DIR="\$run/data" TMPDIR="\$run" \\
  bash -c 'ip link set lo up && { uv run --no-sync pytest "\$@"; rc=\$?; exit \$rc; }' _ "\$@"  # not exec'd: bash stays PID 1 and reaps orphans
EOF
