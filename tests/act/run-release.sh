#!/usr/bin/env bash
# Run the build of release-on-label under act over a clone of a caller.
#
#   tests/act/run-release.sh <caller checkout> <package-name> [input=value ...]
#
# e.g. tests/act/run-release.sh ../loom-py loom-kernel
#      tests/act/run-release.sh ../nautilus-ui periplo package-dir=apps/api \
#        semantic-branch-config=apps/api/pyproject.toml check-distribution=true python-version='"3.12"'
#
# Each value is written as YAML as given, so quote one that must stay a string.
#
# The plan and release jobs talk to the GitHub API (pull requests, tags,
# releases), so the harness replaces plan with a stub that outputs 9.9.9 and
# drops release; the build job is this repository's, except that its checkout
# takes no ref, because act only substitutes the local copy for a checkout of
# the current commit. The caller is cloned into a temporary directory, tagged
# v9.9.9 there and given an unreachable remote: nothing is pushed or published.
# A last job downloads the distributions the way a caller's publish job does.
set -euo pipefail

if [ "$#" -lt 2 ]; then
  sed -n '2,10p' "$0" >&2
  exit 2
fi

root=$(cd "$(dirname "$0")/../.." && pwd)
caller=$1
package=$2
shift 2

work=$(mktemp -d)
artifacts=$(mktemp -d)
trap 'rm -rf "${work}" "${artifacts}"' EXIT

git clone -q "${caller}" "${work}/caller"
cd "${work}/caller"
git -c user.name=act -c user.email=act@localhost tag v9.9.9
git remote set-url origin "https://github.com/example/$(basename "${caller}")-act.git"
git remote set-url --push origin "no-push://disabled"

mkdir -p .github/workflows
uv run --no-project --with pyyaml python - "${root}/.github/workflows/release-on-label.yml" <<'PY'
import sys
from pathlib import Path

import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
workflow = {("on" if key is True else key): value for key, value in workflow.items()}
jobs = workflow["jobs"]
jobs["plan"] = {
    "runs-on": "ubuntu-latest",
    "permissions": {"contents": "read"},
    "outputs": {"version": "${{ steps.plan.outputs.version }}"},
    "steps": [{"id": "plan", "shell": "bash", "run": 'echo "version=9.9.9" >> "$GITHUB_OUTPUT"'}],
}
del jobs["release"]
checkout = jobs["build"]["steps"][0]
assert "actions/checkout" in checkout["uses"], checkout
checkout["with"].pop("ref")
target = Path(".github/workflows/release-on-label-harness.yml")
target.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
PY

{
  cat <<'YAML'
name: act-release
on: workflow_dispatch
jobs:
  release:
    permissions:
      contents: write
    uses: ./.github/workflows/release-on-label-harness.yml
    with:
      build-distribution: true
YAML
  printf '      package-name: "%s"\n' "${package}"
  for pair in "$@"; do
    printf '      %s: %s\n' "${pair%%=*}" "${pair#*=}"
  done
  cat <<'YAML'
  publish:
    needs: release
    if: ${{ needs.release.outputs.distribution-built == 'true' }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        with:
          name: distributions
          path: dist
      - shell: bash
        env:
          BUILT: ${{ needs.release.outputs.distribution-built }}
          VERSION: ${{ needs.release.outputs.version }}
        run: |
          echo "distribution-built=${BUILT} version=${VERSION}"
          ls -1 dist
YAML
} > .github/workflows/act-release.yml

act workflow_dispatch \
  -W .github/workflows/act-release.yml \
  -P ubuntu-latest=ghcr.io/catthehacker/ubuntu:act-latest \
  --artifact-server-path "${artifacts}"
