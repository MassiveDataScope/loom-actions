"""``release-on-label`` releases through a GitHub App when the caller asks (contract L-b).

With ``client-id`` the workflow never writes with ``GITHUB_TOKEN``: ``plan``
resolves and checks the pull request of the release commit first, then the
jobs ``tag-app`` and ``release-app``, in the caller's environment, mint an App
token right before their first write and run nothing but ``gh``. Without it,
both jobs are skipped and the release is the one a caller always got (see
``test_release_on_label_compatibility.py``). Scripts run here as the runner
runs them, with a ``gh`` and a ``sleep`` that log every call.

The letters name the cases of the contract: (a) the interface, (b) the
configuration rule, (c) the rules of the App jobs and ``allowed-actors``,
(d) the minting, (e) the floating major tag, (g) the callers and (h) the
resolution of the pull request.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, cast

import gh_stub
import pytest
import workflow_steps as wf
import yaml
from gh_expressions import evaluate

NAME = "release-on-label"
ROOT = Path(__file__).parents[2]
APP_CALLER = ROOT / "tests" / "fixtures" / "callers" / "release-app-token.yml"
MINT = "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
APP_JOBS = ("tag-app", "release-app")
MERGE_SHA = "a" * 40
OTHER_SHA = "b" * 40
RESOLVE = "Resolve the pull request of the release commit"
ACTORS = "Require an allowed triggering actor"
MAJOR = "Move the floating major tag forward"
CREATE_RELEASE = "Create the GitHub release"
CREATE_TAG = "Create the immutable version tag"
DECODE = "Decode the release notes"
NOTES = "Pass the release notes to the App jobs"

CONFIGURATION = (
    "with client-id the workflow never commits the CHANGELOG; commit it on the release "
    "branch and set changelog-verify"
)
BAD_SHA = "merge-sha is not a 40-character hexadecimal SHA"
NO_ENVIRONMENT = "with client-id the workflow needs environment, the environment that holds the App's private key"
NO_ACTORS = "allowed-actors is empty: with client-id, name the logins that may release"
# The messages keep the backticks the contract's table puts around each value: they are
# part of the message, so a log search for `<sha>` finds the run.
TAG_APP_IF = "${{ inputs.client-id != '' && needs.plan.outputs.version != '' }}"
RELEASE_APP_IF = (
    "${{ !cancelled() && needs.plan.result == 'success' && needs.tag-app.result == 'success'"
    " && (needs.build.result == 'success' || needs.build.result == 'skipped')"
    " && inputs.client-id != '' && needs.plan.outputs.version != '' }}"
)
BUILD_IF = (
    "${{ !cancelled() && needs.plan.result == 'success'"
    " && (needs.tag-app.result == 'success' || needs.tag-app.result == 'skipped')"
    " && inputs.build-distribution && needs.plan.outputs.version != '' }}"
)
RELEASE_IF = (
    "${{ !cancelled() && needs.plan.result == 'success'"
    " && (needs.build.result == 'success' || needs.build.result == 'skipped')"
    " && needs.plan.outputs.version != '' && inputs.client-id == '' }}"
)


def _job(name: str) -> dict[str, Any]:
    return wf.jobs(NAME)[name]


def _ids(job: str) -> list[str]:
    return [cast(str, s.get("id", "")) for s in wf.steps(NAME, job)]


def _by_id(job: str, step_id: str) -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, job) if s.get("id") == step_id)


def _run_id(job: str, step_id: str, env: dict[str, str], cwd: Path) -> Any:
    return wf.run(NAME, job, cast(str, _by_id(job, step_id)["name"]), env, cwd)


# (a) ---------------------------------------------------------------------------


class TestTheInterface:
    @pytest.mark.parametrize(
        "name", ["client-id", "environment", "allowed-actors", "allowed-head-prefixes"]
    )
    def test_each_new_string_input_is_optional_and_empty(self, name: str) -> None:
        declared = wf.call(NAME)["inputs"][name]
        assert declared["type"] == "string"
        assert declared["required"] is False
        assert declared["default"] == ""

    def test_changelog_verify_is_an_optional_boolean_defaulting_to_false(self) -> None:
        declared = wf.call(NAME)["inputs"]["changelog-verify"]
        assert declared["type"] == "boolean"
        assert declared["required"] is False
        assert declared["default"] is False

    def test_the_app_key_is_an_optional_secret(self) -> None:
        secrets = wf.call(NAME)["secrets"]
        assert list(secrets) == ["APP_PRIVATE_KEY"]
        assert secrets["APP_PRIVATE_KEY"]["required"] is False

    def test_the_release_commit_is_an_output(self) -> None:
        assert wf.call(NAME)["outputs"]["release-sha"]["value"] == (
            "${{ jobs.plan.outputs.release_sha }}"
        )

    def test_the_key_is_read_only_where_the_token_is_minted(self) -> None:
        for job_name in wf.jobs(NAME):
            for step in wf.steps(NAME, job_name):
                if "APP_PRIVATE_KEY" in str(step):
                    assert job_name in APP_JOBS
                    assert step["uses"].startswith(MINT)


# (b) and (h) ---------------------------------------------------------------------


def _pull(
    number: int = 7,
    *,
    sha: str = MERGE_SHA,
    base: str = "master",
    merged: bool = True,
    labels: tuple[str, ...] = ("release",),
    head: str = "release/1.2.0",
    head_repo: str | None = "o/r",
) -> dict[str, Any]:
    return {
        "number": number,
        "merged_at": "2026-11-11T10:00:00Z" if merged else None,
        "merge_commit_sha": sha,
        "base": {"ref": base},
        "head": {"ref": head, "repo": None if head_repo is None else {"full_name": head_repo}},
        "labels": [{"name": label} for label in labels],
    }


class TestResolvingThePullRequest:
    def _resolve(
        self,
        tmp_path: Path,
        answers: list[list[dict[str, Any]]],
        *,
        sha: str = MERGE_SHA,
        prefixes: str = "release/ hotfix/",
        changelog: str = "false",
        environment: str = "release",
    ) -> tuple[Any, gh_stub.Stub]:
        stub = gh_stub.install(
            tmp_path,
            [("/pulls", [gh_stub.answer(pulls) for pulls in answers] or [gh_stub.answer([])])],
        )
        env = stub.env | {
            "MERGE_SHA": sha,
            "BASE_BRANCH": "master",
            "RELEASE_LABEL": "release",
            "ALLOWED_HEAD_PREFIXES": prefixes,
            "CHANGELOG": changelog,
            "ENVIRONMENT": environment,
        }
        return _run_id("plan", "resolve-pr", env, tmp_path), stub

    def test_it_is_the_first_step_of_the_plan_and_runs_only_with_an_app(self) -> None:
        first = wf.steps(NAME, "plan")[0]
        assert first["id"] == "resolve-pr"
        assert first["if"] == "${{ inputs.client-id != '' }}"
        assert "uses" not in first

    def test_nothing_can_mint_before_it_ends(self) -> None:
        for name in APP_JOBS:
            needs = _job(name)["needs"]
            assert "plan" in ([needs] if isinstance(needs, str) else needs)

    def test_it_reads_the_inputs_from_the_environment_with_the_plans_token(self) -> None:
        step = _by_id("plan", "resolve-pr")
        assert step["env"] == {
            "GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
            "BASE_BRANCH": "${{ inputs.base-branch }}",
            "RELEASE_LABEL": "${{ inputs.release-label }}",
            "ALLOWED_HEAD_PREFIXES": "${{ inputs.allowed-head-prefixes }}",
            "CHANGELOG": "${{ inputs.changelog }}",
            "ENVIRONMENT": "${{ inputs.environment }}",
        }
        assert _job("plan")["env"]["MERGE_SHA"].startswith("${{ inputs.merge-sha != ''")

    def test_a_labelled_release_pull_request_passes_in_one_call(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull()]])
        assert result.returncode == 0, result.stdout + result.stderr
        assert stub.calls() == [f"api repos/o/r/commits/{MERGE_SHA}/pulls"]
        assert stub.sleeps() == []

    def test_the_configuration_rule_fails_before_any_call(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull()]], changelog="true")
        assert result.returncode == 1
        assert CONFIGURATION in result.stdout
        assert stub.calls() == []

    @pytest.mark.parametrize("environment", ["", " "])
    def test_an_app_without_an_environment_fails_before_any_call(
        self, tmp_path: Path, environment: str
    ) -> None:
        result, stub = self._resolve(tmp_path, [[_pull()]], environment=environment)
        assert result.returncode == 1
        assert NO_ENVIRONMENT in result.stdout
        assert stub.calls() == []

    @pytest.mark.parametrize(
        ("head_repo", "shown"), [("fork/r", "fork/r"), ("o/r2", "o/r2"), (None, "")]
    )
    def test_a_head_from_another_repository_fails_at_once(
        self, tmp_path: Path, head_repo: str | None, shown: str
    ) -> None:
        result, stub = self._resolve(tmp_path, [[_pull(head_repo=head_repo)]])
        assert result.returncode == 1
        assert len(stub.calls()) == 1
        assert f"pull request #7 head repository `{shown}` is not `o/r`" in result.stdout

    @pytest.mark.parametrize("sha", ["", "A" * 40, "a" * 39, "a" * 41, "g" * 40, "a" * 39 + "\n"])
    def test_an_invalid_sha_fails_before_any_call(self, tmp_path: Path, sha: str) -> None:
        result, stub = self._resolve(tmp_path, [[_pull()]], sha=sha)
        assert result.returncode == 1
        assert BAD_SHA in result.stdout
        assert stub.calls() == []

    @pytest.mark.parametrize(
        "pulls",
        [
            [],
            [_pull(merged=False)],
            [_pull(base="develop")],
            [_pull(sha=OTHER_SHA)],
        ],
        ids=["none", "not-merged", "other-base", "other-merge-commit"],
    )
    def test_no_matching_pull_request_fails_after_five_attempts(
        self, tmp_path: Path, pulls: list[dict[str, Any]]
    ) -> None:
        result, stub = self._resolve(tmp_path, [pulls])
        assert result.returncode == 1
        assert len(stub.calls()) == 5
        assert stub.sleeps() == ["5", "10", "15", "20"]
        assert (
            f"no merged pull request into `master` has merge commit `{MERGE_SHA}` after 5 "
            "attempts; relaunch with workflow_dispatch on `master`"
        ) in result.stdout

    def test_a_pull_request_found_on_a_retry_passes(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[], [_pull()]])
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(stub.calls()) == 2
        assert stub.sleeps() == ["5"]

    def test_two_pull_requests_fail_at_once(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull(7), _pull(8)]])
        assert result.returncode == 1
        assert len(stub.calls()) == 1
        assert stub.sleeps() == []
        assert (
            f"more than one merged pull request into `master` has merge commit `{MERGE_SHA}`"
        ) in result.stdout

    def test_an_unlabelled_pull_request_fails_at_once(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull(labels=("bug", "Release"))]])
        assert result.returncode == 1
        assert len(stub.calls()) == 1
        assert "pull request #7 is not labelled `release`" in result.stdout

    def test_a_head_outside_the_prefixes_fails_at_once(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull(head="feature/x")]])
        assert result.returncode == 1
        assert len(stub.calls()) == 1
        assert "pull request #7 head `feature/x` does not match allowed-head-prefixes" in (
            result.stdout
        )

    @pytest.mark.parametrize("head", ["release/1.2.0", "hotfix/1.2.1"])
    def test_a_head_inside_the_prefixes_passes(self, tmp_path: Path, head: str) -> None:
        result, _ = self._resolve(tmp_path, [[_pull(head=head)]], prefixes="release/\nhotfix/")
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("head", ["xrelease/1", "release", "Release/1", "release-1"])
    def test_a_prefix_is_literal_and_anchored(self, tmp_path: Path, head: str) -> None:
        result, _ = self._resolve(tmp_path, [[_pull(head=head)]], prefixes="release/")
        assert result.returncode == 1

    def test_a_glob_in_the_prefixes_is_no_wildcard(self, tmp_path: Path) -> None:
        result, _ = self._resolve(tmp_path, [[_pull(head="feature/x")]], prefixes="*")
        assert result.returncode == 1

    def test_without_prefixes_the_head_is_not_checked(self, tmp_path: Path) -> None:
        result, stub = self._resolve(tmp_path, [[_pull(head="feature/x")]], prefixes="")
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(stub.calls()) == 1

    def test_each_failure_has_its_own_message(self) -> None:
        script = cast(str, _by_id("plan", "resolve-pr")["run"])
        for message in (
            CONFIGURATION,
            NO_ENVIRONMENT,
            BAD_SHA,
            "after 5 attempts; relaunch with workflow_dispatch on",
            "more than one merged pull request into",
            "is not labelled",
            "does not match allowed-head-prefixes",
            "head repository",
        ):
            assert message in script, message


def _plan_context(**values: object) -> dict[str, object]:
    return {
        "inputs.client-id": "Iv23",
        "inputs.changelog-verify": True,
        "inputs.changelog": False,
        "steps.plan.outputs.version": "1.2.0",
        **values,
    }


class TestTheVersionDecidesWhatFollows:
    """A valid pull request with no version ends the plan green, and nothing is written."""

    @pytest.mark.parametrize("step_id", ["changelog-base", "verify-changelog", "notes"])
    def test_without_a_version_the_verification_and_the_notes_are_skipped(
        self, step_id: str
    ) -> None:
        condition = _by_id("plan", step_id)["if"]
        assert evaluate(condition, _plan_context()) is True
        assert evaluate(condition, _plan_context(**{"steps.plan.outputs.version": ""})) is False

    def test_with_the_app_the_plan_never_creates_the_tag(self) -> None:
        tag = wf.step(NAME, "plan", CREATE_TAG)["if"]
        assert evaluate(tag, _plan_context()) is False
        assert evaluate(tag, _plan_context(**{"inputs.client-id": ""})) is True

    @pytest.mark.parametrize("job", APP_JOBS)
    def test_without_a_version_the_app_jobs_are_skipped(self, job: str) -> None:
        context = {
            "inputs.client-id": "Iv23",
            "needs.plan.outputs.version": "",
            "needs.plan.result": "success",
            "needs.tag-app.result": "skipped",
            "needs.build.result": "skipped",
            "cancelled()": False,
        }
        assert evaluate(_job(job)["if"], context) is False


# (c) ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", APP_JOBS)
class TestTheAppJobs:
    def test_they_run_in_the_callers_environment_without_github_token(self, job: str) -> None:
        assert _job(job)["environment"] == "${{ inputs.environment }}"
        # GITHUB_TOKEN gets nothing: every write uses the App token.
        assert _job(job)["permissions"] == {}
        assert "timeout-minutes" in _job(job)

    def test_they_are_serialised_per_tag_prefix_without_cancelling(self, job: str) -> None:
        assert _job(job)["concurrency"] == {
            "group": "release-on-label-app-${{ github.repository }}-${{ inputs.tag-prefix }}",
            "cancel-in-progress": False,
        }

    def test_they_check_out_nothing_and_run_only_the_minting_action(self, job: str) -> None:
        uses = [s["uses"] for s in wf.steps(NAME, job) if "uses" in s]
        assert len(uses) == 1
        assert uses[0] == MINT

    def test_the_actor_check_is_the_first_step(self, job: str) -> None:
        first = wf.steps(NAME, job)[0]
        assert first["id"] == "allowed-actors"
        assert first["name"] == ACTORS
        assert "if" not in first and "uses" not in first
        assert "continue-on-error" not in first
        assert first["env"] == {
            "ALLOWED_ACTORS": "${{ inputs.allowed-actors }}",
            "TRIGGERING_ACTOR": "${{ github.triggering_actor }}",
        }

    def test_no_step_after_a_failure_runs(self, job: str) -> None:
        """A failed step stops the job unless a later step asks to run anyway."""
        for step in wf.steps(NAME, job):
            assert "if" not in step, step.get("name")
            assert "continue-on-error" not in step, step.get("name")

    def test_no_script_traces_or_leaks_the_token(self, job: str) -> None:
        text = yaml.safe_dump(_job(job))
        assert "github.token" not in text
        assert "secrets.GITHUB_TOKEN" not in text
        assert "outputs" not in _job(job)
        for step in wf.steps(NAME, job):
            script = cast(str, step.get("run", ""))
            assert "set -x" not in script and "xtrace" not in script
            assert "GITHUB_ENV" not in script
            # Only the repository name is a step output; the token never is.
            assert ("GITHUB_OUTPUT" in script) is (step.get("id") == "repo")
            assert "${{" not in script

    def test_every_write_uses_the_app_token_and_checks_it(self, job: str) -> None:
        for step in wf.steps(NAME, job):
            env = cast(dict[str, str], step.get("env", {}))
            if "GH_TOKEN" in env:
                assert env["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
                assert '[ -z "${GH_TOKEN}" ]' in step["run"]


class TestTheActorCheck:
    def _check(self, tmp_path: Path, job: str, allowed: str, actor: str) -> Any:
        env = {"ALLOWED_ACTORS": allowed, "TRIGGERING_ACTOR": actor}
        return _run_id(job, "allowed-actors", env, tmp_path)

    @pytest.mark.parametrize("job", APP_JOBS)
    @pytest.mark.parametrize("allowed", ["", " ", "\n", " \t\r\n"])
    def test_an_empty_list_fails_closed(self, tmp_path: Path, job: str, allowed: str) -> None:
        """The App jobs run only with client-id, where an empty list is a mistake."""
        result = self._check(tmp_path, job, allowed, "the-reacher-data")
        assert result.returncode == 1
        assert NO_ACTORS in result.stdout

    @pytest.mark.parametrize("job", APP_JOBS)
    @pytest.mark.parametrize(
        "allowed", ["the-reacher-data", "someone the-reacher-data", "a\nthe-reacher-data\n"]
    )
    def test_a_listed_actor_passes(self, tmp_path: Path, job: str, allowed: str) -> None:
        result = self._check(tmp_path, job, allowed, "the-reacher-data")
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("job", APP_JOBS)
    @pytest.mark.parametrize("actor", ["mdscope-agent", "The-Reacher-Data", "the-reacher", ""])
    def test_an_unlisted_actor_fails_before_any_token(
        self, tmp_path: Path, job: str, actor: str
    ) -> None:
        stub = gh_stub.install(tmp_path, [])
        result = wf.run(
            NAME,
            job,
            ACTORS,
            stub.env | {"ALLOWED_ACTORS": "the-reacher-data", "TRIGGERING_ACTOR": actor},
            tmp_path,
        )
        assert result.returncode == 1
        assert f"triggering actor `{actor}` is not in allowed-actors" in result.stdout
        assert stub.calls() == []
        ids = _ids(job)
        assert ids.index("allowed-actors") < ids.index("app")


class TestTheJobGraph:
    def test_tag_app_follows_the_plan(self) -> None:
        assert _job("tag-app")["needs"] == "plan"
        assert _job("tag-app")["if"] == TAG_APP_IF

    def test_release_app_needs_the_plan_the_tag_and_the_build(self) -> None:
        assert _job("release-app")["needs"] == ["plan", "tag-app", "build"]
        assert _job("release-app")["if"] == RELEASE_APP_IF

    def test_the_build_waits_for_the_tag(self) -> None:
        assert _job("build")["needs"] == ["plan", "tag-app"]
        assert _job("build")["if"] == BUILD_IF

    def test_the_release_job_is_the_path_without_an_app(self) -> None:
        assert _job("release")["needs"] == ["plan", "build"]
        assert _job("release")["if"] == RELEASE_IF


# (d) ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", APP_JOBS)
class TestTheMinting:
    def test_it_is_pinned_and_asks_for_one_permission(self, job: str) -> None:
        mint = _by_id(job, "app")
        assert re.fullmatch(r"actions/create-github-app-token@[0-9a-f]{40}", mint["uses"])
        assert mint["with"] == {
            "client-id": "${{ inputs.client-id }}",
            "private-key": "${{ secrets.APP_PRIVATE_KEY }}",
            "owner": "${{ github.repository_owner }}",
            "repositories": "${{ steps.repo.outputs.name }}",
            "permission-contents": "write",
        }

    def test_it_comes_right_before_the_first_write(self, job: str) -> None:
        steps = wf.steps(NAME, job)
        mint = _ids(job).index("app")
        writes = [i for i, s in enumerate(steps) if "GH_TOKEN" in s.get("env", {})]
        assert writes and writes[0] == mint + 1
        assert all("GH_TOKEN" not in s.get("env", {}) for s in steps[:mint])

    def test_the_repository_is_named_right_before_the_mint(self, job: str) -> None:
        ids = _ids(job)
        assert ids.index("repo") == ids.index("app") - 1
        assert "env" not in _by_id(job, "repo")
        assert "github.event" not in yaml.safe_dump(_job(job))

    @pytest.mark.parametrize(
        ("repository", "name"),
        [("o/r", "r"), ("Acme/periplo-cloud", "periplo-cloud"), ("o/a.b_c", "a.b_c")],
    )
    def test_the_name_comes_from_github_repository(
        self, tmp_path: Path, job: str, repository: str, name: str
    ) -> None:
        output = tmp_path / "output"
        env = {"GITHUB_REPOSITORY": repository, "GITHUB_OUTPUT": str(output)}
        result = _run_id(job, "repo", env, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert output.read_text("utf-8") == f"name={name}\n"

    @pytest.mark.parametrize("repository", ["", "o/", "o/r/x", "o/r x", "o/r\nname=x", "o/$(id)"])
    def test_a_bad_name_fails(self, tmp_path: Path, job: str, repository: str) -> None:
        output = tmp_path / "output"
        env = {"GITHUB_REPOSITORY": repository, "GITHUB_OUTPUT": str(output)}
        result = _run_id(job, "repo", env, tmp_path)
        assert result.returncode == 1
        assert not output.exists() or output.read_text("utf-8") == ""

    def test_the_version_comment_names_v3_3_0(self, job: str) -> None:
        text = (wf.WORKFLOWS / f"{NAME}.yml").read_text("utf-8")
        assert f"uses: {MINT} # v3.3.0" in text


# The tag ---------------------------------------------------------------------------


def _ref(name: str, sha: str, kind: str = "commit") -> dict[str, Any]:
    return {"ref": f"refs/tags/{name}", "object": {"sha": sha, "type": kind}}


APP_ENV = {
    "VERSION": "1.2.0",
    "RELEASE_SHA": MERGE_SHA,
    "TAG_PREFIX": "control-plane/v",
}


class TestTheTagApp:
    def _tag(self, tmp_path: Path, refs: list[dict[str, Any]], token: str = "t") -> Any:
        stub = gh_stub.install(
            tmp_path,
            [("matching-refs", [gh_stub.answer(refs)]), ("--method POST", [gh_stub.answer({})])],
        )
        result = _run_id("tag-app", "tag", stub.env | APP_ENV | {"GH_TOKEN": token}, tmp_path)
        return result, stub.calls()

    def test_it_creates_the_tag_on_the_release_commit(self, tmp_path: Path) -> None:
        result, calls = self._tag(tmp_path, [])
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/control-plane/v1.2.0",
            "api --method POST repos/o/r/git/refs "
            f"-f ref=refs/tags/control-plane/v1.2.0 -f sha={MERGE_SHA}",
        ]

    def test_an_existing_tag_on_the_release_commit_is_kept(self, tmp_path: Path) -> None:
        result, calls = self._tag(tmp_path, [_ref("control-plane/v1.2.0", MERGE_SHA)])
        assert result.returncode == 0
        assert len(calls) == 1

    def test_an_existing_tag_on_another_commit_fails(self, tmp_path: Path) -> None:
        result, calls = self._tag(tmp_path, [_ref("control-plane/v1.2.0", OTHER_SHA)])
        assert result.returncode == 1
        assert len(calls) == 1

    def test_an_empty_token_writes_nothing(self, tmp_path: Path) -> None:
        result, calls = self._tag(tmp_path, [], token="")
        assert result.returncode == 1
        assert calls == []


@pytest.mark.parametrize("job", APP_JOBS)
class TestTheInputsAreValidated:
    @pytest.mark.parametrize(
        "change",
        [
            {"VERSION": "1.2"},
            {"VERSION": "1.2.0;id"},
            {"RELEASE_SHA": "a" * 39},
            {"RELEASE_SHA": "A" * 40},
            {"TAG_PREFIX": "-v"},
            {"TAG_PREFIX": "a..b"},
        ],
    )
    def test_a_bad_input_fails(self, tmp_path: Path, job: str, change: dict[str, str]) -> None:
        env = APP_ENV | {"NOTES_B64": "aGk="} | change
        assert _run_id(job, "inputs", env, tmp_path).returncode == 1

    def test_good_inputs_pass(self, tmp_path: Path, job: str) -> None:
        env = APP_ENV | {"NOTES_B64": "aGk="}
        result = _run_id(job, "inputs", env, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("notes", ["", "not base64!", "aGk=\nc29tZQ=="])
def test_release_app_refuses_notes_that_are_not_base64(tmp_path: Path, notes: str) -> None:
    env = APP_ENV | {"NOTES_B64": notes}
    assert _run_id("release-app", "inputs", env, tmp_path).returncode == 1


# (e) ---------------------------------------------------------------------------


class TestTheFloatingMajorTag:
    def _move(
        self, tmp_path: Path, refs: list[dict[str, Any]], status: str | None = None
    ) -> tuple[Any, list[str]]:
        rules = [
            ("matching-refs", [gh_stub.answer(refs)]),
            ("/compare/", [gh_stub.answer({"status": status})]),
            ("--method", [gh_stub.answer({})]),
        ]
        stub = gh_stub.install(tmp_path, rules)
        summary = tmp_path / "summary.md"
        env = stub.env | APP_ENV | {"GITHUB_STEP_SUMMARY": str(summary)}
        result = _run_id("release-app", "major", env, tmp_path)
        return result, stub.calls()

    def test_ahead_moves_it(self, tmp_path: Path) -> None:
        result, calls = self._move(tmp_path, [_ref("control-plane/v1", OTHER_SHA)], "ahead")
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/control-plane/v1",
            f"api repos/o/r/compare/{OTHER_SHA}...{MERGE_SHA}",
            "api --method PATCH repos/o/r/git/refs/tags/control-plane/v1 "
            f"-f sha={MERGE_SHA} -F force=false",
        ]

    def test_identical_writes_nothing(self, tmp_path: Path) -> None:
        result, calls = self._move(tmp_path, [_ref("control-plane/v1", MERGE_SHA)], "identical")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any("--method" in call for call in calls)

    def test_behind_keeps_it_and_warns(self, tmp_path: Path) -> None:
        result, calls = self._move(tmp_path, [_ref("control-plane/v1", OTHER_SHA)], "behind")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any("--method" in call for call in calls)
        assert "::warning" in result.stdout
        assert "control-plane/v1" in (tmp_path / "summary.md").read_text("utf-8")

    def test_diverged_fails(self, tmp_path: Path) -> None:
        result, calls = self._move(tmp_path, [_ref("control-plane/v1", OTHER_SHA)], "diverged")
        assert result.returncode == 1
        assert not any("--method" in call for call in calls)

    def test_a_missing_major_is_created(self, tmp_path: Path) -> None:
        result, calls = self._move(tmp_path, [_ref("control-plane/v1.2.0", MERGE_SHA)])
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[-1] == (
            "api --method POST repos/o/r/git/refs "
            f"-f ref=refs/tags/control-plane/v1 -f sha={MERGE_SHA}"
        )

    def test_an_annotated_major_fails(self, tmp_path: Path) -> None:
        refs = [_ref("control-plane/v1", OTHER_SHA, kind="tag")]
        result, calls = self._move(tmp_path, refs, "ahead")
        assert result.returncode == 1
        assert not any("--method" in call for call in calls)

    def test_it_runs_before_the_release(self) -> None:
        ids = _ids("release-app")
        assert ids.index("app") < ids.index("major") < ids.index("release")


class TestTheRelease:
    def _release(
        self,
        tmp_path: Path,
        exists: bool,
        tag_sha: str = MERGE_SHA,
        **release: Any,
    ) -> Any:
        found = {
            "isDraft": False,
            "isPrerelease": False,
            "author": {"login": "periplo-release[bot]"},
        } | release
        rules = [
            ("release view", [gh_stub.answer(found) if exists else gh_stub.answer(exit=1)]),
            ("matching-refs", [gh_stub.answer([_ref("control-plane/v1.2.0", tag_sha)])]),
            ("release create", [gh_stub.answer("")]),
        ]
        stub = gh_stub.install(tmp_path, rules)
        notes = tmp_path / "release-notes.md"
        notes.write_text("## notes\n", "utf-8")
        env = stub.env | APP_ENV | {"RUNNER_TEMP": str(tmp_path)}
        return _run_id("release-app", "release", env, tmp_path), stub.calls()

    def test_it_creates_the_release_on_the_existing_tag(self, tmp_path: Path) -> None:
        result, calls = self._release(tmp_path, exists=False)
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls[-1] == (
            "release create control-plane/v1.2.0 --repo o/r --verify-tag "
            f"--title control-plane/v1.2.0 --notes-file {tmp_path}/release-notes.md"
        )

    def test_an_existing_release_on_the_tag_is_kept(self, tmp_path: Path) -> None:
        result, calls = self._release(tmp_path, exists=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not any("release create" in call for call in calls)

    @pytest.mark.parametrize(
        "release",
        [
            {"isDraft": True},
            {"isPrerelease": True},
            {"author": {"login": "the-reacher-data"}},
            {"author": {"login": "bot"}},
            {"author": None},
        ],
    )
    def test_an_existing_release_not_published_by_an_app_fails(
        self, tmp_path: Path, release: dict[str, Any]
    ) -> None:
        result, calls = self._release(tmp_path, exists=True, **release)
        assert result.returncode == 1
        assert not any("release create" in call for call in calls)

    def test_it_reads_the_release_it_finds(self, tmp_path: Path) -> None:
        _, calls = self._release(tmp_path, exists=True)
        assert calls[0] == (
            "release view control-plane/v1.2.0 --repo o/r --json isDraft,isPrerelease,author"
        )

    def test_an_existing_release_whose_tag_moved_fails(self, tmp_path: Path) -> None:
        result, calls = self._release(tmp_path, exists=True, tag_sha=OTHER_SHA)
        assert result.returncode == 1
        assert not any("release create" in call for call in calls)


class TestTheNotesReachTheReleaseAsBase64:
    def test_the_plan_encodes_its_notes(self, tmp_path: Path) -> None:
        (tmp_path / "CHANGELOG_RELEASE.md").write_text("## 1.2.0\n\n- é `x` $(id)\n", "utf-8")
        output = tmp_path / "output"
        result = _run_id("plan", "notes", {"GITHUB_OUTPUT": str(output)}, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        (line,) = output.read_text("utf-8").splitlines()
        key, _, value = line.partition("=")
        assert key == "notes_b64"
        assert base64.b64decode(value).decode("utf-8") == "## 1.2.0\n\n- é `x` $(id)\n"
        assert _job("plan")["outputs"]["notes_b64"] == "${{ steps.notes.outputs.notes_b64 }}"

    def test_oversized_notes_fail(self, tmp_path: Path) -> None:
        (tmp_path / "CHANGELOG_RELEASE.md").write_text("x" * 100_001, "utf-8")
        output = tmp_path / "output"
        result = _run_id("plan", "notes", {"GITHUB_OUTPUT": str(output)}, tmp_path)
        assert result.returncode == 1
        assert not output.exists() or output.read_text("utf-8") == ""

    def test_release_app_decodes_them_to_a_file(self, tmp_path: Path) -> None:
        text = "## 1.2.0\n\n- é\n"
        env = {"NOTES_B64": base64.b64encode(text.encode()).decode(), "RUNNER_TEMP": str(tmp_path)}
        result = _run_id("release-app", "decode", env, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "release-notes.md").read_text("utf-8") == text
        assert _by_id("release-app", "decode")["name"] == DECODE


class TestATagFailureStopsTheRelease:
    """(e) If tag-app fails, release-app is skipped and nothing moves the major tag."""

    def _context(self, tag_app: str) -> dict[str, object]:
        return {
            "cancelled()": False,
            "inputs.client-id": "Iv23",
            "inputs.build-distribution": True,
            "needs.plan.result": "success",
            "needs.plan.outputs.version": "1.2.0",
            "needs.tag-app.result": tag_app,
            "needs.build.result": "skipped",
        }

    def test_release_app_runs_once_the_tag_exists(self) -> None:
        assert evaluate(RELEASE_APP_IF, self._context("success")) is True

    @pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
    def test_release_app_is_skipped_otherwise(self, result: str) -> None:
        context = self._context(result)
        assert evaluate(_job("release-app")["if"], context) is False
        assert evaluate(_job("build")["if"], context) is (result == "skipped")
        assert evaluate(_job("release")["if"], context) is False

    def test_only_release_app_moves_the_major_tag_with_an_app(self) -> None:
        for name, job in wf.jobs(NAME).items():
            if "refs/tags/${MAJOR_TAG}" in yaml.safe_dump(job) and name != "release-app":
                assert evaluate(job["if"], self._context("failure")) is False


# (g) ---------------------------------------------------------------------------


class TestTheAppCaller:
    def _job(self) -> dict[str, Any]:
        return cast(
            dict[str, Any], yaml.safe_load(APP_CALLER.read_text("utf-8"))["jobs"]["release"]
        )

    def test_it_passes_the_key_by_name(self) -> None:
        job = self._job()
        assert job["secrets"] == {"APP_PRIVATE_KEY": "${{ secrets.APP_PRIVATE_KEY }}"}
        lines = APP_CALLER.read_text("utf-8").splitlines()
        assert not [line for line in lines if re.match(r"\s*secrets:\s*inherit", line)]

    def test_it_releases_the_pushed_or_the_given_commit(self) -> None:
        caller = yaml.safe_load(APP_CALLER.read_text("utf-8"))
        triggers = caller.get("on", caller.get(True))
        assert triggers["push"] == {"branches": ["master"]}
        assert "merge_sha" in triggers["workflow_dispatch"]["inputs"]
        assert self._job()["with"]["merge-sha"] == "${{ inputs.merge_sha || github.sha }}"

    def test_it_never_commits_and_verifies_the_changelog(self) -> None:
        given = self._job()["with"]
        assert given["changelog"] is False
        assert given["changelog-verify"] is True
        assert given["client-id"] and given["environment"]
        assert given["allowed-actors"] == "the-reacher-data"
        assert given["allowed-head-prefixes"] == "release/ hotfix/"

    def test_it_runs_only_on_master_for_the_owner(self) -> None:
        condition = self._job()["if"]
        assert "github.ref == 'refs/heads/master'" in condition
        assert "github.triggering_actor == 'the-reacher-data'" in condition
        assert "event_name" not in condition
