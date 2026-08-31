from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import responses

# Import Azure DevOps SDK classes
from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsClientError, AzureDevOpsServiceError
from azure.devops.v7_0.git import models
from cumulusci.core.config.project_config import BaseProjectConfig
from msrest import Deserializer
from msrest.authentication import BasicAuthentication

# Import classes from your adapter.py
from cumulusci_ado.vcs.ado.adapter import (
    ADOBranch,
    ADOComparison,
    ADOPullRequest,
    ADORef,
    ADORepository,
    ADOTag,
)

# parse_repo_url will be patched

# --- Constants for Test Data ---
TEST_ADO_ORG = "TestOrg"
TEST_ADO_PROJECT_NAME = "TestProject"
TEST_ADO_REPO_NAME = "TestRepo"
TEST_ADO_REPO_ID = "repo-id-12345"
TEST_ADO_PROJECT_ID = "project-id-67890"
TEST_ADO_BASE_URL = f"https://dev.azure.com/{TEST_ADO_ORG}"
TEST_ADO_API_BASE_URL = f"{TEST_ADO_BASE_URL}/{TEST_ADO_PROJECT_NAME}/_apis/git"
API_VERSION = "api-version=7.0"

TEST_ADO_REPO_URL_CONFIG = f"https://dev.azure.com/{TEST_ADO_ORG}/{TEST_ADO_PROJECT_NAME}/_git/{TEST_ADO_REPO_NAME}"
TEST_DEFAULT_BRANCH = "main"
TEST_FEATURE_BRANCH = "feature/test-branch"
TEST_TARGET_BRANCH = "develop"
TEST_COMMIT_SHA_BASE = "basecommitsha123"
TEST_COMMIT_SHA_HEAD = "headcommitsha456"
TEST_TAG_NAME = "v1.0.0"
TEST_TAG_OBJECT_SHA = "tagobjsha789"  # SHA of the tag object itself
TEST_PEELED_COMMIT_SHA = "peeledtagsha012"  # SHA the annotated tag points to (commit)
TEST_PR_ID = 101
TEST_USER_ID = "user-id-abc"
TEST_PAT = "dummy_pat_for_testing"

client_models = {k: v for k, v in models.__dict__.items() if isinstance(v, type)}
deserialize = Deserializer(client_models)


def get_mock_identity_ref_json(user_id=TEST_USER_ID, display_name="Test User"):
    return {
        "id": user_id,
        "displayName": display_name,
        "uniqueName": f"{display_name.lower().replace(' ', '')}@example.com",
        "url": f"{TEST_ADO_BASE_URL}/_apis/Identities/{user_id}",
        "imageUrl": f"{TEST_ADO_BASE_URL}/_apis/GraphProfile/MemberAvatars/{user_id}",
    }


def get_mock_project_ref_json():
    return {
        "id": TEST_ADO_PROJECT_ID,
        "name": TEST_ADO_PROJECT_NAME,
        "url": f"{TEST_ADO_BASE_URL}/_apis/projects/{TEST_ADO_PROJECT_ID}",
        "state": "wellFormed",
        "revision": 1,
        "visibility": "private",
        "lastUpdateTime": "2023-01-01T00:00:00Z",
    }


def get_mock_repo_json():
    return {
        "id": TEST_ADO_REPO_ID,
        "name": TEST_ADO_REPO_NAME,
        "url": f"{TEST_ADO_API_BASE_URL}/repositories/{TEST_ADO_REPO_ID}",
        "project": get_mock_project_ref_json(),
        "defaultBranch": f"refs/heads/{TEST_DEFAULT_BRANCH}",
        "size": 12345,
        "remoteUrl": TEST_ADO_REPO_URL_CONFIG,
        "sshUrl": f"git@ssh.dev.azure.com:v3/{TEST_ADO_ORG}/{TEST_ADO_PROJECT_NAME}/{TEST_ADO_REPO_NAME}",
        "webUrl": f"{TEST_ADO_BASE_URL}/{TEST_ADO_PROJECT_NAME}/_git/{TEST_ADO_REPO_NAME}",
        "isDisabled": False,
    }


def get_mock_git_ref_json(name, object_id, peeled_object_id=None):
    ref_data = {
        "name": name,
        "objectId": object_id,
        "url": f"{TEST_ADO_API_BASE_URL}/repositories/{TEST_ADO_REPO_ID}/refs?filter={name.replace('/', '%2F')}",
    }
    if peeled_object_id:
        ref_data["peeledObjectId"] = peeled_object_id
    return ref_data


def get_mock_annotated_tag_json(
    name, object_id, message="Test Tag", tagged_object_sha=TEST_PEELED_COMMIT_SHA
):
    return {
        "name": name,
        "objectId": object_id,  # SHA of the tag object
        "message": message,
        "taggedObject": {"objectId": tagged_object_sha, "type": "commit"},
        "url": f"{TEST_ADO_API_BASE_URL}/repositories/{TEST_ADO_REPO_ID}/annotatedtags/{object_id}",
    }


def get_mock_pr_json(
    pr_id, title, source_ref, target_ref, status="active", merge_status="queued"
):
    return {
        "pull_request_id": pr_id,
        "repository": get_mock_repo_json(),
        "codeReviewId": pr_id + 1000,
        "status": status,
        "created_by": get_mock_identity_ref_json(),
        "creation_date": "2023-01-01T10:00:00Z",
        "title": title,
        "description": "PR Description",
        "source_ref_name": source_ref,
        "target_ref_name": target_ref,
        "merge_status": merge_status,
        "is_draft": False,
        "last_merge_source_commit": {"commit_id": TEST_COMMIT_SHA_HEAD},
        "url": f"{TEST_ADO_API_BASE_URL}/repositories/{TEST_ADO_REPO_ID}/pullrequests/{pr_id}",
    }


def make_ado_service_error(message: str) -> AzureDevOpsServiceError:
    wrapped = MagicMock()
    wrapped.inner_exception = None
    wrapped.message = message
    wrapped.exception_id = None
    wrapped.type_name = None
    wrapped.type_key = None
    wrapped.error_code = None
    wrapped.event_id = None
    wrapped.custom_properties = None
    return AzureDevOpsServiceError(wrapped)


def get_mock_commit_diffs_json(behind_count=0, ahead_count=0, changes=None):
    if changes is None:
        changes = [{"item": {"path": "/file.txt"}, "changeType": "edit"}]
    return {
        "behindCount": behind_count,
        "aheadCount": ahead_count,
        "changes": changes,
    }


def get_mock_branch_json(name, commit_sha):
    return {
        "name": name,
        "commit": get_mock_git_ref_json(name, commit_sha),
    }


# --- Fixtures ---
@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def mock_project_config(mock_logger):
    config = MagicMock(spec=BaseProjectConfig)
    config.repo_url = TEST_ADO_REPO_URL_CONFIG  # Used by parse_repo_url
    config.logger = mock_logger
    # Options
    config.project__git__prefix_release = "release/"
    config.project__git__prefix_beta = "beta/"
    config.project__git__prefix_feature = "feature/"
    config.project__git__default_branch = TEST_DEFAULT_BRANCH
    return config


@pytest.fixture
def ado_connection():
    ado_connection = Connection(
        base_url=TEST_ADO_BASE_URL, creds=BasicAuthentication("", TEST_PAT)
    )
    ado_connection.clients.get_git_client = MagicMock()
    git_client = MagicMock()
    git_client.get_repository.return_value = deserialize(
        "GitRepository", get_mock_repo_json()
    )

    ado_connection.clients.get_git_client.return_value = git_client
    return ado_connection


@pytest.fixture
@patch("cumulusci_ado.vcs.ado.adapter.parse_repo_url")  # Patching the utility function
def ado_repository_instance(mock_parse_repo_url, ado_connection, mock_project_config):
    # Configure the mock for parse_repo_url
    mock_parse_repo_url.return_value = (
        TEST_ADO_ORG,
        TEST_ADO_REPO_NAME,
        f"{TEST_ADO_ORG}/{TEST_ADO_PROJECT_NAME}",  # project_identifier (used as 'project' param in SDK calls)
        TEST_ADO_PROJECT_NAME,
    )

    repo_options = {
        "retry_timeout": 0.01,
        "retry_interval": 0.005,
        "create_pull_request_on_conflict": True,
        "completion_opts_delete_source_branch": False,
        "completion_opts_merge_strategy": "squash",
        "completion_opts_bypass_policy": False,
        "completion_opts_bypass_reason": "Automated bypass for CI/CD pipeline",
    }
    instance = ADORepository(
        connection=ado_connection, config=mock_project_config, **repo_options
    )
    instance._init_repo()
    instance.logger = mock_project_config.logger
    return instance


# --- Test Classes ---
class TestADORepositoryInitAndProperties:
    @responses.activate
    def test_successful_initialization(self, ado_connection, mock_project_config):

        with patch("cumulusci_ado.vcs.ado.adapter.parse_repo_url") as mock_parse:
            mock_parse.return_value = (
                TEST_ADO_ORG,
                TEST_ADO_REPO_NAME,
                TEST_ADO_PROJECT_NAME,
                TEST_ADO_PROJECT_NAME,
            )

            instance = ADORepository(
                connection=ado_connection, config=mock_project_config
            )
            instance._init_repo()

        # --- Assertions ---
        assert instance.repo is not None
        assert instance.repo.id == TEST_ADO_REPO_ID  # From get_mock_repo_json

        if mock_project_config.repo_url:
            mock_parse.assert_called_once_with(mock_project_config.repo_url)

    @patch("cumulusci_ado.vcs.ado.adapter.parse_repo_url")
    def test_initialization_no_repo_url(
        self, mock_parse_repo_url, mock_project_config, ado_connection
    ):
        type(mock_project_config).repo_url = PropertyMock(return_value=None)

        instance = ADORepository(connection=ado_connection, config=mock_project_config)

        assert instance.repo is None
        assert instance.project is None
        mock_parse_repo_url.assert_not_called()  # Should not be called if repo_url is None

        with pytest.raises(ValueError, match="Repository is not set."):
            _ = instance.id
        with pytest.raises(ValueError, match="Project is not set."):
            _ = instance.project_id


class TestADORepositoryRefsAndTags:
    @responses.activate
    def test_get_ref_for_tag_success(self, ado_repository_instance: ADORepository):
        ado_repository_instance.git_client.get_refs = (
            MagicMock()
        )  # Mock the SDK call to get_refs

        tag_ref_json = get_mock_git_ref_json(
            f"refs/tags/{TEST_TAG_NAME}", TEST_TAG_OBJECT_SHA, TEST_PEELED_COMMIT_SHA
        )

        ado_repository_instance.git_client.get_refs.return_value = [
            deserialize("GitRef", tag_ref_json)
        ]

        ref = ado_repository_instance.get_ref_for_tag(TEST_TAG_NAME)
        assert isinstance(ref, ADORef)
        assert ref.sha == TEST_TAG_OBJECT_SHA
        assert ref.ref.peeled_object_id == TEST_PEELED_COMMIT_SHA

    @responses.activate
    def test_get_ref_for_tag_not_annotated(
        self, ado_repository_instance: ADORepository
    ):
        ado_repository_instance.git_client.get_refs = (
            MagicMock()
        )  # Mock the SDK call to get_refs

        tag_ref_json = get_mock_git_ref_json(
            f"refs/tags/{TEST_TAG_NAME}", TEST_TAG_OBJECT_SHA, None
        )

        ado_repository_instance.git_client.get_refs.return_value = [
            deserialize("GitRef", tag_ref_json)
        ]

        with pytest.raises(AzureDevOpsClientError, match="is not an annotated tag"):
            ado_repository_instance.get_ref_for_tag(TEST_TAG_NAME)

    @responses.activate
    def test_get_tag_by_ref_success(self, ado_repository_instance: ADORepository):
        ado_repository_instance.git_client.get_annotated_tag = MagicMock()
        ado_repository_instance.git_client.get_annotated_tag.return_value = deserialize(
            "GitAnnotatedTag",
            get_mock_annotated_tag_json(
                TEST_TAG_NAME,
                TEST_TAG_OBJECT_SHA,
                tagged_object_sha=TEST_PEELED_COMMIT_SHA,
            ),
        )

        # Prepare a mock ADORef object
        mock_sdk_ref = MagicMock()
        mock_sdk_ref.object_id = (
            TEST_TAG_OBJECT_SHA  # This is what ADORef's sha will be
        )
        ado_ref_instance = ADORef(ref=mock_sdk_ref)

        tag = ado_repository_instance.get_tag_by_ref(ado_ref_instance, TEST_TAG_NAME)
        assert isinstance(tag, ADOTag)
        assert tag.sha == TEST_TAG_OBJECT_SHA
        assert tag.tag.name == TEST_TAG_NAME

    @responses.activate
    def test_create_tag_success(self, ado_repository_instance: ADORepository):
        ado_repository_instance.git_client.create_annotated_tag = MagicMock()

        tag_name_to_create = "release/2.0"
        message = "New release tag"
        commit_to_tag_sha = TEST_COMMIT_SHA_HEAD

        ado_repository_instance.git_client.create_annotated_tag.return_value = (
            deserialize(
                "GitAnnotatedTag",
                get_mock_annotated_tag_json(
                    tag_name_to_create,
                    "newly_created_tag_object_sha",
                    message,
                    commit_to_tag_sha,
                ),
            )
        )

        tag = ado_repository_instance.create_tag(
            tag_name_to_create, message, commit_to_tag_sha, "commit"
        )
        assert isinstance(tag, ADOTag)
        assert tag.tag.name == tag_name_to_create
        assert tag.sha == "newly_created_tag_object_sha"


class TestADORepositoryBranchesAndComparison:
    @responses.activate
    def test_branches_method(self, ado_repository_instance: ADORepository):
        branch_ref_json = get_mock_git_ref_json(
            f"refs/heads/{TEST_FEATURE_BRANCH}", TEST_COMMIT_SHA_HEAD
        )
        ado_repository_instance.git_client.get_branches = MagicMock()
        ado_repository_instance.git_client.get_branches.return_value = [
            deserialize("GitRef", branch_ref_json)
        ]

        branches = ado_repository_instance.branches()  # This calls ADOBranch.branches
        assert len(branches) == 1
        assert branches[0].name == f"refs/heads/{TEST_FEATURE_BRANCH}"

    @responses.activate
    def test_compare_commits_method(self, ado_repository_instance: ADORepository):
        ado_repository_instance.git_client.get_commit_diffs = MagicMock()
        ado_repository_instance.git_client.get_commit_diffs.return_value = deserialize(
            "GitCommitDiffs", get_mock_commit_diffs_json(behind_count=3)
        )

        comparison = ado_repository_instance.compare_commits(
            TEST_COMMIT_SHA_BASE, TEST_COMMIT_SHA_HEAD
        )
        assert isinstance(comparison, ADOComparison)
        assert comparison.behind_by == 3


class TestADORepositoryPullRequestsAndMerge:
    @responses.activate
    def test_create_pull_delegation_and_actual_call(
        self, ado_repository_instance: ADORepository
    ):
        title = "My Great PR"
        pr_id_created = TEST_PR_ID + 5

        ado_repository_instance.git_client.create_pull_request = MagicMock()
        ado_repository_instance.git_client.create_pull_request.return_value = (
            deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    pr_id_created,
                    title,
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                ),
            )
        )

        ado_pr = ado_repository_instance.create_pull(
            title=title,
            base=TEST_FEATURE_BRANCH,  # sourceRefName
            head=TEST_TARGET_BRANCH,  # targetRefName
            body="PR body content",
        )
        assert isinstance(ado_pr, ADOPullRequest)
        assert ado_pr.pull_request.title == title

    @responses.activate
    @patch("time.sleep", MagicMock())  # Mock time.sleep
    def test_merge_automerge_success(
        self, ado_repository_instance: ADORepository, mock_logger
    ):
        pr_id_for_merge = TEST_PR_ID + 10
        title = f"Automerge {TEST_FEATURE_BRANCH} into {TEST_TARGET_BRANCH}"

        ado_repository_instance.git_client.create_pull_request = MagicMock()
        ado_repository_instance.git_client.create_pull_request.return_value = (
            deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    pr_id_for_merge,
                    title,
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    merge_status="queued",
                ),
            )
        )

        ado_repository_instance.git_client.get_pull_request = MagicMock()
        ado_repository_instance.git_client.get_pull_request.return_value = deserialize(
            "GitPullRequest",
            get_mock_pr_json(
                pr_id_for_merge,
                title,
                f"refs/heads/{TEST_FEATURE_BRANCH}",
                f"refs/heads/{TEST_TARGET_BRANCH}",
                merge_status="succeeded",
            ),
        )

        ado_repository_instance.git_client.update_pull_request = MagicMock()
        ado_repository_instance.git_client.update_pull_request.return_value = (
            deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    pr_id_for_merge,
                    title,
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    merge_status="queued",
                ),
            )
        )

        ado_pr_instance = ado_repository_instance.merge(
            base=TEST_FEATURE_BRANCH, head=TEST_TARGET_BRANCH, message="Auto merge"
        )

        assert ado_pr_instance is not None


class TestADOPullRequestCompleteOverride:
    def _build_pr(self, ado_repository_instance, status="active"):
        pull_request = deserialize(
            "GitPullRequest",
            get_mock_pr_json(
                TEST_PR_ID,
                "Complete with override",
                f"refs/heads/{TEST_FEATURE_BRANCH}",
                f"refs/heads/{TEST_TARGET_BRANCH}",
                status=status,
                merge_status="succeeded",
            ),
        )
        return ADOPullRequest(
            repo=ado_repository_instance,
            pull_request=pull_request,
            options=ado_repository_instance.options,
        )

    def test_complete_pull_request_with_override_success(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance)
        ado_repository_instance.git_client.update_pull_request = MagicMock()
        ado_repository_instance.git_client.update_pull_request.return_value = (
            deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    TEST_PR_ID,
                    "Complete with override",
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    status="completed",
                    merge_status="succeeded",
                ),
            )
        )

        result = ado_pr.complete_pull_request_with_override()

        assert result is True
        assert ado_pr.pull_request.status == "completed"
        update = (
            ado_repository_instance.git_client.update_pull_request.call_args.kwargs[
                "git_pull_request_to_update"
            ]
        )
        assert update.status == "completed"
        assert update.auto_complete_set_by is None
        assert update.completion_options.bypass_policy is True
        ado_repository_instance.logger.info.assert_called()

    def test_complete_pull_request_with_override_permission_denied_warns(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance)
        ado_repository_instance.git_client.update_pull_request = MagicMock(
            side_effect=make_ado_service_error(
                "TF401027: You need the Git 'Bypass policies when completing "
                "pull requests' permission to perform this action on the resource."
            )
        )

        result = ado_pr.complete_pull_request_with_override()

        assert result is False
        assert ado_pr.pull_request.status == "active"
        ado_repository_instance.logger.warning.assert_called()
        warning = ado_repository_instance.logger.warning.call_args.args[0]
        assert "does not have permission to bypass policies" in warning

    def test_complete_pull_request_with_override_other_error_raises(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance)
        ado_repository_instance.git_client.update_pull_request = MagicMock(
            side_effect=make_ado_service_error(
                "Invalid argument value. Parameter name: The bypass option "
                "cannot be used with auto-complete."
            )
        )

        with pytest.raises(AzureDevOpsServiceError, match="Failed to complete"):
            ado_pr.complete_pull_request_with_override()

        ado_repository_instance.logger.warning.assert_not_called()

    def test_merge_skips_auto_complete_when_already_completed(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance, status="completed")
        ado_repository_instance.git_client.update_pull_request = MagicMock()

        ado_pr.merge()

        ado_repository_instance.git_client.update_pull_request.assert_not_called()
        ado_repository_instance.logger.info.assert_called()

    def test_merge_treats_tf401181_as_success_when_pr_already_completed(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance, status="active")
        ado_repository_instance.git_client.update_pull_request = MagicMock(
            side_effect=make_ado_service_error(
                "TF401181: The pull request cannot be edited due to its state."
            )
        )
        ado_repository_instance.git_client.get_pull_request = MagicMock(
            return_value=deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    TEST_PR_ID,
                    "Complete with override",
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    status="completed",
                    merge_status="succeeded",
                ),
            )
        )

        ado_pr.merge()

        ado_repository_instance.git_client.update_pull_request.assert_called_once()
        assert ado_pr.pull_request.status == "completed"
        ado_repository_instance.logger.info.assert_called()

    def test_merge_treats_tf401181_as_success_when_pr_still_active(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance, status="active")
        ado_repository_instance.git_client.update_pull_request = MagicMock(
            side_effect=make_ado_service_error(
                "TF401181: The pull request cannot be edited due to its state."
            )
        )
        ado_repository_instance.git_client.get_pull_request = MagicMock(
            return_value=deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    TEST_PR_ID,
                    "Complete with override",
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    status="active",
                    merge_status="succeeded",
                ),
            )
        )

        ado_pr.merge()

        ado_repository_instance.git_client.update_pull_request.assert_called_once()
        ado_repository_instance.logger.info.assert_called()

    def test_merge_skips_auto_complete_after_override_completion(
        self, ado_repository_instance: ADORepository
    ):
        ado_pr = self._build_pr(ado_repository_instance, status="active")
        ado_repository_instance.git_client.update_pull_request = MagicMock(
            return_value=deserialize(
                "GitPullRequest",
                get_mock_pr_json(
                    TEST_PR_ID,
                    "Complete with override",
                    f"refs/heads/{TEST_FEATURE_BRANCH}",
                    f"refs/heads/{TEST_TARGET_BRANCH}",
                    status="active",
                    merge_status="succeeded",
                ),
            )
        )

        result = ado_pr.complete_pull_request_with_override()
        ado_pr.merge()

        assert result is True
        assert ado_pr._completed_with_override is True
        assert ado_repository_instance.git_client.update_pull_request.call_count == 1


class TestADORef:
    def test_init(self):
        mock_sdk_ref = MagicMock()
        mock_sdk_ref.object_id = TEST_TAG_OBJECT_SHA

        ado_ref = ADORef(ref=mock_sdk_ref)
        assert ado_ref.ref is mock_sdk_ref
        assert ado_ref.sha == TEST_TAG_OBJECT_SHA
        assert ado_ref.type == "tag"


class TestADOTag:
    def test_init(self):
        mock_sdk_annotated_tag = MagicMock()
        mock_sdk_annotated_tag.object_id = (
            TEST_PEELED_COMMIT_SHA  # ADOTag.sha is the commit SHA
        )
        mock_sdk_annotated_tag.name = TEST_TAG_NAME

        ado_tag = ADOTag(tag=mock_sdk_annotated_tag)
        assert ado_tag.tag is mock_sdk_annotated_tag
        assert ado_tag.sha == TEST_PEELED_COMMIT_SHA


class TestADOBranch:  # ADOBranch methods also make SDK calls
    @responses.activate
    def test_get_branch_success(self, ado_repository_instance: ADORepository):
        branch_name_to_get = "myfeature"
        commit_sha_for_branch = "featurecommit123"
        ado_repository_instance.git_client.get_branch = MagicMock()
        ado_repository_instance.git_client.get_branch.return_value = deserialize(
            "GitBranchStats",
            get_mock_branch_json(
                f"refs/heads/{branch_name_to_get}", commit_sha_for_branch
            ),
        )

        ado_branch = ADOBranch(
            repo=ado_repository_instance, branch_name=branch_name_to_get
        )
        ado_branch.get_branch()  # Act

        assert ado_branch.branch is not None
        assert ado_branch.branch.name == f"refs/heads/{branch_name_to_get}"
