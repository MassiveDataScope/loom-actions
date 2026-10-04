# loom-actions

[![CI PR](https://github.com/MassiveDataScope/loom-actions/actions/workflows/ci-pr.yml/badge.svg)](https://github.com/MassiveDataScope/loom-actions/actions/workflows/ci-pr.yml)
[![CI Main](https://github.com/MassiveDataScope/loom-actions/actions/workflows/ci-main.yml/badge.svg?branch=master)](https://github.com/MassiveDataScope/loom-actions/actions/workflows/ci-main.yml)
[![Pyright](https://img.shields.io/badge/Pyright-type%20checked-2b5b84?logo=microsoft&logoColor=white)](https://github.com/microsoft/pyright)
[![Ruff](https://img.shields.io/badge/Ruff-lint-111111?logo=ruff&logoColor=white)](https://docs.astral.sh/ruff/)
![License](https://img.shields.io/github/license/MassiveDataScope/loom-actions)

Reusable GitHub Actions for Python projects using Trunk-Based Development and Conventional Commits.

## Features

- Trunk-based release flow on `master`
- Semantic versioning from merged branch name, raised to a major by a commit that declares a break (`type!:` or a `BREAKING CHANGE:` footer)
- Changelog generation from Conventional Commits + PR preview comment
- Unified Python quality report in PRs (ruff + pyright + pytest/coverage + bandit)
- Local and CI test strategy (`make` + `act` + GitHub workflows)

## Actions Overview

| Category | Action | Description | Status |
|---|---|---|---|
| Core | `actions/core/pr-comment-update` | Create/update PR comment identified by hidden tags | ✅ Ready |
| Core | `actions/core/setup-uv` | Setup Python + uv toolchain | ✅ Ready |
| Release | `actions/release/versioning-branch-semantic` | Calculate semantic version based on branch rules | ✅ Ready |
| Release | `actions/release/changelog-conventional-commit` | Build changelog markdown from Conventional Commits (its own Jinja format, not the `changelog: true` one) | ✅ Ready |
| Release | `actions/release/plan-release/preview` | Preview on a pull request the version and changelog section a `changelog: true` release would write | ✅ Ready |
| Python | `actions/python/quality-report` | Aggregated quality/security report and fail gates | ✅ Ready |

## Reusable Workflows

| Workflow | Purpose |
|---|---|
| `.github/workflows/python-service-ci.yml` | CI for a Python service shipped as a container image: lint, tests, quality report, image build/smoke/scan, optional Codecov/SonarQube/Snyk, one `gate` check |
| `.github/workflows/release-on-label.yml` | Trunk-based release: tag, notes and GitHub Release when a pull request labelled `release` merges; building a distribution is opt-in |
| `.github/workflows/node-ci.yml` | CI for npm workspaces: lint, type-check, tests with repository-relative lcov per workspace, build, Playwright end-to-end on Chromium, optional Codecov, one `gate` check |
| `.github/workflows/repo-security.yml` | gitleaks over the full history, CodeQL, dependency review and a command of the caller's with no secret, one `gate` check |
| `.github/workflows/pages.yml` | Builds a static site with a read-only token and uploads it as the Pages artifact; the caller deploys |
| `.github/workflows/image-release.yml` | Publishes a release image to GHCR and optionally Docker Hub: multi-arch, `X.Y.Z`/`X.Y`/`latest`, SBOM, provenance and an attestation |

### python-service-ci

```yaml
name: ci
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

permissions: {}

jobs:
  ci:
    permissions:
      contents: read
      pull-requests: write
    uses: MassiveDataScope/loom-actions/.github/workflows/python-service-ci.yml@<sha> # vX.Y.Z
    with:
      python-version: "3.13"
      image-smoke-command: python -c "import app.main"
      sonar: true
      sonar-project-key: ${{ vars.SONAR_PROJECT_KEY }}
    secrets:
      SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
```

| Job | Runs | Blocks on |
|---|---|---|
| `versions` | always | a `python-versions` or `python-versions-experimental` that is not a JSON array of versions, or, when either is set, a `python-version` that is not `3.N` |
| `lint` | always | `uv sync --locked`, ruff, ruff format, mypy (`typecheck`) |
| `test (<v>)` | once per version in `python-versions` | pytest failures on any version; coverage under `coverage-threshold` on `python-version` |
| `test-experimental (<v>)` | once per version in `python-versions-experimental` | nothing: failures show but never reach the `gate` |
| `report` | always | bandit at `fail-on-security`; posts the quality report to the PR and the job summary; uploads to Codecov when `codecov` |
| `sonar` | `sonar: true` | missing `SONAR_TOKEN` or `sonar-project-key`, scanner failure; neither with `sonar-blocking: false` |
| `dependencies` | `snyk: true` | missing `SNYK_TOKEN`, vulnerable locked runtime dependency (a scanner outage only warns) |
| `image` | `image: true` | build, `image-smoke-command`, fixable vulnerabilities at `image-scan-severity` |
| `branch` | pull requests | a branch name no `[tool.semantic_branch]` class matches, or a missing rules file |
| `gate` | always | any job above failed or was cancelled |

- Require only `ci / gate` in branch protection.
- Codecov, SonarQube and Snyk are off by default and need no secret while off. Secrets are
  passed explicitly (`secrets: inherit` does not cross organizations).
- The repository must commit `uv.lock`; every install runs with `--locked`.
- A pull request from a fork skips the secret-dependent jobs and the PR comment.
- Nothing is pushed or deployed. The image uses the `gha` layer cache (`scope=image`), shared
  by pull requests and the trunk, so a later publishing stage can reuse the tested layers.
  Its BuildKit is pinned by the digest `image-release` uses.
- `codecov-flag` sets the Codecov flag of both uploads, for a repository that splits its
  coverage by component. It is empty by default: the uploads carry no flag, as before.
- `sonar-blocking: false` makes Sonar informative: a scan that fails, or `sonar: true`
  without `SONAR_TOKEN` or `sonar-project-key`, leaves a notice and the `gate` ignores it.
  It is `true` by default, so a Sonar failure blocks as before.

#### Testing on several Python versions

```yaml
    with:
      python-version: "3.14"
      python-versions: '["3.12", "3.14"]'
      python-versions-experimental: '["3.15-dev"]'
```

- `python-version` is the primary version: lint, types, the coverage threshold, the quality
  report, Codecov, Sonar, Snyk and the image run once, on it.
- `python-versions` is a JSON array of `3.N` strings. Each version is a `test (<v>)` leg,
  run in parallel with `fail-fast: false`, and the `gate` requires every leg. Duplicates run
  once and `python-version` is added when missing.
- `python-versions-experimental` accepts `3.N` and `3.N-dev`, installed with prereleases
  allowed. Its `test-experimental (<v>)` legs show their failures without blocking; a
  version the runner cannot install yet leaves a warning.
- A list that is not a JSON array of versions fails `versions`, naming the value, before any
  test runs. A version the lock does not cover (`requires-python`, `uv.lock`) fails its leg
  and says so: widen `requires-python`, run `uv lock` and commit it.
- Each leg installs the lock for its version (`uv sync --locked --python <v>`), keys the uv
  cache on it and uploads `test-results-<v>`. The primary also uploads `test-results`, the
  artifact `report` and `sonar` read.
- To test on one version only, omit `python-versions` or pass `'["3.14"]'`. Without either
  list, `test` runs once, as `test (<python-version>)`, after `versions`, and uploads
  `test-results`.

To reproduce a leg locally, run the tests with that interpreter; uv fetches it when it is
missing and nothing else is needed:

```sh
uv run --locked --python 3.12 pytest
uv run --locked --python 3.14 pytest
```

tox or nox remain an option for a local loop.

#### A service in a monorepo

When the Python project lives in a subdirectory, with its own `pyproject.toml` and
`uv.lock`, pass `working-directory`:

```yaml
    with:
      python-version: "3.12"
      working-directory: apps/api
      semantic-branch-config: apps/api/pyproject.toml
      image-context: .
      dockerfile: Dockerfile
```

| Input | Default | Relative to | Used by |
|---|---|---|---|
| `working-directory` | `.` | repository root, no trailing slash | `uv sync`, ruff, mypy, pytest, bandit, the quality report and Snyk run there; `uv.lock` is read from it |
| `src-dir`, `test-dir` | `src`, `tests` | `working-directory` | mypy, pytest, the quality report, Sonar |
| `semantic-branch-config` | empty: `<working-directory>/pyproject.toml` | repository root | `branch` |
| `image-context`, `dockerfile` | `.`, `Dockerfile` | repository root | `image` |

- Sonar and Codecov run from the repository root, so a `sonar-project.properties` there is
  honoured; the sources, tests and reports they receive carry the `working-directory`
  prefix (`apps/api/src`, `apps/api/coverage.xml`).
- The paths inside `coverage.xml` are relative to the project, so a monorepo maps them for
  Codecov with `fixes` in its `codecov.yml`.
- With the defaults every path is the one used before, and the jobs are the same.
- [`examples/monorepo`](examples/monorepo) is a runnable caller: `make act-monorepo`.

### release-on-label

Merging a pull request that carries the `release` label plans the version from the branches
merged since the last tag, creates the immutable tag `vX.Y.Z`, moves the major tag `vX`,
writes the notes and creates the GitHub Release. With `build-distribution: true` it also
builds the package at the tag and leaves it as the `distributions` artifact; the caller
uploads it, because PyPI's trusted publishing does not accept a reusable workflow. See
[PUBLISHING.md](PUBLISHING.md).

```yaml
jobs:
  release:
    permissions:
      contents: write
      pull-requests: read # the planner reads the pull request of each merged commit
    uses: MassiveDataScope/loom-actions/.github/workflows/release-on-label.yml@<sha> # vX.Y.Z
    with:
      build-distribution: true
      package-name: periplo
      package-dir: apps/api
      semantic-branch-config: apps/api/pyproject.toml
      check-distribution: true
      python-version: "3.12"
      merge-sha: ${{ inputs.merge_sha || '' }}
```

A monorepo package that releases on its own passes its directory and a tag prefix of its
own. With the path-style prefix of the Go modules convention, it is tagged
`control-plane/v0.1.0`, moves `control-plane/v0` and never reads another package's tags:

```yaml
jobs:
  release:
    permissions:
      contents: write
      pull-requests: read # the planner reads the pull request of each merged commit
    uses: MassiveDataScope/loom-actions/.github/workflows/release-on-label.yml@<sha> # vX.Y.Z
    with:
      build-distribution: true
      package-name: control-plane
      package-dir: apps/control-plane
      semantic-branch-config: apps/control-plane/pyproject.toml
      tag-prefix: control-plane/v
      scope-to-package: true # ships only the commits touching apps/control-plane ...
      shared-paths: uv.lock # ... or uv.lock
      changelog: true # keeps apps/control-plane/CHANGELOG.md
```

| Input | Default | Effect |
|---|---|---|
| `release-label` | `release` | label that authorises a release when its pull request merges |
| `base-branch` | `master` | branch the release commit must be on |
| `build-distribution` | `false` | build a wheel and an sdist at the tag; needs `package-name` |
| `package-name` | empty | distribution name; the wheel must be `<name>-<version>-py3-none-any.whl` |
| `package-dir` | `.` | directory of the package, relative to the root, with no leading or trailing `/` and no empty segment (checked before anything runs): `uv lock --check`, the build and the wheel name check run there, and `<package-dir>/dist/` is uploaded |
| `semantic-branch-config` | `pyproject.toml` | TOML file, relative to the root, whose `[tool.semantic_branch]` decides the version |
| `check-distribution` | `false` | run `twine check --strict` (twine 7.0.0) on every built distribution before storing it |
| `tag-prefix` | `v` | prefix of the release tags: the planner reads `<prefix>X.Y.Z`, the workflow creates it, moves `<prefix>X`, builds at it and names the GitHub Release after it |
| `scope-to-package` | `false` | ship only the commits touching `package-dir` (not the root) or one of `shared-paths`; `false` ships every commit whatever `package-dir` is, as before |
| `shared-paths` | empty | with `scope-to-package`, the paths the package shares with the others, separated by newlines or commas, such as `uv.lock`; refused without it |
| `changelog` | `false` | keep `<package-dir>/CHANGELOG.md` in the Keep a Changelog 1.1.0 format, commit it to `base-branch` and use the new section as the GitHub Release body |
| `python-version`, `uv-version` | `3.11`, `0.10.2` | toolchain of the build |
| `merge-sha` | empty | commit to release, to resume a run that stopped halfway |

| Output | Value |
|---|---|
| `version` | the version released, `X.Y.Z`; empty when nothing was released |
| `distribution-built` | `true` once the distributions were built, checked and stored; `false` otherwise |
| `changelog-committed` | `true` when `base-branch` holds the changelog with the release, committed by this run or already; `false` without `changelog`, or when the commit failed after the release was published |

- The planner (`actions/release/plan-release`) and its `commit-changelog` composite are
  pinned by the commit of a release, so a caller's SHA pin on this workflow also fixes the
  planner it runs. Both pins point at the v1.11.0 release, the first to hold the `paths`
  input and the `commit-changelog` composite this workflow uses.
- The workflow passes `tag-prefix` (`v` by default) to the planner it pins (v1.11.0), which
  reads only the tags `<prefix>X.Y.Z` to find the last release and write the notes, so a
  monorepo package released as `control-plane/v0.1.0` never plans from another package's
  tags. The same prefix names the tag the workflow creates, the major tag it moves, the tag
  the build checks out and the GitHub Release. Letters, digits, `.`, `_`, `-` and `/` only,
  starting with a letter or a digit, without `..`, `//`, `/.` or `.lock/`: the workflow
  checks it with the planner's rule before it plans, tags or releases anything.
- Scoping is opt-in. With `scope-to-package: true` the planner reads only the commits
  touching `package-dir` or one of the `shared-paths`
  (`git log --no-merges --full-history <range> -- <paths>`, with literal pathspecs) to pick
  the version, raise a declared break to a major, and write the changelog and the notes, so
  another package's pull requests never reach it. A labelled merge touching none of those
  paths, but the package's own changelog commits, releases nothing: the planner says why in
  the step summary, no tag, build or release follows, `version` is empty and the run is
  green. A monorepo caller can add the same paths to its `pull_request` trigger so such a
  merge does not run the package's release at all. No path may be absolute, hold `..`, or
  start with `-` or `:`; the planner refuses one before it reads git.
- Left `false`, the default, no path reaches the planner and every commit since the last
  `<prefix>` tag ships, whatever `package-dir` is: keep it so for a package built from files
  outside its directory, such as an image built from the root.
- The build checks out the tag with its full history, so a version read from git (hatch-vcs,
  setuptools-scm) is the tag's. A wheel with any other version fails the build instead of
  burning a version on the index.
- With the defaults, a package at the root is built, checked and uploaded exactly as before.
- A merge without the label runs nothing: `version` is empty and `distribution-built` is not
  `true`. Gate the caller's
  image job on `needs.release.outputs.version != ''` and its upload on
  `needs.release.outputs.distribution-built == 'true'`.
- A merge whose branches all belong to `release_ignore` (for example `dependabot/.*`) fails
  the plan with "nothing to release": no tag, no image, no upload. So does an unscoped range
  holding no commit.

#### The changelog

With `changelog: true` the planner adds the release to `<package-dir>/CHANGELOG.md` (the
root's `CHANGELOG.md` for `package-dir: .`), taken from the head of `base-branch` and created
when missing, before any tag is written. The GitHub Release body is the new section, with
its link. Once the release exists, the release job commits the file to `base-branch` through
the contents API as `docs(release): changelog for <prefix>X.Y.Z`.

- [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/): the standard header and
  intro, `## [Unreleased]` kept on top, newest version first, `## [X.Y.Z] - YYYY-MM-DD`, the
  sections Added, Changed, Deprecated, Removed, Fixed and Security in that order with empty
  ones left out, and link references at the bottom: `[X.Y.Z]` compares the previous
  `<prefix>` tag with the new one, `[unreleased]` compares the new one with `HEAD`. An
  existing file without an `## [Unreleased]` heading fails the plan; notes written by hand
  under it are kept there.
- [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html): the heading carries `X.Y.Z`, never the
  prefix. [ISO 8601](https://www.iso.org/iso-8601-date-and-time-format.html): the date is the
  UTC calendar date of the release commit, so a re-run writes the same one.
- One entry per merged pull request, from its title, linked to it. A pull request merged
  with a merge commit brings every commit of its branch into the range, intermediate commits
  and their reverts included; its title says what changed for a user. A commit with no pull
  request is listed by its subject.
- The title is a [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
  header: `feat` is Added; `fix` is Fixed, or Security when the scope, or one of its
  comma-separated parts, is `sec` or `security`; `perf`, `refactor` and `revert` are
  Changed; `deprecate` is Deprecated and `remove` is Removed, the only titles that fill
  those sections; `build`, `chore`, `ci`, `docs`, `style` and `test` are left out. Any other
  type, `!` or not, or a title that is not a header, such as `feat(): x` with its empty
  scope, fails the plan before a tag exists: edit the title and resume with `merge-sha`.
- The title GitHub's revert button writes, `Revert "<title>"`, is listed under Changed as
  `**Reverted:** <title>`, provided the quoted title is a header.
- Dependabot titles its pull requests `Bump x from 1 to 2`, which is not a header. Give it
  one in `.github/dependabot.yml`: `commit-message: {prefix: "build", include: "scope"}`
  titles them `build(deps): bump x from 1 to 2`, left out of the changelog; `prefix: "fix"`
  lists them under Fixed.
- A `!` in the title, or a `BREAKING CHANGE:` footer on a commit of the pull request, lists
  it under Changed as `**BREAKING:**`, and the planner, reading the same parser, raises the
  release to a major for it: `2.0.0` after `1.4.2`, and `1.0.0` after `0.3.1`, since the
  planner treats `0.y.z` the same way. The changelog refuses a breaking entry under any
  other version.
- A version the file already lists is left alone, its section is the body, and nothing is
  committed, so resuming a release never lists it twice.
- The commit (plan-release's `commit-changelog` composite) reads the file `base-branch`
  holds when it runs and adds the release on top with the planner's own update, so a change
  made since the plan is kept, and a version already listed, say by a re-run that committed
  first, is not added again. When GitHub answers 409 because the file changed between that
  read and the commit, it reads again and retries, up to three times, after 1, 2 and 4
  seconds.
- The commit changes only `CHANGELOG.md`, which the planner passes over in the next range,
  so it needs no pull request. `base-branch` must accept it from `GITHUB_TOKEN`: a branch
  protection rule or ruleset that requires a pull request refuses it.
- A failed commit does not fail the run, since the tag and the GitHub Release already exist:
  the release job warns, in the annotations and the step summary, and `changelog-committed`
  is `false`. To add the file, re-run all jobs of the run (re-running failed jobs does
  nothing, as none failed), or run the caller again with `merge-sha` set to the release
  commit: the tag and the release are kept, the planner finds the version on the branch or
  builds it again, and the commit adds it.
- The entries come from the same commits as the version: every commit since the last
  `<prefix>` tag, or, with `scope-to-package`, only those touching `package-dir` or one of
  the `shared-paths`.

#### Previewing the release on a pull request

`actions/release/plan-release/preview` shows on an open pull request what merging it with the
release label would release, using the release's own planner and changelog writer, so the
preview cannot drift from the release: the version, from the last `<prefix>` tag, the branch
classes and the declared breaks, the title's `!` included, and the exact section the release
adds to `<package-dir>/CHANGELOG.md`, which is also the GitHub Release body. The entries are
the pull requests merged since the last tag and not released yet, and this one under the title
it has now, scoped to the package's paths. A package nothing touches shows "no release", and a
title that is not a Conventional Commits header shows the error the release would stop on.
The section is dated today in UTC, marked provisional: the release dates it by its merge
commit.

It matches `changelog: true`. `actions/release/changelog-conventional-commit` is unchanged
for its callers, but it renders its own format, listing commits, not what the release writes.

It runs on the test merge a `pull_request` event checks out, so the job checks out the
default ref with `fetch-depth: 0`, and needs `contents: read` and `pull-requests: read`; only
the comment step needs `pull-requests: write`. Each run appends one package to the output
file, so a monorepo previews each package and posts one comment. Pass each package the
`tag-prefix`, `semantic-branch-config` and paths its release uses (`package-dir` plus
`shared-paths` with `scope-to-package`, nothing without it):

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, edited] # edited: a new title, a new preview

jobs:
  release-preview:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      pull-requests: write # the comment; the preview itself only reads
    steps:
      - uses: actions/checkout@<sha> # v4
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: MassiveDataScope/loom-actions/actions/release/plan-release/preview@<sha> # vX.Y.Z
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          tag-prefix: control-plane/v
          paths: apps/control-plane,uv.lock
          semantic-branch-config: apps/control-plane/pyproject.toml
          changelog-file: apps/control-plane/CHANGELOG.md
      # ...one more step per package, same output file...
      - uses: MassiveDataScope/loom-actions/actions/core/pr-comment-update@<sha> # vX.Y.Z
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          tags: "<!-- release-preview -->"
          body-file: release-preview.md
```

The comment's header names the test merge it was computed from and its base, by short sha.
GitHub recomputes that merge lazily and a push to the base branch alone runs no
`pull_request` workflow, so a preview can trail the base; the shas make that visible, and the
next run on the pull request (a push, a title edit, or closing and reopening it) catches up.

The `failed` output is `true` when the release would fail, for a caller that wants the check
to fail too; the preview step itself succeeds so the comment is posted. A pull request with
merge conflicts has no test merge, and its checkout fails until they are resolved.

### Monorepo callers

The four workflows below are small and independent, so a monorepo calls each one from its own
job, grants that job only the permissions the workflow needs, and requires one `gate` of its
own. `tests/fixtures/callers/` holds a complete example (`ci.yml`, `docs.yml`, `release.yml`)
that the unit tests check against every workflow it calls: each input and secret it passes is
declared, each required input is passed, and each job is granted what the called jobs request.

None of them sets `concurrency`; the caller does. What reaches the caller's own code:

- `pages` (the build command) and the `extra-check` job of `repo-security`: a read-only token
  and no secret.
- `node-ci`: every job only reads. `CODECOV_TOKEN` reaches only the `codecov` job, which runs
  no npm and none of the caller's scripts: it downloads the lcov the `test` job stored.
- `image-release`: the caller's Dockerfile is built in the privileged job, the one holding
  `packages: write`, `id-token: write` and the Docker Hub secrets. Its `RUN` steps execute
  inside BuildKit without the job's token or secrets, but whatever the Dockerfile and the
  tagged commit contain ends up in a signed, attested image, so only a reviewed commit on the
  default branch may be released (see below).

Command inputs (`build-command`, `extra-check-command`, `e2e-command`) are run as shell code.
Write them as constants in the caller: never build one from `github.head_ref`, a pull request
title or body, or any other text a contributor controls.

### node-ci

```yaml
  node:
    permissions:
      contents: read
    uses: MassiveDataScope/loom-actions/.github/workflows/node-ci.yml@<sha> # vX.Y.Z
    with:
      node-version: "22"
      workspaces: "@acme/core acme-web"
      e2e-command: "npm run test:e2e -w @acme/core"
      e2e-workspace-dir: packages/core
      codecov: true
    secrets:
      CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}
```

| Job | Runs | Blocks on |
|---|---|---|
| `workspaces` | always | no `package-lock.json`, a name that is not a workspace, two workspaces in directories with the same name |
| `lint` | always | `npm ci`, `lint-script` and `typecheck-script` in every workspace (a missing script fails) |
| `test` | once per workspace | `test-script` (a missing script fails), no lcov at `coverage-file` |
| `codecov` | once per workspace, `codecov: true` | never: the upload is informational |
| `build` | `build-script` set | `build-script` where it exists |
| `e2e` | `e2e-command` set | `npx --no playwright install --with-deps chromium` in `e2e-workspace-dir`, then the command |
| `gate` | always | any job above failed or was cancelled |

- `workspaces` is a space-separated list of npm workspace names, resolved to their directories
  through `package-lock.json`; empty runs the scripts in the root project.
- The `SF:` paths of each lcov are rewritten relative to the repository root
  (`src/a.ts` in `packages/core` becomes `packages/core/src/a.ts`), stored as the artifact
  `coverage-<dir>` and, with `codecov: true`, uploaded by the `codecov` job with the flag
  `<dir>`: the basename of the workspace directory (`core`, `web`). The upload never fails the
  run.
- Playwright comes from the lockfile: `npx --no` refuses to download another version.

### repo-security

```yaml
  security:
    permissions:
      contents: read
      security-events: write
      pull-requests: write
      actions: read
    uses: MassiveDataScope/loom-actions/.github/workflows/repo-security.yml@<sha> # vX.Y.Z
    with:
      gitleaks-config: .gitleaks.toml
      codeql-languages: "python,javascript-typescript"
      extra-check-command: "python3 scripts/check.py && node scripts/check.mjs"
      extra-check-python-version: "3.12"
      extra-check-node-version: "22"
```

| Job | Runs | Permissions | Blocks on |
|---|---|---|---|
| `gitleaks` | always | `contents: read` | a secret anywhere in the history (gitleaks 8.30.1, image pinned by digest; findings are redacted) |
| `codeql` | `codeql-languages` set | `contents: read`, `security-events: write`, `actions: read` | an analysis that fails; alerts land in code scanning (`build-mode: none`) |
| `dependency-review` | pull requests, `dependency-review: true` | `contents: read`, `pull-requests: write` | a new dependency vulnerable at `dependency-review-severity` (`high`) |
| `extra-check` | `extra-check-command` set | `contents: read` | a non-zero exit of the command |
| `gate` | always | none | any job above failed or was cancelled |

- In a private repository CodeQL and the dependency review need GitHub Advanced Security, so
  their jobs leave a notice and pass.
- On a pull request gitleaks reads its configuration (`gitleaks-config`, or `.gitleaks.toml`)
  and `.gitleaksignore` from the base branch, never from the head, so a pull request cannot
  allowlist its own finding; without them on the base, the default rules apply. On a push it
  reads them from the commit scanned.
- `extra-check-command` runs with `bash -euo pipefail`, no secret and a checkout that keeps no
  token. `extra-check-python-version` installs uv and a virtualenv of that Python first on
  `PATH` (outside the workspace); `extra-check-node-version` installs Node.js.

### pages

```yaml
  docs:
    permissions:
      contents: read
    uses: MassiveDataScope/loom-actions/.github/workflows/pages.yml@<sha> # vX.Y.Z
    with:
      deploy: true
      python-version: "3.12"
      build-command: "uv sync --locked && uv run --locked sphinx-build -W -b html docs docs/_build/html"
      output-dir: docs/_build/html
      fetch-depth: 0 # the version is read from the git tags

  deploy:
    needs: docs
    if: ${{ needs.docs.outputs.pages-artifact == 'true' }}
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@368f82528645a54fb793d4d04e342629a3f51346 # v5.0.1
```

- The workflow only builds, with `contents: read` and no secret, and fails when
  `output-dir/index.html` is missing. It never deploys: `pages: write` and `id-token: write`
  belong to the caller's deploy job, which runs none of the build's code.
- `deploy: true` uploads the site as the Pages artifact and sets the output
  `pages-artifact` to `true`, only on the default branch of a public repository; elsewhere it
  leaves a notice instead. With `deploy: false` (a pull request) it only builds.
- A build with `deploy: true` restores no uv cache, so nothing a pull request saved reaches a
  published site.
- `fetch-depth` (1 by default) is passed to the checkout; 0 brings the tags a version read from
  git needs.

### image-release

```yaml
  image:
    needs: release
    if: ${{ needs.release.result == 'success' && needs.release.outputs.version != '' }}
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    uses: MassiveDataScope/loom-actions/.github/workflows/image-release.yml@<sha> # vX.Y.Z
    with:
      version: ${{ needs.release.outputs.version }}
      expected-sha: ${{ github.event.pull_request.merge_commit_sha || inputs.merge_sha }}
      ghcr-image: ghcr.io/acme/app
      dockerhub-image: acme/app
      platforms: "linux/amd64,linux/arm64"
    secrets:
      DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}
      DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}
```

- It stops before anything else unless `version` matches `^[0-9]+\.[0-9]+\.[0-9]+$` and
  `ghcr-image` is a lowercase `ghcr.io/...` name, and when `dockerhub-image` is set without
  both Docker Hub secrets.
- It builds the tag `v<version>`, not the commit that started the run, and refuses it unless
  the tagged commit is on the default branch and, when `expected-sha` is passed, is exactly
  that commit.
- It pushes `X.Y.Z` and `X.Y` to GHCR and, optionally, Docker Hub, and `latest` only when
  `v<version>` is the highest `vX.Y.Z` tag, so a patch to an older line leaves `latest` alone.
  The build args are `VERSION`, `REVISION` (the tagged commit) and `CREATED`, with an SBOM and
  `provenance: mode=max`.
- It reads no layer cache: the `gha` cache pull requests write is never trusted by a signed
  release. The binfmt and BuildKit images are pinned by digest; QEMU is set up only for a
  platform other than `linux/amd64`.
- Protect the `v*` tags with a repository ruleset (restrict creation, update and deletion to
  the release automation). The ancestry check stops a tag on a side branch, not one moved to
  another commit of the default branch; `expected-sha` covers that for the caller that passes it.
- In a public repository the digest gets a build provenance attestation pushed to the
  registry (`gh attestation verify oci://ghcr.io/acme/app:X.Y.Z --repo <owner>/<repo>
  --signer-repo MassiveDataScope/loom-actions`); in a private one, a notice. No storage record
  is created, so `artifact-metadata: write` is not needed.

## Quality Budgets

Default budgets for `actions/python/quality-report`:

| Signal | Budget / Rule | Default | Blocking by default |
|---|---|---|---|
| Tests | `tests_failed == 0` | enforced via `fail-on-quality=any` | ✅ Yes |
| Coverage | `coverage >= coverage-threshold` | `80` | ✅ Yes |
| Ruff | `ruff_issues == 0` | enforced via `fail-on-quality=any` | ✅ Yes |
| Pyright | `pyright_errors == 0` | enforced via `fail-on-quality=any` | ✅ Yes |
| Security (Bandit) | Fail by severity threshold | `fail-on-security=high` | ✅ Yes (HIGH+) |
| Security execution | Run Bandit check | `include-security=true` | ✅ Yes |

Tunable inputs:

| Input | Allowed values | Default |
|---|---|---|
| `coverage-threshold` | `0-100` | `80` |
| `fail-on-quality` | `none`, `any` | `any` |
| `fail-on-security` | `none`, `low`, `medium`, `high` | `high` |
| `include-security` | `true`, `false` | `true` |
| `test-results-dir` | path, or empty | empty |

### Reporting test results produced elsewhere

`test-results-dir` points at a directory already holding `junit.xml`, `coverage.json` and
`coverage.xml`. When it is set, the action reports those files instead of running pytest, and
fails if any of the three is missing. It exists for a pipeline that runs its tests in its own
job — split across several jobs, or under a resolution the caller controls — and wants one
report over the results rather than a second execution of the same suite.

The gate is unchanged: a failed test and coverage below the threshold still block, because
both are read from these files rather than from the exit code of a pytest this action ran.
The tool table shows pytest as `reused`; the quality gate gives the verdict. (From the release
after this change; `python-service-ci` picks it up once it pins that release.)

Every input reaches the scripts through the environment, never through an expression in the
script text.

### Lockfiles

When the repository contains a `uv.lock`, the tools run with `--frozen`, so the committed
resolution is what gets used.

> **Breaking for one case, shipped in v1.1.0.** A repository whose lockfile is **stale** used
> to be re-resolved silently and now **fails**. That is the intended behaviour — a run whose
> dependencies differ from the ones you committed is not reproducing anything — but it is a
> behavioural break, so a consumer upgrading to v1.1.0 should run `uv lock` and commit the
> result before pinning. A repository with no `uv.lock` at all is unaffected: `--frozen` is
> only added when the file exists.

## Quick Start

### Use quality-report in a PR workflow

```yaml
name: quality
on:
  pull_request:

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Quality report
        uses: MassiveDataScope/loom-actions/actions/python/quality-report@v1
        with:
          src-dir: src
          test-dir: tests
          coverage-threshold: "80"
          fail-on-quality: "any"
          fail-on-security: "high"
          include-security: "true"
```

### Use release actions

```yaml
- name: Compute version
  id: version
  uses: MassiveDataScope/loom-actions/actions/release/versioning-branch-semantic@v1
  with:
    branch: feature/my-change
    prerelease: "false"

- name: Generate changelog
  uses: MassiveDataScope/loom-actions/actions/release/changelog-conventional-commit@v1
  with:
    mode: release
    branch: feature/my-change
    version: ${{ steps.version.outputs.version }}
    output: CHANGELOG_RELEASE.md
```

## Local Testing

```bash
make bootstrap
make test-unit
make test-builder-render
```

With `act`:

```bash
make act-unit
make act-smoke
make act-monorepo
```

`make act-monorepo` runs `python-service-ci` over [`examples/monorepo`](examples/monorepo),
on its `python-version` alone; `PYTHON_VERSIONS='["3.12","3.14"]' make act-monorepo` passes
that list as `python-versions`, so `test (3.12)` and `test (3.14)` run and `report` reads
the primary's results.
act's artifact server only implements the protocol of `upload-artifact` and
`download-artifact` v4, so the target runs a throwaway copy of the checkout in which those
two pins are v4; every other step runs as on GitHub.

`tests/act/run-release.sh <caller checkout> <package-name> [input=value ...]` runs the
`build` job of `release-on-label` over a throwaway clone of a caller, tagged `v9.9.9`, and
downloads its distributions as a caller's `publish` job would. The plan and the GitHub
release need the GitHub API, so the harness stubs the plan and leaves the release out;
nothing is pushed or published.

```bash
tests/act/run-release.sh ../loom-py loom-kernel
tests/act/run-release.sh ../nautilus-ui periplo package-dir=apps/api \
  semantic-branch-config=apps/api/pyproject.toml check-distribution=true python-version='"3.12"'
```

## Repository Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci-pr.yml` | `pull_request` | Validate actions, publish changelog+quality PR comments, enforce gates |
| `ci-main.yml` | `push` on `master` | Mainline validation with stricter smoke checks |
| `release.yml` | `pull_request` `closed` on `master` | On merged PR: prepare release PR, auto-merge it, then publish tags/release when `release/*` is merged |
| `act-unit-builder.yml` | local/PR | Unit tests intended for `act` |
| `act-quality-smoke.yml` | local/PR | Composite action smoke run intended for `act` |

## Versioning and Consumption

- Trunk-based flow: merge to `master`, then release workflow creates tags.
- Tag strategy (standard for reusable GitHub Actions):
  - Immutable release tag: `vX.Y.Z` (for pinning exact versions)
  - Moving major tag: `vX` (updated on each compatible minor/patch release)
- Intended external consumption pattern:
  - `MassiveDataScope/loom-actions/actions/python/quality-report@v1`
  - `MassiveDataScope/loom-actions/actions/release/versioning-branch-semantic@v1`
- Keep major tags (`v1`, `v2`) stable and move them only on compatible releases.

## Repository Settings

- To allow automated release PRs to trigger downstream workflows (`ci-pr` and final `release` on `release/*` merge), add repository secret:
  - `RELEASE_BOT_TOKEN`: PAT/GitHub App token with `contents:write` and `pull_requests:write`.
- Keep `GITHUB_TOKEN` for standard workflow operations; `RELEASE_BOT_TOKEN` is used by the release automation steps that must emit new workflow events.
