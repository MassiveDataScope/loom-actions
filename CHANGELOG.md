# 🚀 Release 1.9.0 ([#61](https://github.com/MassiveDataScope/loom-actions/pull/61)) ([`90410a7`](https://github.com/MassiveDataScope/loom-actions/commit/90410a7112c5da939116ba64f9bf24e5f8a14e12))


## ✨ Features
### plan-release
- **plan-release:** plan and write notes from the tags of one prefix











# 🚀 Release 1.8.1 ([#48](https://github.com/MassiveDataScope/loom-actions/pull/48)) ([`cf2cf11`](https://github.com/MassiveDataScope/loom-actions/commit/cf2cf11f2b09d538250219616f6e85967268d80e))









## 🛠 Chores
- reference the repository under MassiveDataScope




# 🚀 Release 1.8.0 ([#46](https://github.com/the-reacher-data/loom-actions/pull/46)) ([`526652d`](https://github.com/the-reacher-data/loom-actions/commit/526652d2566435b2d0aae7b00270415a50a36e7a))


## ✨ Features
### workflows
- **workflows:** test python-service-ci on a matrix of Python versions<br>
  > python-versions and python-versions-experimental take JSON arrays of
  > versions, empty by default. A new versions job normalises them before
  > any matrix reads them: an empty list means python-version alone,
  > duplicates run once, the primary is added when missing and experimental
  > versions already required are dropped. A value that is not JSON, not an
  > array of strings or not 3.N (3.N-dev only among the experimental ones)
  > fails the job with an error naming it.
  > test becomes test (<v>), one leg per required version with fail-fast
  > off; each leg installs the lock for its version with uv sync --locked
  > -python and keys the uv cache on it. test-experimental (<v>) installs
  > prereleases through setup-python, continues on error and is left out of
  > the gate, which now needs versions so an invalid list still fails it.
  > The coverage threshold applies to the primary leg alone.
  > Every leg uploads test-results-<v>; the primary also uploads
  > test-results, the artifact report and sonar read, so a caller that sets
  > none of the new inputs gets the same results under the same names.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 🐛 Fixes
### workflows
- **workflows:** leave python-version unchecked unless a matrix is declared<br>
  > setup-uv takes any interpreter request, so a caller may pin a patch
  > release such as 3.12.8. Checking python-version against 3.N on every run
  > would fail those callers although they set none of the new inputs; it is
  > now checked only when python-versions or python-versions-experimental is
  > set, which is when it has to match the list. Without them it is passed
  > on untouched, as before.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 📖 Documentation
### readme
- **readme:** document the Python version matrix of python-service-ci<br>
  > How to declare the versions and the experimental ones, what stays on
  > the primary, how to pin to one version, the artifacts each leg uploads,
  > how to reproduce a leg with uv run --python and why the matrix is not
  > delegated to tox or nox.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>




## ♻️ Refactor
### workflows
- **workflows:** keep comments to what the code does<br>
  > Trim comments in the L005 diff (python-service-ci.yml, README.md,
  > examples/monorepo/.github/workflows/ci.yml,
  > tests/unit/test_python_service_ci_contract.py) that justified design
  > decisions, referenced spec IDs, or restated history, keeping only the
  > description of what the code does.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** keep comments to what the code does<br>
  > Remove remaining history references in the README and test docstrings,
  > and restore a non-obvious invariant in the gate comment without its
  > justification, per the axiom of keeping comments to what the code does.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>




## ✅ Tests
### act
- **act:** run the monorepo example on the versions in PYTHON_VERSIONS<br>
  > The example caller passes python-versions from the PYTHON_VERSIONS
  > variable, which run-monorepo.sh sets from the environment variable of
  > the same name, so make act-monorepo keeps its single leg and
  > PYTHON_VERSIONS='["3.12","3.14"]' make act-monorepo runs test (3.12)
  > and test (3.14) and one report. The caller fixture declares a matrix
  > and an experimental version, so the caller contract test reads both new
  > inputs.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






# 🚀 Release 1.7.0 ([#44](https://github.com/the-reacher-data/loom-actions/pull/44)) ([`4ed4098`](https://github.com/the-reacher-data/loom-actions/commit/4ed4098e1b5d233e062d500440e309dd3ef1413b))


## ✨ Features
### release-on-label
- **release-on-label:** release a package in a subdirectory with a pinned planner<br>
  > A monorepo keeps the package it publishes in a subdirectory with its own
  > pyproject.toml and uv.lock, and release-on-label only built from the root.
  > package-dir, "." by default: the lock check, the build and the wheel name
  > check run there, and the distributions are uploaded from its dist/. The
  > root package uploads "dist/", the path it always had.
  > semantic-branch-config, "pyproject.toml" by default like plan-release's
  > own default, is forwarded to the planner.
  > check-distribution, off by default: twine 7.0.0 check --strict over every
  > built distribution before it is stored (it reads Metadata-Version 2.5).
  > plan-release is pinned by f18400c (v1.5.0), the commit the floating v1 tag
  > points at, instead of @v1: a caller's SHA pin on this workflow now also
  > fixes the planner. Its composite is identical at v1.4.0, v1.5.0 and here.
  > distribution-built reads a job output the last build step sets, instead
  > of jobs.build.result, which actionlint does not type there. The value is
  > the same: true once every build step succeeded, false when it was skipped.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>


### python-service-ci
- **python-service-ci:** add codecov-flag and pin the image builder by digest<br>
  > codecov-flag, empty by default, is passed as flags to both Codecov uploads
  > so a monorepo can split its coverage by component. codecov-action's flags
  > input has no default, so an empty flag is what it got before.
  > The image job's setup-buildx now pins BuildKit by the digest image-release
  > uses (buildx-stable-1 on 2026-09-25), instead of pulling the floating tag
  > to build the caller's Dockerfile. A test keeps both workflows on one digest.
  > The caller fixture passes the monorepo inputs plan §5 gives the API.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 🐛 Fixes
### quality-report
- **quality-report:** pass every input to the checks through the environment<br>
  > src-dir, test-dir, include-security, coverage-threshold, fail-on-quality,
  > fail-on-security and the action path were interpolated into the script
  > text, where GitHub substitutes them before bash parses it. They now reach
  > both steps as environment variables, and each check, which runs in a login
  > shell of its own, reads them there, so a value is never parsed as shell.
  > Every tool gets the arguments it got before, with or without uv.lock; the
  > command text recorded in command_status.tsv now names the variables
  > instead of their values. Tests run the step with a recording uv, including
  > a source directory with spaces, quotes and command substitutions; the act
  > smoke passes at the root and in a subdirectory.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **quality-report:** show supplied test results as reused, not failed<br>
  > With test-results-dir the pytest row has status "reused", and the tool
  > table painted anything but pass and skipped as a failure. It now reads
  > "reused"; whether the tests passed is still the quality gate's verdict,
  > which reads the supplied files.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 📖 Documentation
- document a monorepo release with PyPI, GHCR, Docker Hub and attestations<br>
  > PUBLISHING.md now gives the complete caller of a package in a subdirectory:
  > release-on-label with package-dir, semantic-branch-config and
  > check-distribution, the image job gated on a version, and the caller's own
  > publish job gated on distribution-built. It lists what PyPI's trusted
  > publisher, the pypi environment, GHCR and Docker Hub need, and how to
  > verify the image attestation with gh attestation verify and
  > -signer-repo the-reacher-data/loom-actions. A test keeps its caller in
  > step with the tested fixture.
  > The README documents release-on-label's inputs and outputs, codecov-flag,
  > the pinned BuildKit, and the reused pytest row of quality-report.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>


### publishing
- **publishing:** pin the signer workflow instead of the signer repository<br>
  > gh attestation verify refuses --signer-repo and --signer-workflow together,
  > so the stricter check replaces the first instead of adding to it. The pypi
  > environment note no longer names the exact ref of a pull_request run, and
  > the README says python-service-ci shows the reused row only once it pins
  > the release that carries it.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  > --------
  > Co-authored-by: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 🎨 Style
### release
- **release:** write the detected metadata in one redirect<br>
  > actionlint's shellcheck flagged four separate redirects to GITHUB_OUTPUT
  > (SC2129). They are grouped; the outputs are the same, and a test runs the
  > step for a release branch, other branches and a hostile branch name.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>





## ✅ Tests
### callers
- **callers:** release a monorepo package and upload it from the caller<br>
  > The release fixture now passes package-dir, semantic-branch-config,
  > check-distribution and merge-sha as plan §5 and review round 3 give them,
  > and carries the caller's publish job: environment pypi, id-token only,
  > gated on distribution-built, skip-existing. New tests pin that neither the
  > image nor the upload runs when a merge without the label leaves the
  > release outputs empty.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>


### act
- **act:** run the build of release-on-label over a clone of a caller<br>
  > tests/act/run-release.sh clones a caller into a temporary directory, tags
  > it v9.9.9, and runs release-on-label's build job under act with the
  > caller's inputs, then downloads the distributions as the caller's publish
  > job does. The plan and the GitHub release need the GitHub API, so the plan
  > is a stub and the release is left out; the clone has an unreachable
  > remote, so nothing is pushed or published.
  > With loom-py's own inputs it builds loom_kernel-9.9.9 as before; with
  > package-dir apps/api and check-distribution it builds periplo-9.9.9, twine
  > passes both files, and the artifact holds them at its root.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






# 🚀 Release 1.6.0 ([#42](https://github.com/the-reacher-data/loom-actions/pull/42)) ([`f299351`](https://github.com/the-reacher-data/loom-actions/commit/f299351c56249c774edd991725b6129383ca9561))


## ✨ Features
### workflows
- **workflows:** add reusable node-ci workflow<br>
  > CI for npm workspaces, one job per concern and one gate: npm ci from the
  > lockfile, lint and type-check, tests with coverage once per workspace, build
  > where the script exists, and Playwright end-to-end tests on a Chromium the
  > locked Playwright installs. A missing lint, type-check or test script fails
  > instead of passing unnoticed.
  > Workspace names resolve to their directories through package-lock.json, so
  > each lcov is rewritten with SF: paths relative to the repository root and
  > uploaded to Codecov, opt-in and never blocking, flagged with the directory
  > name. Every job only reads; the Codecov token reaches the upload alone.
  > The contract tests run the resolve and lcov steps themselves through a small
  > helper that executes a workflow's run: script as the runner does.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** add reusable repo-security workflow<br>
  > gitleaks 8.30.1 over the full history, from its image pinned by digest;
  > CodeQL without a build for the languages given; dependency review on pull
  > requests, blocking at high by default; and a command of the caller's, run
  > with a read-only token, no secret and, on request, a pinned uv with a
  > virtualenv of the Python asked for and Node.js. One gate requires them all.
  > In a private repository CodeQL and the dependency review need GitHub
  > Advanced Security, so their jobs leave a notice and pass. The tests run the
  > gitleaks step against a throwaway repository holding a token built at run
  > time, and the extra check's exit code, besides the contract.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** add reusable pages build workflow<br>
  > Builds a static site with a read-only token and no secret, requires
  > output-dir/index.html, and with deploy uploads it as the Pages artifact,
  > reporting that through the pages-artifact output. It never deploys: the

- **workflows:** add reusable image-release workflow<br>
  > Publishes the image of a release to GHCR and, optionally, Docker Hub. It
  > stops first unless version is X.Y.Z and the GHCR name is valid, and when
  > Docker Hub is asked for without both secrets. It builds the release tag, not
  > the commit that started the run, for every platform given, tags X.Y.Z, X.Y
  > and latest, passes VERSION, REVISION and CREATED, and pushes an SBOM and
  > mode=max provenance. In a public repository each pushed digest gets a build
  > provenance attestation stored in the registry; in a private one, a notice.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 🐛 Fixes
### workflows
- **workflows:** run gitleaks from the repository and test its configuration<br>
  > The scan now runs with /repo as its working directory, so any path gitleaks
  > resolves against the current directory lands in the repository rather than
  > the image root. The tests cover the paths a caller takes beyond the default:
  > a configuration whose allowlist clears a finding, a configuration that does
  > not exist, and a committed .gitleaksignore holding the finding's fingerprint.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** upload node-ci coverage from a job that runs no caller code<br>
  > The test job ran the caller's npm scripts, with dependencies installed, on
  > the same runner where a later step received CODECOV_TOKEN. The test job now
  > only stores each workspace's rewritten lcov as an artifact; a codecov job
  > with the same matrix, a read-only token and no npm, downloads it and
  > uploads it. A workspace that stored no report skips its upload; its tests
  > already failed the gate.
  > A contract test now requires that no job running npm, npx or a command of
  > the caller's references a secret, in all four new workflows.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** harden what image-release builds and tags<br>
  > The signed release no longer reads the gha layer cache that pull
  > requests write (scope=image), so no layer a pull request produced can
  > reach a published, attested image.
  > The binfmt and BuildKit images the builder actions start are pinned by
  > digest, and QEMU is set up only when a platform other than linux/amd64
  > is asked for.
  > latest moves only when the version is the highest vX.Y.Z tag, so a patch
  > to an older line leaves it alone.
  > The tagged commit must be on the default branch and, with the new
  > optional expected-sha input, be exactly the commit the caller released.
  > The scripts are tested against a throwaway repository with tags on and off
  > the default branch.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **workflows:** take the gitleaks settings of a pull request from its base<br>
  > A pull request could clear its own finding: gitleaks read the allowlist in
  > .gitleaks.toml, or the configured file, and .gitleaksignore from the checked
  > out head. On pull_request the scan now reads both from the base branch into
  > a temporary directory, falls back to the default rules and an empty ignore
  > list when the base has none, removes the head's .gitleaksignore from the
  > scanned root, and fails when the base branch cannot be read. Pushes still
  > use the settings of the commit scanned.
  > The tests reproduce each bypass against the previous step and show it

- **workflows:** upload the Pages site only from the default branch<br>
  > A caller passing deploy on another branch, or on a pull request, could have
  > uploaded a site its deploy job would publish. The Pages artifact is now
  > uploaded only when github.ref is the default branch, with a notice
  > otherwise, and a build that may be deployed restores no uv cache a pull
  > request could have saved.
  > A new fetch-depth input, 1 by default, lets a build that reads its version
  > from git tags check out the full history.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>



## 📖 Documentation
### readme
- **readme:** document node-ci, repo-security, pages and image-release<br>
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **readme:** state exactly what reaches the caller's code<br>
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>

- **readme:** document the hardened release, pages and gitleaks behaviour<br>
  > Covers the codecov job, gitleaks settings taken from the base branch, the
  > default-branch-only Pages upload and fetch-depth, and in image-release the
  > dropped cache, the pinned builder images, latest for the highest release
  > only, the ancestry check and expected-sha. It states that the Dockerfile is
  > built in the privileged job, recommends a ruleset protecting v* tags, and
  > warns against building command inputs from contributor-controlled text.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  > --------
  > Co-authored-by: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






## ✅ Tests
### workflows
- **workflows:** check the new workflows and a monorepo caller<br>
  > The four new workflows are only callable, pin every action by commit with
  > its version, interpolate no expression into a script, keep no checkout
  > token, declare permissions and a timeout per job and leave concurrency to
  > the caller; only image-release can mint an identity.
  > The caller fixtures mirror the monorepo CI, docs and release the workflows
  > are built for. GitHub rejects an undeclared input or secret, and a caller
  > granting less than the called jobs request, only when a run starts, so the
  > test checks every call in them for all three.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






# 🚀 Release 1.5.0 ([#40](https://github.com/the-reacher-data/loom-actions/pull/40)) ([`fa9f387`](https://github.com/the-reacher-data/loom-actions/commit/fa9f387931df4c8d046bb4a556e8d40fa698c65f))


## ✨ Features
### python-service-ci
- **python-service-ci:** support a monorepo project and an informative Sonar<br>
  > A monorepo keeps its Python service in a subdirectory with its own
  > pyproject.toml and uv.lock, and the workflow only ran from the root.
  > working-directory, "." by default: lint, test and dependencies run uv
  > there, src-dir and test-dir are relative to it, the test results are
  > uploaded from it and downloaded back into it, and quality-report gets it.
  > quality-report is pinned to be54dfb (the merge of #37, whose actions/ tree
  > is the one released as v1.4.0), the first commit with working-directory.
  > Sonar, Codecov, the image and the branch check stay at the root; Sonar and
  > Codecov read the project's reports through a PROJECT_PREFIX that is empty
  > by default, so every path is exactly the one used before.
  > semantic-branch-config, empty by default: the branch check reads its rules
  > from that file, relative to the root, or else from the pyproject.toml in
  > working-directory. A missing file is a clear error instead of a traceback.
  > sonar-blocking, true by default: when false, a Sonar that fails or is on
  > without its token or project key leaves a notice and the gate ignores it.
  > The jobs and the gate are unchanged, and the contract tests pin both.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>




## 📖 Documentation
### readme
- **readme:** document the monorepo inputs and sonar-blocking of python-service-ci<br>
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






## ✅ Tests
### examples
- **examples:** run python-service-ci over a monorepo example under act<br>
  > examples/monorepo keeps a Python service in apps/api with its own
  > pyproject.toml, [tool.semantic_branch] and uv.lock, and a Dockerfile built
  > from the monorepo root. Its caller passes working-directory,
  > semantic-branch-config, image-context and dockerfile, so quality-report runs
  > with --frozen in a subdirectory, which the composite smoke did not cover.
  > make act-monorepo runs it. act's artifact server only speaks the protocol of
  > upload-artifact and download-artifact v4, so tests/act/run-monorepo.sh runs a
  > throwaway copy of the checkout with those two pins swapped for v4 and fails
  > if the pins it swaps are gone. The root .gitignore ignores uv.lock, so the
  > example's lockfile is unignored.
  > Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>






# 🚀 Release 1.4.0 ([#16](https://github.com/the-reacher-data/loom-actions/pull/16)) ([`1dcfe63`](https://github.com/the-reacher-data/loom-actions/commit/1dcfe63cf91e6c9a38b389cda3674e9f176e607d))



## 🐛 Fixes
- actions/python/quality-report/requirements.txt to reduce vulnerabilities










# 🚀 Release 1.4.0 ([#37](https://github.com/the-reacher-data/loom-actions/pull/37)) ([`be54dfb`](https://github.com/the-reacher-data/loom-actions/commit/be54dfb371c9390deaca1c7787d9de261ae17255))


## ✨ Features
### quality-report
- **quality-report:** run the checks in an optional working-directory<br>
  > A monorepo keeps its Python project in a subdirectory, and the composite only
  > ran from the workspace root. The new working-directory input, "." by default,
  > runs every check and the report there, so src-dir, test-dir, test-results-dir
  > and the uv.lock lookup are relative to it.
  > The report paths the action returns carry the directory, so a caller still
  > reads them from the workspace root. The prefix is written before the builder
  > runs, because a blocking report exits non-zero. With the default it is empty
  > and the outputs are exactly the paths returned before; the act smoke asserts
  > both cases.


### release
- **release:** read the branch rules from an optional semantic-branch-config<br>
  > A monorepo declares [tool.semantic_branch] in the pyproject.toml of the
  > package it releases, not at the root. plan-release gains a
  > semantic-branch-config input, pyproject.toml by default, passed to both of its
  > planner runs so the plan shown and the version returned read the same rules.
  > A file that does not exist now refuses with a ReleasePlanError naming it,
  > instead of an uncaught FileNotFoundError. An empty value means the default, so
  > a reusable workflow can forward its own input unchanged.
  > versioning-branch-semantic already reads config-file, which holds both the
  > rules and the version it rewrites. Its semantic-branch-config is empty by
  > default and then keeps reading the rules from config-file; when set, only the
  > rules come from it, and the version is still read from and written to
  > config-file, so a separate rules file is never rewritten.
  > With the defaults, both produce exactly the output they did. The unit tests
  > load cli.py, which imports tomli_w, so the test runs install tomli-w.












# 🚀 Release 1.3.0 ([#35](https://github.com/the-reacher-data/loom-actions/pull/35)) ([`433c52c`](https://github.com/the-reacher-data/loom-actions/commit/433c52cd7e58127c0cb263a924a9f3a2b8d756ad))


## ✨ Features
### workflows
- **workflows:** add reusable python-service-ci workflow











# 🚀 Release 1.2.3 ([#33](https://github.com/the-reacher-data/loom-actions/pull/33)) ([`facf1a8`](https://github.com/the-reacher-data/loom-actions/commit/facf1a8c5d86658099fcfa9205ea9aad5e55da33))



## 🐛 Fixes
### release
- **release:** let a commit's breaking marker raise the version










# 🚀 Release 1.2.2 ([#31](https://github.com/the-reacher-data/loom-actions/pull/31)) ([`e594096`](https://github.com/the-reacher-data/loom-actions/commit/e59409611f0de4ecffe889dd0c7734fb23d9bd09))



## 🐛 Fixes
### release
- **release:** let a re-run of a tagged release plan the same version<br>
  > Recovering a release that stopped after tagging was documented as a dispatch
  > with the merge SHA, and it could not work: the planner read the highest tag
  > reachable from that commit, which now included the tag the halted run had just
  > created, so the range was empty and the run refused with "nothing to release".
  > loom-py's v1.11.0 is in exactly that state — tagged and released, never
  > uploaded.
  > A tag pointing at the commit being released is not a release that preceded it,
  > so it is ignored when choosing the previous tag. The re-run then plans the same
  > version, finds the tag already pointing at the right commit, and carries on to
  > the upload.
  > Removing the guard turns two tests red. The empty-range refusal is now
  > unreachable through this path, so its test asserts what the code does instead:
  > a commit carrying the only tag is planned from the start of history.
  > Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  > Claude-Session: https://claude.ai/code/session_016xY1skW5S2PU9Fc3M7tfAW

- **release:** the same tag-on-HEAD defect, in the copy nobody tested<br>
  > An independent audit found the fix for the previous commit landed in one of two
  > places. build_release_notes.py has its own latest_release_tag, with the same
  > rule and the same bug: the action detaches at the released commit and runs it,
  > so a re-run for an already tagged commit read its own tag as the previous
  > release, saw an empty range, and refused. The next dispatch would have died in
  > the plan job, before the tag step, before the upload.
  > That module had no test file at all, which is why the bug survived a fix aimed
  > at itself. It has nine now, including the re-run.
  > The reusable workflow also called its own action at @master while callers pin
  > the workflow by SHA, so unreviewed action code reached a pinned release. It
  > tracks the floating major tag instead, and a test refuses master. A caller can
  > now read whether a distribution was built rather than inferring it from the
  > aggregate result of the called workflow.
  > Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  > Claude-Session: https://claude.ai/code/session_016xY1skW5S2PU9Fc3M7tfAW
  > --------
  > Co-authored-by: Claude Opus 5 <noreply@anthropic.com>











# 🚀 Release 1.2.1 ([#29](https://github.com/the-reacher-data/loom-actions/pull/29)) ([`eb68725`](https://github.com/the-reacher-data/loom-actions/commit/eb6872566d79eb09bae5b68ffa73362c407b0eea))



## 🐛 Fixes
### release
- **release:** leave the upload to the caller, which PyPI can recognise










# 🚀 Release 1.2.0 ([#27](https://github.com/the-reacher-data/loom-actions/pull/27)) ([`70d2b11`](https://github.com/the-reacher-data/loom-actions/commit/70d2b11807373845aaed4d619872985922939927))


## ✨ Features
### release
- **release:** a reusable release for every trunk-based repository<br>
  > Merging a pull request that carries the release label publishes everything
  > merged since the last tag. The version is derived from the branches the range
  > ships, taking the highest part any of them asks for, so a batch holding a
  > feature never goes out as a patch.
  > Publishing to PyPI is opt-in and off by default. A repository that ships an
  > application, not a package, gets the tag, the notes and the GitHub release and
  > never builds a distribution — so it needs no index account, no trusted
  > publisher and no secret. Asking to publish without a package name fails
  > closed, because the built version could not then be checked against the tag.
  > The planner moves here from loom-py: a workflow reusable by other repositories
  > cannot read a script that lives in one of them.
  > Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  > Claude-Session: https://claude.ai/code/session_016xY1skW5S2PU9Fc3M7tfAW












# 🚀 Release 1.1.2 ([#25](https://github.com/the-reacher-data/loom-actions/pull/25)) ([`6260f7c`](https://github.com/the-reacher-data/loom-actions/commit/6260f7cf2774d652fc08246a6748c6e1dab646f5))



## 🐛 Fixes
### quality-report
- **quality-report:** adopt results that already sit in the workspace










# 🚀 Release 1.1.1 ([#23](https://github.com/the-reacher-data/loom-actions/pull/23)) ([`a0c0d6d`](https://github.com/the-reacher-data/loom-actions/commit/a0c0d6de35ff2a1acc62d4f857d5d63d1c08f4ef))



## 🐛 Fixes
### actions
- **actions:** pass every caller-supplied input through the environment










# 🚀 Release 1.1.0 ([#19](https://github.com/the-reacher-data/loom-actions/pull/19)) ([`90dbb02`](https://github.com/the-reacher-data/loom-actions/commit/90dbb0292cbf5ffe46975fe826ae8ac1732bb80e))


## ✨ Features
### quality-report
- **quality-report:** report test results produced elsewhere











# 🚀 Release 1.0.6 ([#20](https://github.com/the-reacher-data/loom-actions/pull/20)) ([`4af5799`](https://github.com/the-reacher-data/loom-actions/commit/4af57995f0565c02fb52dc355c6a02f94433f51c))



## 🐛 Fixes
### release
- **release:** pass the merged branch name through the environment










# 🚀 Release 1.0.5 ([#17](https://github.com/the-reacher-data/loom-actions/pull/17)) ([`b355b14`](https://github.com/the-reacher-data/loom-actions/commit/b355b14ed42098bc3d09d1aba1a71727a643358a))


## ✨ Features
### ai
- **ai:** pluggable MCP server authentication (#139)<br>
  > So a well-formed feat produced a changelog with a header and nothing under it.
  > The fallback now fires when no collected entry lands in a type the template
  > renders, not merely when the list is empty. A squash subject is always the PR
  > title; a squash body is free text anyone can write, and a single "Word: " line
  > was enough to swallow the release.
  > Verified by reverting the one-line condition: the new test fails against the
  > old behaviour and passes against the new one.
  > Claude-Session: https://claude.ai/code/session_01TLAapySqoC4wWTLxydLka3
  > Co-authored-by: Claude Fable 5 <noreply@anthropic.com>












# 🚀 Release 1.0.4 ([#14](https://github.com/the-reacher-data/loom-actions/pull/14)) ([`17779af`](https://github.com/the-reacher-data/loom-actions/commit/17779af7110d8f5f69c7cb5fa235f71825b42a9b))









## 🛠 Chores
- remove release badge from readme




# 🚀 Release 1.0.3 ([#12](https://github.com/the-reacher-data/loom-actions/pull/12)) ([`cfb2dc7`](https://github.com/the-reacher-data/loom-actions/commit/cfb2dc7c6c5c3a05a368449cd5890d07458eaa8d))









## 🛠 Chores
- normalize badges and auto-delete release branches
- label release PRs with target version
- simplify ruff and pyright section headers in report




# 🚀 Release 1.0.2 ([#10](https://github.com/the-reacher-data/loom-actions/pull/10)) ([`ea98554`](https://github.com/the-reacher-data/loom-actions/commit/ea985547d823e60b7e68b4c8f53510fc20013925))


## ✨ Features
- improve PR quality report UX layout








## 🛠 Chores
- refine workflow badges and no-background tooling icons




# 🚀 Release 1.0.1 ([#8](https://github.com/the-reacher-data/loom-actions/pull/8)) ([`63d6d1a`](https://github.com/the-reacher-data/loom-actions/commit/63d6d1aeb44e9a5c1f01d81e51bad3c1b40ba5b3))



## 🐛 Fixes
- use bot token for release PR chaining










# 🚀 Release 1.0.0 ([#6](https://github.com/the-reacher-data/loom-actions/pull/6)) ([`32cfe40`](https://github.com/the-reacher-data/loom-actions/commit/32cfe40037519e4ea44bbe6f9509d99ffbf311d7))


## ✨ Features
- standardize release docs and auto-merge release PR











# 🚀 Release 0.1.1 ([#4](https://github.com/the-reacher-data/loom-actions/pull/4)) ([`c476454`](https://github.com/the-reacher-data/loom-actions/commit/c4764540f51594199ec54bfdfd95edd4a87c2262))



## 🐛 Fixes
- trigger release for release-pr file-only merges
- trigger release from merged PR context<br>
  > --------
  > Co-authored-by: Administrador <administrador@MacBook-Pro-de-Administrador.local>
