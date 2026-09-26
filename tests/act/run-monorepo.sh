#!/usr/bin/env bash
# Run python-service-ci over examples/monorepo under act.
#
# act's artifact server only speaks the protocol of upload-artifact and
# download-artifact v4, and python-service-ci pins v7 and v8, so the test
# results never reach the report. This runs a throwaway copy of the checkout
# in which those two pins are v4; the workflow in the repository is untouched.
#
# PYTHON_VERSIONS, a JSON array like '["3.12","3.14"]', becomes the example's
# python-versions; unset, the tests run on its python-version alone.
set -euo pipefail

root=$(git rev-parse --show-toplevel)
work=$(mktemp -d)
artifacts=$(mktemp -d)
trap 'rm -rf "${work}" "${artifacts}"' EXIT

git -C "${root}" ls-files --cached --others --exclude-standard \
  | rsync -a --files-from=- "${root}/" "${work}/"

workflow="${work}/.github/workflows/python-service-ci.yml"
swap() {
  if ! grep -qF "$1" "${workflow}"; then
    echo "run-monorepo: '$1' is no longer in python-service-ci.yml; update this script" >&2
    exit 1
  fi
  sed -i.bak "s|$1|$2|" "${workflow}"
}
swap "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1" \
  "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"
swap "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1" \
  "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0"
rm -f "${workflow}.bak"

cd "${work}"
git init -q
git add -A
git -c user.name=act -c user.email=act@localhost commit -qm "act run of examples/monorepo"
variables=()
if [ -n "${PYTHON_VERSIONS:-}" ]; then
  variables=(--var "PYTHON_VERSIONS=${PYTHON_VERSIONS}")
fi
act pull_request \
  -W examples/monorepo/.github/workflows/ci.yml \
  -e tests/act/events/pull_request.json \
  --artifact-server-path "${artifacts}" \
  ${variables[@]+"${variables[@]}"}
