from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cumulusci.utils.yaml.cumulusci_yml import ReleaseBranchFormat

from cumulusci_ado.vcs.ado.release_notes.generator import ADOReleaseNotesGenerator
from cumulusci_ado.vcs.ado.release_notes.parser import ADOLinesParser, parser_configs
from cumulusci_ado.vcs.ado.release_notes.provider import ADOChangeNotesProvider

FY_FORMAT = ReleaseBranchFormat(
    type="date", prefix="FY", pattern="yyQqSn", max_sprints_per_quarter=7
)


class FakePullRequest:
    def __init__(
        self,
        number,
        title="Subject",
        body="",
        html_url="https://dev.azure.com/org/proj/_git/repo/pullrequest/1",
        head_ref="feature/some-work",
        merge_commit_sha="abc",
        merged_at=None,
    ):
        self.number = number
        self.title = title
        self.body = body
        self.html_url = html_url
        self.head_ref = head_ref
        self.merge_commit_sha = merge_commit_sha
        self.merged_at = merged_at


def _github_info():
    return {
        "github_owner": "org",
        "github_repo": "repo",
        "default_branch": "main",
        "prefix_beta": "beta/",
        "prefix_prod": "release/",
    }


def _mock_project_config():
    config = MagicMock()
    config.project__git__prefix_feature = "feature/"
    config.project__git__prefix_release = "release/"
    config.project__git__default_branch = "main"
    config.project__git__release_branch_format__type = "date"
    config.project__git__release_branch_format__prefix = "FY"
    config.project__git__release_branch_format__pattern = "yyQqSn"
    config.project__git__release_branch_format__max_sprints_per_quarter = 7
    config.repo_branch = "main"
    config.get_release_branch_prefix_and_format_config.return_value = (
        "feature/",
        FY_FORMAT,
    )
    return config


def _mock_repo(**overrides):
    repo = MagicMock()
    repo.project_name = "sfcore-p1-base"
    repo.default_branch = "main"
    repo.project_config = _mock_project_config()
    repo.get_version_package.return_value = (MagicMock(version="1.2.0-release"), None)
    repo.artifact_ui_url.return_value = (
        "https://dev.azure.com/org/proj/_artifacts/feed/feed/UPack/sfcore-p1-base/"
        "overview/1.2.0-release"
    )
    repo.compare_files_url.return_value = (
        "https://dev.azure.com/org/proj/_git/repo/branchCompare"
        "?baseVersion=GTbeta/1.1.0&targetVersion=GTbeta/1.2.0&_a=files"
    )
    repo.resolve_release_notes_path.return_value = "release-notes/beta/1.2.0.md"
    repo.list_tag_names.return_value = ["release/1.1.0", "beta/1.2.0"]
    repo.config.side_effect = lambda key: None
    repo.pull_requests.return_value = []
    for key, value in overrides.items():
        setattr(repo, key, value)
    return repo


class TestParserConfigs:
    def test_prefers_azure_devops_parsers(self):
        config = MagicMock()
        config.project__git__release_notes__parsers__azure_devops = {
            1: {"class_path": "pkg.Parser", "title": "Changes"}
        }
        result = parser_configs(config)
        assert result == [{"class_path": "pkg.Parser", "title": "Changes"}]

    def test_skips_nested_provider_map(self):
        config = MagicMock()
        config.project__git__release_notes__parsers__azure_devops = None
        config.project__git__release_notes__parsers = {
            "github": {1: {"class_path": "g.Parser", "title": "G"}},
            "azure_devops": {1: {"class_path": "a.Parser", "title": "A"}},
        }
        result = parser_configs(config)
        assert result == [{"class_path": "a.Parser", "title": "A"}]


class TestADOLinesParser:
    def test_parses_headed_section_and_links(self):
        generator = SimpleNamespace(link_pr=True)
        parser = ADOLinesParser(generator, "Changes")
        pr = FakePullRequest(
            12,
            body="# Changes\n- Fixed the thing\n# Other\nignored",
            html_url="https://example/pullrequest/12",
        )
        assert parser.parse(pr) is True
        rendered = parser.render()
        assert "Fixed the thing" in rendered
        assert "[[PR12](https://example/pullrequest/12)]" in rendered
        assert "ignored" not in rendered


class TestADOChangeNotesProvider:
    def _commit(self, sha, when):
        commit = MagicMock()
        commit.sha = sha
        commit.author_date = when
        return commit

    def test_includes_prs_between_tags(self):
        last = datetime(2023, 1, 1, tzinfo=UTC)
        current = datetime(2023, 2, 1, tzinfo=UTC)
        in_range = FakePullRequest(
            2,
            merge_commit_sha="mid",
            merged_at=datetime(2023, 1, 15, tzinfo=UTC),
        )
        too_old = FakePullRequest(
            1,
            merge_commit_sha="old",
            merged_at=datetime(2022, 12, 1, tzinfo=UTC),
        )
        current_merge = FakePullRequest(
            3,
            merge_commit_sha="cursha",
            merged_at=datetime(2023, 3, 1, tzinfo=UTC),
        )
        last_merge = FakePullRequest(
            0,
            merge_commit_sha="lastsha",
            merged_at=datetime(2023, 1, 1, tzinfo=UTC),
        )
        repo = _mock_repo()
        repo.pull_requests.return_value = [too_old, last_merge, in_range, current_merge]
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        last_commit = self._commit("lastsha", last)
        current_commit = self._commit("cursha", current)
        with patch(
            "cumulusci_ado.vcs.ado.release_notes.provider.get_tag_by_name"
        ) as mock_tag:
            mock_tag.side_effect = [
                SimpleNamespace(sha="lastsha"),
                SimpleNamespace(sha="cursha"),
            ]
            repo.get_commit.side_effect = [last_commit, current_commit]
            provider = ADOChangeNotesProvider(
                generator, "release/1.2.0", last_tag="release/1.1.0"
            )
            included = [pr.number for pr in provider()]
        assert included == [2, 3]

    def test_last_tag_from_production_prefix(self):
        repo = _mock_repo()
        repo.list_tag_names.return_value = [
            "beta/1.2.0",
            "release/1.0.0",
            "release/1.1.0",
            "release/2.0.0",
        ]
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        provider = ADOChangeNotesProvider(generator, "release/1.2.0")
        assert provider._get_last_tag() == "release/1.1.0"

    def test_last_tag_for_beta_uses_previous_beta(self):
        repo = _mock_repo()
        repo.list_tag_names.return_value = [
            "corus-beta/1.0.0.4",
            "corus-beta/2.0.0.1",
            "corus-beta/2.0.0.2",
            "corus-beta/2.0.0.3",
            "release/1.0.0",
        ]
        info = _github_info()
        info["prefix_beta"] = "corus-beta/"
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=info,
            current_tag="corus-beta/2.0.0.3",
        )
        provider = ADOChangeNotesProvider(generator, "corus-beta/2.0.0.3")
        assert provider._get_last_tag() == "corus-beta/2.0.0.2"

    def test_beta_without_prior_beta_falls_back_to_production(self):
        repo = _mock_repo()
        repo.list_tag_names.return_value = [
            "corus-beta/2.0.0.3",
            "release/1.0.0",
        ]
        info = _github_info()
        info["prefix_beta"] = "corus-beta/"
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=info,
            current_tag="corus-beta/2.0.0.3",
        )
        provider = ADOChangeNotesProvider(generator, "corus-beta/2.0.0.3")
        assert provider._get_last_tag() == "release/1.0.0"

    def test_excludes_prs_merged_before_previous_beta(self):
        last = datetime(2025, 6, 1, tzinfo=UTC)
        current = datetime(2025, 9, 1, tzinfo=UTC)
        old = FakePullRequest(
            11467,
            merge_commit_sha="old",
            merged_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        in_range = FakePullRequest(
            92000,
            merge_commit_sha="mid",
            merged_at=datetime(2025, 7, 15, tzinfo=UTC),
        )
        repo = _mock_repo()
        repo.pull_requests.return_value = [old, in_range]
        info = _github_info()
        info["prefix_beta"] = "corus-beta/"
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=info,
            current_tag="corus-beta/2.0.0.3",
        )
        with patch(
            "cumulusci_ado.vcs.ado.release_notes.provider.get_tag_by_name"
        ) as mock_tag:
            mock_tag.side_effect = [
                SimpleNamespace(sha="lastsha"),
                SimpleNamespace(sha="cursha"),
            ]
            repo.get_commit.side_effect = [
                self._commit("lastsha", last),
                self._commit("cursha", current),
            ]
            provider = ADOChangeNotesProvider(
                generator, "corus-beta/2.0.0.3", last_tag="corus-beta/1.0.0.4"
            )
            included = [pr.number for pr in provider()]
        assert included == [92000]

    def test_expands_feature_sprint_merge_children(self):
        last = datetime(2025, 6, 1, tzinfo=UTC)
        current = datetime(2025, 9, 1, tzinfo=UTC)
        parent = FakePullRequest(
            80386,
            title="FY27Q1S3 : Merge feature/FY27Q1S3 into main",
            head_ref="feature/FY27Q1S3",
            merge_commit_sha="sprintmerge",
            merged_at=datetime(2025, 7, 15, tzinfo=UTC),
        )
        child = FakePullRequest(
            80001,
            title="HZPM-4362 work",
            head_ref="feature/FY27Q1S3__HZPM-4362",
            merge_commit_sha="childsha",
            merged_at=datetime(2025, 7, 1, tzinfo=UTC),
        )
        abandoned = FakePullRequest(
            80002,
            head_ref="feature/FY27Q1S3__HZCM-1",
            merged_at=None,
        )
        direct = FakePullRequest(
            87249,
            title="Fix trailing comma",
            head_ref="feature/fix-comma",
            merge_commit_sha="direct",
            merged_at=datetime(2025, 8, 1, tzinfo=UTC),
        )
        repo = _mock_repo()

        def pull_requests(**kwargs):
            base = kwargs.get("base")
            if base == "main":
                return [parent, direct]
            if base == "feature/FY27Q1S3":
                return [child, abandoned]
            return []

        repo.pull_requests.side_effect = lambda **kwargs: pull_requests(**kwargs)
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        with patch(
            "cumulusci_ado.vcs.ado.release_notes.provider.get_tag_by_name"
        ) as mock_tag:
            mock_tag.side_effect = [
                SimpleNamespace(sha="lastsha"),
                SimpleNamespace(sha="cursha"),
            ]
            repo.get_commit.side_effect = [
                self._commit("lastsha", last),
                self._commit("cursha", current),
            ]
            provider = ADOChangeNotesProvider(
                generator, "release/1.2.0", last_tag="release/1.1.0"
            )
            included = [pr.number for pr in provider()]
        assert included == [80386, 80001, 87249]

    def test_expands_release_nnn_sprint_merge_children(self):
        last = datetime(2025, 6, 1, tzinfo=UTC)
        current = datetime(2025, 9, 1, tzinfo=UTC)
        parent = FakePullRequest(
            10,
            head_ref="release/001",
            merge_commit_sha="sprintmerge",
            merged_at=datetime(2025, 7, 15, tzinfo=UTC),
        )
        child = FakePullRequest(
            11,
            head_ref="feature/FY27Q1S3__HZPM-1",
            merge_commit_sha="childsha",
            merged_at=datetime(2025, 7, 2, tzinfo=UTC),
        )
        repo = _mock_repo()

        def pull_requests(**kwargs):
            if kwargs.get("base") == "main":
                return [parent]
            if kwargs.get("base") == "release/001":
                return [child]
            return []

        repo.pull_requests.side_effect = lambda **kwargs: pull_requests(**kwargs)
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        with patch(
            "cumulusci_ado.vcs.ado.release_notes.provider.get_tag_by_name"
        ) as mock_tag:
            mock_tag.side_effect = [
                SimpleNamespace(sha="lastsha"),
                SimpleNamespace(sha="cursha"),
            ]
            repo.get_commit.side_effect = [
                self._commit("lastsha", last),
                self._commit("cursha", current),
            ]
            provider = ADOChangeNotesProvider(
                generator, "release/1.2.0", last_tag="release/1.1.0"
            )
            included = [pr.number for pr in provider()]
        assert included == [10, 11]

    def test_does_not_expand_jira_feature_branch_merged_to_main(self):
        last = datetime(2025, 6, 1, tzinfo=UTC)
        current = datetime(2025, 9, 1, tzinfo=UTC)
        leaf = FakePullRequest(
            12,
            head_ref="feature/FY27Q1S3__HZPM-4362",
            merge_commit_sha="leaf",
            merged_at=datetime(2025, 7, 15, tzinfo=UTC),
        )
        repo = _mock_repo()
        repo.pull_requests.return_value = [leaf]
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        with patch(
            "cumulusci_ado.vcs.ado.release_notes.provider.get_tag_by_name"
        ) as mock_tag:
            mock_tag.side_effect = [
                SimpleNamespace(sha="lastsha"),
                SimpleNamespace(sha="cursha"),
            ]
            repo.get_commit.side_effect = [
                self._commit("lastsha", last),
                self._commit("cursha", current),
            ]
            provider = ADOChangeNotesProvider(
                generator, "release/1.2.0", last_tag="release/1.1.0"
            )
            included = [pr.number for pr in provider()]
        assert included == [12]
        assert repo.pull_requests.call_count == 1

    def test_identifies_sprint_and_release_parents_from_project_config(self):
        repo = _mock_repo()
        generator = SimpleNamespace(
            get_repo=lambda: repo,
            github_info=_github_info(),
            current_tag="release/1.2.0",
        )
        provider = ADOChangeNotesProvider(
            generator, "release/1.2.0", last_tag="release/1.1.0"
        )
        assert provider._is_sprint_merge_branch("feature/FY27Q1S3") is True
        assert provider._is_sprint_merge_branch("release/001") is True
        assert provider._is_sprint_merge_branch("feature/FY27Q1S3__HZPM-4362") is False
        assert provider._is_sprint_merge_branch("feature/some-work") is False
        repo.project_config.get_release_branch_prefix_and_format_config.assert_called()


class TestADOReleaseNotesGenerator:
    def _generator(
        self, publish=False, include_empty=False, parsers=None, last_tag=None
    ):
        repo = _mock_repo()
        generator = ADOReleaseNotesGenerator(
            repo,
            _github_info(),
            parsers
            or [
                {
                    "class_path": "cumulusci_ado.vcs.ado.release_notes.parser.ADOLinesParser",
                    "title": "Changes",
                }
            ],
            "beta/1.2.0",
            last_tag=last_tag,
            publish=publish,
            include_empty=include_empty,
        )
        return generator, repo

    def test_appends_artifact_link(self):
        generator, repo = self._generator()
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            content = generator()
        assert "# Artifact" in content
        assert "sfcore-p1-base" in content
        assert repo.artifact_ui_url.return_value in content

    def test_omits_artifact_when_version_missing(self):
        generator, repo = self._generator()
        repo.get_version_package.return_value = (None, None)
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            content = generator()
        assert "# Artifact" not in content

    def test_appends_files_compare_link(self):
        generator, repo = self._generator(last_tag="beta/1.1.0")
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            content = generator()
        assert "# Files" in content
        assert "[Changed Files](" in content
        assert repo.compare_files_url.return_value in content
        repo.compare_files_url.assert_called_once_with("beta/1.1.0", "beta/1.2.0")

    def test_omits_files_when_no_last_tag(self):
        generator, _repo = self._generator()
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            content = generator()
        assert "# Files" not in content

    def test_publish_opens_pull_request_and_json_path(self):
        generator, repo = self._generator(publish=True)
        pr = MagicMock(number=44)
        pr.can_auto_merge.return_value = True
        repo.create_pull.return_value = pr
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            generator()
        repo.create_branch.assert_called_once_with(
            "cci/release-notes/beta/1.2.0", source_branch="main"
        )
        repo.push_file.assert_called_once()
        args, kwargs = repo.push_file.call_args
        assert args[0] == "release-notes/beta/1.2.0.md"
        assert kwargs["branch"] == "cci/release-notes/beta/1.2.0"
        repo.create_pull.assert_called_once()
        pr.merge.assert_called_once()
        repo.merge_package_description_fields.assert_called_once_with(
            "beta/1.2.0", {"release_notes_path": "release-notes/beta/1.2.0.md"}
        )

    def test_does_not_publish_when_flag_false(self):
        generator, repo = self._generator(publish=False)
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            generator()
        repo.push_file.assert_not_called()
        repo.create_pull.assert_not_called()

    def test_publish_file_writes_local_markdown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generator, repo = self._generator(publish=True)
        repo.config.side_effect = lambda key: (
            "file" if key == "release_notes_publish" else None
        )
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            generator()
        written = tmp_path / "release-notes" / "beta" / "1.2.0.md"
        assert written.is_file()
        assert "# Artifact" in written.read_text()
        repo.push_file.assert_not_called()
        repo.create_pull.assert_not_called()

    def test_publish_repository_pushes_to_configured_repo(self):
        generator, repo = self._generator(publish=True)
        other = MagicMock()
        other.default_branch = "docs"
        service = MagicMock()
        service.get_repository.return_value = other

        def config(key):
            return {
                "release_notes_publish": "repository",
                "release_notes_repository": "https://dev.azure.com/org/proj/_git/notes",
                "release_notes_branch": "changelog",
            }.get(key)

        repo.config.side_effect = config
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            with patch(
                "cumulusci_ado.vcs.ado.service.get_ado_service_for_url",
                return_value=service,
            ):
                generator()
        other.push_file.assert_called_once()
        args, kwargs = other.push_file.call_args
        assert args[0] == "release-notes/beta/1.2.0.md"
        assert kwargs["branch"] == "changelog"
        assert kwargs["create_branch_from"] == "docs"
        repo.push_file.assert_not_called()
        repo.create_pull.assert_not_called()

    def test_publish_list_runs_file_then_pull_request(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        generator, repo = self._generator(publish=True)
        pr = MagicMock(number=8)
        pr.can_auto_merge.return_value = True
        repo.create_pull.return_value = pr
        repo.config.side_effect = lambda key: (
            ["file", "pull_request"] if key == "release_notes_publish" else None
        )
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([])):
            generator()
        assert (tmp_path / "release-notes" / "beta" / "1.2.0.md").is_file()
        repo.create_pull.assert_called_once()

    def test_passes_pull_request_objects_to_parsers(self):
        seen = []

        class RecordingParser:
            def __init__(self, generator, title):
                self.title = title
                self.content = []

            def parse(self, pull_request):
                seen.append(pull_request)
                return True

            def render(self):
                return "# Changes\r\n\r\none"

        generator, _repo = self._generator()
        generator.parsers = [RecordingParser(generator, "Changes")]
        pr = FakePullRequest(9, title="A change")
        with patch.object(ADOChangeNotesProvider, "__call__", return_value=iter([pr])):
            generator()
        assert seen == [pr]
