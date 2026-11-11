# Design notes

Decisions that are easier to re-litigate than to remember. Each one records what was
considered and why it was refused, so the next person does not rediscover it.

## Refused: moving tool execution out of `quality-report`

`quality-report` both runs the tools (ruff, pyright, pytest, bandit) and renders the report.
The cleaner factoring is to split those: `src/builder.py` already takes tool output as JSON,
so a caller could run the tools itself and this action would only render and gate.

It was refused. Consumers pinned to `v1.0.5` rely on the action running the tools, so the
split is a breaking change and would be a `v2.0.0` with a migration nobody asked for. The
`test-results-dir` input reaches the same result for a caller that wants it and costs every
other consumer nothing.

## Refused: wheels-only installs

The intent was to stop source builds on the runner. It cannot be done as stated. `uv` offers
a deny-list (`--no-build-package`) but no allow-list, so "no source builds except these" is
not expressible, and the packages that need one are real: `antlr4-python3-runtime` 4.9.3
ships no wheel and is a transitive requirement of `omegaconf`, and `pyspark` is distributed
as an sdist. A blanket ban would break any consumer whose graph contains either.

What is enforced instead is the lockfile: with `--frozen` the caller's committed resolution
is what gets installed, so what builds is what the caller reviewed and locked.

## Inputs reach shell through the environment

Every input that a caller can trace back to `github.event.*` is passed to a step's script as
an environment variable and quoted at each use. A `${{ }}` expression is substituted into the
script **text** before bash parses it, so an input is source code there, not data — and git
permits `"` and `$` in a ref name.

The boundary is the part worth remembering: auditing the workflow that reads
`github.event.pull_request.head.ref` is not enough, because the value keeps travelling. It
goes into a job output, then into `with:` on a composite action, and the interpolation that
executes it is one level down inside that action. **An audit has to follow the value across
the action boundary, not stop at the file where it enters.**

Six interpolations remain, all in `quality-report`: `src-dir`, `test-dir`,
`include-security`, `coverage-threshold`, `fail-on-quality` and `fail-on-security`. They are
workflow-authored literals (`src`, `tests`, `80`, `any`, `high`, `true`) with no path from
event data, and they sit inside command strings handed to `bash -lc` by `run_check`, so making
them travel through the environment means escaping the reference for the inner shell
(`\"\$SRC_DIR\"`) at each site. That is worth doing when `run_check` is restructured; it is
recorded here rather than left silent.

## python-service-ci: the quality report blocks only on security

`quality-report` runs its own ruff (`~=0.6`) and pyright. A service locks its own ruff and
type checker (mypy), so gating on the report's versions would block on rules the project never
chose. The workflow gates ruff and mypy in `lint` with the locked tools, tests and coverage in
`test` on the run that measured them, and calls the report with `fail-on-quality: none` and
`test-results-dir: .`: it adds pyright and bandit to the PR comment and blocks on bandit only.

## python-service-ci: one workflow, one required check

Splitting lint, tests, scanners and the image into separate reusable workflows would give each
caller several calls to keep on the same version and several checks to require. One workflow
with a `gate` job that needs every other job keeps branch protection to `ci / gate` and lets a
job be skipped (a scanner that is off) without a required check going missing. Publishing an
image is a different trigger with different permissions (`id-token: write`), so it belongs in
its own workflow, not behind an input here.

## commit-changelog: render and verify, so the bot never writes the base branch

`commit-changelog` used to do one thing: render the release onto the base branch's
`CHANGELOG.md` and commit it there through the contents API. A caller whose base branch
only a person may write (a ruleset on `master`, a release that runs with a GitHub App
token and must not commit) cannot use that. That caller commits the changelog itself, on
the release pull request's head, and needs two things instead: the file it should commit,
and a check that the file it merged is the one the release plans.

The input `mode` adds both and leaves `commit` as the default:

- `render` reads the file at `ref` (the base branch when empty), carries the planned
  release onto it with the planner's own update, and writes the result to `out`. It never
  writes to GitHub. A section the ref already lists for the version is replaced by the
  planned one.
- `verify` fails unless the file at `ref` is exactly the file `render` expects of it. With
  `out`, it first leaves the expected file there for the caller to upload or diff. Each
  of these fails with its own message:
  - the version is missing;
  - the version is listed more than once (a second section further down);
  - its link (`[X.Y.Z]:`, in any case) is defined outside the reference block at the
    bottom, where Markdown's first definition would win over the real one;
  - the heading has no ISO 8601 date;
  - the section or its link differ;
  - the rest of the file differs: the position of the section, the `[unreleased]` link,
    line endings, blank lines.
- Both set the output `changed`: whether the file at `ref` differs from the expected one.

The date gets its own rule. The heading is `## [X.Y.Z] - YYYY-MM-DD`, and the day a person
renders the file is rarely the day the release merges. So `render` reuses the date of a
section the ref already lists, or takes today's date in UTC if there is none. `verify`
requires a valid ISO 8601 date but does not compare its value: it builds the expected file
with the ref's own date, so a file that differs only in the date passes. A merge on another
UTC day therefore never breaks the check.

Refused: a separate `verify-changelog` action. `render` and `verify` need the same read,
the same carry and the same date rule as `commit`. Keeping them in one script means the
file a person commits is computed by the code that later verifies it. Also refused:
changing `commit` to reuse the date too. It writes exactly what it wrote before, so no
pinned caller sees a change.

`verify` is only as independent as `built-file`. The planner leaves a version that a file
already lists as it is, so `built-file` must be produced from a `CHANGELOG.md` that does
not list the version yet:

- on a pull request check, from the pull request's base SHA;
- after the merge, from the merge commit's first parent (`merge^1`).

It must never come from the merge commit, from the pull request's head or merge ref, or
from the base branch after the merge. All of those already hold the section, so `verify`
would compare the section with itself and always pass.

## What cannot be tested here

These are exercised by a real release and by nothing else. They are listed so nobody reads
their absence as coverage:

- publishing to PyPI;
- pushing an immutable version tag;
- moving the floating major tag;
- creating a GitHub Release;
- the merge event that a release label triggers, which needs a genuinely merged pull request;
- `python-service-ci.yml` end to end: its contract is tested and it passes actionlint, but the
  Codecov, SonarQube and Snyk jobs need real tokens, and the whole workflow only runs from a
  caller once a tag contains it.

Secret-dependent stages — Sonar, Snyk, TestPyPI — cannot run in a fixture pipeline that has
no tokens. When they are skipped they must **say so**, in the job summary and as an
annotation, never appear as a green check. A green tick nobody can act on is worse than a
missing one: it reports a scan that never happened.
