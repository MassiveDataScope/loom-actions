# Publishing from a caller

`release-on-label.yml` plans the release, tags it, writes the notes and creates the
GitHub release. With `build-distribution: true` it also builds a distribution and
leaves it as the `distributions` artifact of the run. `image-release.yml` builds the
tagged commit into an image and pushes it to GHCR and, optionally, Docker Hub.

Neither uploads to PyPI, and they cannot: PyPI's trusted publishing does not support
reusable workflows. The OIDC token minted for a reusable workflow names that workflow,
not the caller's, so PyPI answers

```
invalid-publisher: valid token, but no corresponding publisher
```

no matter how the trusted publisher is configured. Observed on loom-py's v1.11.0.

So the caller publishes, in its own workflow file, which is the file PyPI's trusted
publisher names.

## A complete caller

The release of a package that lives in `apps/api` of a monorepo, with its image:

```yaml
name: release

on:
  pull_request:
    types: [closed]
    branches: [master]
  workflow_dispatch:
    inputs:
      merge_sha:
        description: "Commit on master to release, to resume a run that stopped halfway"
        type: string
        required: true

# One release at a time, and a second merge waits instead of cancelling the first.
concurrency:
  group: release-${{ github.workflow }}
  cancel-in-progress: false

permissions: {}

jobs:
  release:
    permissions:
      contents: write
    uses: the-reacher-data/loom-actions/.github/workflows/release-on-label.yml@<sha> # vX.Y.Z
    with:
      build-distribution: true
      package-name: periplo
      package-dir: apps/api
      semantic-branch-config: apps/api/pyproject.toml
      check-distribution: true
      python-version: "3.12"
      merge-sha: ${{ inputs.merge_sha || '' }}

  image:
    needs: release
    if: ${{ needs.release.result == 'success' && needs.release.outputs.version != '' }}
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    uses: the-reacher-data/loom-actions/.github/workflows/image-release.yml@<sha> # vX.Y.Z
    with:
      version: ${{ needs.release.outputs.version }}
      expected-sha: ${{ github.event.pull_request.merge_commit_sha || inputs.merge_sha }}
      ghcr-image: ghcr.io/<owner>/<image>
      dockerhub-image: <namespace>/<image>
      platforms: "linux/amd64,linux/arm64"
      dockerfile: Dockerfile
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}

  publish:
    needs: release
    if: ${{ needs.release.outputs.distribution-built == 'true' }}
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@<sha> # v4
        with:
          name: distributions
          path: dist
      # skip-existing so a re-run finishes an upload that landed one file and failed
      # on the next: a version on PyPI cannot be replaced.
      - uses: pypa/gh-action-pypi-publish@<sha> # release/v1
        with:
          skip-existing: true
```

- `package-dir` is where `uv lock --check`, the build and the wheel name check run, and
  `<package-dir>/dist/` is what gets uploaded. A package at the root leaves it at `.`.
- `semantic-branch-config` is the file whose `[tool.semantic_branch]` decides the version,
  relative to the root. Put `dependabot/.*` in its `release_ignore` if dependency updates
  must never release on their own.
- The build checks out the tag with its full history, so hatch-vcs or setuptools-scm read
  the version from the tag. A wheel with any other version fails the build.
- `check-distribution: true` runs `twine check --strict` over the wheel and the sdist before
  they are stored, so a README that PyPI would refuse to render fails here, not at upload.
- `distribution-built` is `true` only once the distributions were built, checked and stored.
  A merge without the label, or a caller that does not build, leaves it `false` and
  `version` empty, so neither `image` nor `publish` runs.
- A run that stopped after tagging is resumed with `workflow_dispatch` and the merge commit
  as `merge_sha`: the tag is reused when it points at that commit, and `skip-existing`
  finishes a partial upload.
- A repository that ships an application leaves `build-distribution` off and drops the
  `publish` job: it gets the tag, the notes and the GitHub release, and no index account.

## PyPI with trusted publishing

1. On PyPI, add a trusted publisher to the project, or a *pending* publisher if the project
   does not exist yet: owner and repository of the **caller**, workflow `release.yml` (the
   caller's file, not `release-on-label.yml`), environment `pypi`.
2. In the caller repository, create the environment `pypi`. Required reviewers are the way to
   hold an upload for approval. Do not limit its deployment branches to `master`: a run
   started by a `pull_request` event does not run on the `master` ref, so such a rule would
   refuse the upload of every labelled merge.
3. No PyPI token is stored anywhere; the `publish` job only needs `id-token: write`.
   `gh-action-pypi-publish` also uploads PEP 740 attestations for the files it publishes.

## Images on GHCR and Docker Hub

- **GHCR** needs no secret: `image-release` logs in with the run's `GITHUB_TOKEN`, which the
  caller's `packages: write` allows to push. The first push creates the package under the
  owner of the repository; in an organisation it starts private, so make it public from the
  package settings. If the package already exists and belongs to no repository, grant the
  caller repository write access from *Manage Actions access*.
- **Docker Hub** is opt-in: pass `dockerhub-image` and the secrets `DOCKERHUB_USERNAME` and
  `DOCKERHUB_TOKEN`, an access token with read and write scope. Without `dockerhub-image` the
  image goes to GHCR only; with it but without both secrets the workflow fails before any
  login.
- Each release pushes `X.Y.Z`, `X.Y` and, for the highest version, `latest`, for every
  platform, with an SBOM and `provenance: mode=max`.

## Verifying the image attestation

In a public repository, `image-release` attests the pushed digest on GHCR and, with Docker
Hub, on Docker Hub too, and pushes the attestation to the registry. The attestation is
signed by the workflow that built the image, which lives in loom-actions, so the check
names both repositories:

```bash
gh attestation verify oci://ghcr.io/<owner>/<image>:X.Y.Z \
  --repo <owner>/<repo> \
  --signer-repo the-reacher-data/loom-actions

gh attestation verify oci://docker.io/<namespace>/<image>:X.Y.Z \
  --repo <owner>/<repo> \
  --signer-repo the-reacher-data/loom-actions
```

`--repo` is the caller, whose commit was built; `--signer-repo` is where the signing
workflow lives. To pin the workflow itself, pass
`--signer-workflow the-reacher-data/loom-actions/.github/workflows/image-release.yml`
**instead of** `--signer-repo`: `gh` refuses both at once. `gh` must be logged in
(`gh auth login`) to read the attestations.

A private repository gets a notice instead of an attestation (artifact attestations need
GitHub Enterprise Cloud there); the image still carries its BuildKit provenance and SBOM.
