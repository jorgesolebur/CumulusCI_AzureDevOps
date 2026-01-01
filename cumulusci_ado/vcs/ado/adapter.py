import json
import os
import time
from datetime import UTC, datetime
from io import BytesIO, StringIO
from re import Pattern
from typing import Iterator, Optional, Tuple, Union

from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsClientError, AzureDevOpsServiceError
from azure.devops.v7_0.feed.feed_client import FeedClient
from azure.devops.v7_0.feed.models import Feed, FeedView, Package, PackageVersion
from azure.devops.v7_0.git.git_client import GitClient
from azure.devops.v7_0.git.models import (
    GitAnnotatedTag,
    GitBaseVersionDescriptor,
    GitBranchStats,
    GitCommit,
    GitCommitDiffs,
    GitObject,
    GitPullRequest,
    GitPullRequestCompletionOptions,
    GitPullRequestQuery,
    GitPullRequestQueryInput,
    GitPullRequestSearchCriteria,
    GitRef,
    GitRepository,
    GitStatus,
    GitStatusContext,
    GitTargetVersionDescriptor,
    GitVersionDescriptor,
    TeamProjectReference,
)
from azure.devops.v7_0.upack_api.models import JsonPatchOperation, PackageVersionDetails
from azure.devops.v7_0.upack_api.upack_api_client import UPackApiClient
from cumulusci.core.config.project_config import BaseProjectConfig
from cumulusci.core.config.util import get_devhub_config
from cumulusci.salesforce_api.utils import get_simple_salesforce_connection
from cumulusci.vcs.models import (
    AbstractBranch,
    AbstractComparison,
    AbstractGitTag,
    AbstractPullRequest,
    AbstractRef,
    AbstractRelease,
    AbstractRepo,
    AbstractRepoCommit,
)

from cumulusci_ado.utils.ado import (
    custom_to_semver,
    parse_repo_url,
    publish_package,
    sanitize_path_name,
)
from cumulusci_ado.vcs.ado.exceptions import ADOApiNotFoundError

RELEASE = "Release"
PRERELEASE = "Prerelease"


class ADORef(AbstractRef):
    ref: GitRef
    sha: Optional[str]
    type: Optional[str]

    def __init__(self, ref: GitRef, **kwargs) -> None:
        super().__init__(ref, **kwargs)
        self.sha = kwargs.get("sha") or self.ref.object_id
        self.type = "tag"


class ADOTag(AbstractGitTag):
    tag: GitAnnotatedTag

    @property
    def message(self) -> str:
        """Gets the message of the tag."""
        return self.tag.message or "" if self.tag else ""

    @property
    def sha(self) -> str:
        """Gets the SHA of the tag."""
        return (
            self.tag.tagged_object.object_id or ""
            if self.tag and self.tag.tagged_object
            else ""
        )


class ADOComparison(AbstractComparison):
    repo: "ADORepository"
    commit_diffs: GitCommitDiffs

    def get_comparison(self) -> None:
        """Gets the comparison object for the current base and head."""
        if self.base == self.head:
            self.commit_diffs = GitCommitDiffs(
                changes=[],
                ahead_count=0,
                behind_count=0,
            )
            return

        base_version_descriptor = GitBaseVersionDescriptor(
            self.base,
            version_type="commit",
            base_version=self.base,
            base_version_type="commit",
        )

        target_version_descriptor: GitTargetVersionDescriptor = (
            GitTargetVersionDescriptor(
                self.head,
                version_type="commit",
                target_version=self.head,
                target_version_type="commit",
            )
        )

        try:
            self.commit_diffs = self.repo.git_client.get_commit_diffs(
                self.repo.id,
                self.repo.project_id,
                top=10,
                base_version_descriptor=base_version_descriptor,
                target_version_descriptor=target_version_descriptor,
            )

            return
        except AzureDevOpsServiceError as e:
            e.message = f"Failed to get commit diffs: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during getting commit diffs: {str(ex)}"
            raise Exception(message)

    @property
    def files(self) -> list:
        return self.commit_diffs.changes or []

    @property
    def behind_by(self) -> int:
        """Returns the number of commits the head is behind the base."""
        return self.commit_diffs.behind_count or 0

    @classmethod
    def compare(cls, repo: AbstractRepo, base: str, head: str) -> "ADOComparison":
        comparison = ADOComparison(repo, base, head)
        comparison.get_comparison()
        return comparison


class ADOCommit(AbstractRepoCommit):
    """ADO commit object for representing a commit in the repository."""

    commit: GitCommit
    _sha: str

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sha = (
            self.commit.commit_id or "" if self.commit else kwargs.get("sha", "")
        )
        self.git_client: Optional[GitClient] = kwargs.get("git_client")
        self.repo_id = kwargs.get("repo_id")
        self.project_id = kwargs.get("project_id")

    def get_statuses(self, context: str, regex_match: Pattern[str]) -> Optional[str]:
        """
        Returns the first regex group from the description of a successful status with the given context name.
        """
        if (
            not self.sha
            or self.git_client is None
            or self.repo_id is None
            or self.project_id is None
        ):
            return None

        statuses = self.git_client.get_statuses(
            self.sha, self.repo_id, self.project_id, latest_only=True
        )

        for status in statuses:
            if (
                status.state == "succeeded"
                and getattr(status.context, "name", None) == context
            ):
                search_match = regex_match.search(status.description)
                if search_match:
                    return search_match.group(1)
        return None

    @property
    def tree_id(self) -> str:
        """Gets the tree ID of the commit."""
        return self.commit.tree_id or "" if self.commit else ""

    @property
    def parents(self) -> list["ADOCommit"]:
        # TODO: Test this method
        return [ADOCommit(sha=c) for c in self.commit.parents or []]

    @property
    def sha(self) -> str:
        """Gets the SHA of the commit."""
        return self._sha


class ADOBranch(AbstractBranch):
    repo: "ADORepository"
    branch: Optional[GitBranchStats]

    def __init__(self, repo: "ADORepository", branch_name: str, **kwargs) -> None:
        branch_name = sanitize_path_name(branch_name)
        super().__init__(repo, branch_name, **kwargs)

    def get_branch(self) -> None:
        try:
            self.branch = self.repo.git_client.get_branch(
                self.repo.id, self.name, self.repo.project_id
            )
        except AzureDevOpsServiceError as e:
            e.message = f"Branch {self.name} not found. {e.message}"
            raise AzureDevOpsServiceError(e)

    @classmethod
    def branches(cls, ado_repo: "ADORepository") -> list["ADOBranch"]:
        """Fetches all branches from the given repository"""
        source_branch: Optional[str] = ado_repo.options.get("source_branch", None)

        try:
            if ado_repo.repo is None:
                raise ValueError("Repository is not set. Cannot access its branches.")

            base_version_descriptor = GitVersionDescriptor(
                sanitize_path_name(source_branch or ""), "none", "branch"
            )
            branches: list[GitBranchStats] = ado_repo.git_client.get_branches(
                ado_repo.repo.id, ado_repo.project_id, base_version_descriptor
            )
            return [
                ADOBranch(
                    repo=ado_repo,
                    branch_name=(branch.name if branch.name else ""),
                    branch=branch,
                )
                for branch in branches
            ]
        except AzureDevOpsServiceError as e:
            e.message = f"Failed to get branches: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error when getting branches: {str(ex)}"
            raise Exception(message)

    @property
    def commit_id(self) -> str:
        """Gets the commit ID of the branch."""
        return (
            self.branch.commit.commit_id or ""
            if self.branch and self.branch.commit
            else ""
        )

    @property
    def commit(self) -> Optional[ADOCommit]:
        """Gets the branch commit for the current branch."""
        if self.branch is None:
            self.get_branch()

        return ADOCommit(commit=self.branch.commit) if self.branch else None


class ADOPullRequest(AbstractPullRequest):
    """ADO pull request object for creating and managing pull requests."""

    pull_request: GitPullRequest
    repo: "ADORepository"
    options: dict

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.options = kwargs.get("options", {})

    @classmethod
    def pull_requests(
        cls,
        repo: "ADORepository",
        state: Optional[str] = None,
        head: Optional[str] = None,
        base: Optional[str] = None,
        sort: Optional[str] = "creationDate",
        direction: Optional[str] = "desc",
        number: Optional[int] = -1,
        etag: Optional[str] = None,
    ) -> list["ADOPullRequest"]:
        """Fetches all pull requests from the repository."""
        try:
            search_criteria = GitPullRequestSearchCriteria(
                target_ref_name=f"refs/heads/{base}" if base else None,
                source_ref_name=f"refs/heads/{head}" if head else None,
                status="open",
                repository_id=repo.id,
                source_repository_id=repo.id,
            )
            pull_requests = repo.git_client.get_pull_requests(
                repo.id, search_criteria, repo.project_id
            )

            pull_requests.sort(
                key=lambda p: getattr(p, sort or "pull_request_id", "pull_request_id"),
                reverse=(direction == "desc"),
            )

            return [
                ADOPullRequest(repo=repo, pull_request=pull_request)
                for pull_request in pull_requests
            ]
        except AzureDevOpsServiceError as e:
            e.message = f"Failed to get pull requests: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during getting pull requests: {str(ex)}"
            raise Exception(message)

    @classmethod
    def create_pull(
        cls,
        repo: "ADORepository",
        title: str,
        base: str,
        head: str,
        body: Optional[str] = None,
        maintainer_can_modify: Optional[bool] = False,
        options: Optional[dict] = {},
    ) -> "ADOPullRequest":
        """Creates a pull request on the given repository.

        Args:
            repo: The ADO repository instance
            title: Title of the pull request
            base: Target branch name (where changes will be merged INTO)
            head: Source branch name or commit ID (where changes come FROM)
            body: Optional description of the pull request
            maintainer_can_modify: Optional flag (not used in ADO)
            options: Optional additional options

        Returns:
            ADOPullRequest: The created pull request object

        Note:
            If head is a commit ID (40-character hex string), the method will raise an error.
            If no branch is found, it will attempt to use the commit ID directly,
            though this may fail depending on Azure DevOps API limitations.
        """

        try:
            # Determine if head is a commit ID (SHA) or branch name
            # Commit IDs are typically 40 character hexadecimal strings
            is_head_commit = len(head) == 40 and all(
                c in "0123456789abcdefABCDEF" for c in head
            )
            if is_head_commit:
                raise AzureDevOpsServiceError("Head is a commit ID, not a branch name.")

            source_ref_name = f"refs/heads/{head}"
            target_ref_name = f"refs/heads/{base}"

            pull_request = GitPullRequest(
                source_ref_name=source_ref_name,  # HEAD is the source (FROM)
                target_ref_name=target_ref_name,  # BASE is the target (INTO)
                title=title,
                description=body,
            )

            created_pr = repo.git_client.create_pull_request(
                git_pull_request_to_create=pull_request,
                repository_id=repo.id,
                project=repo.project_id,
            )

            repo.logger.info(
                f"Pull request created: #{created_pr.pull_request_id} - {created_pr.title}"
            )

            return ADOPullRequest(repo=repo, pull_request=created_pr, options=options)
        except AzureDevOpsServiceError as e:
            e.message = f"Failed to create pull request: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during PR creation: {str(ex)}"
            raise Exception(message)

    @property
    def number(self) -> int:
        """Gets the pull request number."""
        return self.pull_request.pull_request_id or 0

    @property
    def base_ref(self) -> str:
        """Gets the base reference of the pull request."""
        return sanitize_path_name(self.pull_request.target_ref_name or "")

    @property
    def head_ref(self) -> str:
        """Gets the head reference of the pull request."""
        return sanitize_path_name(self.pull_request.source_ref_name or "")

    def can_auto_merge(self) -> bool:
        """
        Waits until the pull request has completed merge checks or timeout hits.
        """
        self.repo.logger.debug("Waiting for PR auto-merge check...")
        start_time = time.time()
        timeout = self.options.get("retry_timeout", 100)
        interval = self.options.get("retry_interval", 10)

        while time.time() - start_time < timeout:
            self.reload()

            if self.pull_request.merge_status == "succeeded":
                self.repo.logger.info("Pull request can be automatically merged.")
                return True

            if self.pull_request.merge_status in ("conflicts", "failure"):
                self.repo.logger.info(
                    f"Merge status resolved: {self.pull_request.merge_status}"
                )
                return False

            self.repo.logger.info(
                f"Current merge status: {self.pull_request.merge_status}. Retrying in {interval} seconds..."
            )
            time.sleep(interval)

        self.repo.logger.warning(
            f"Pull request cannot be auto-merged. Merge status: {self.pull_request.merge_status}"
        )
        return False

    def reload(self) -> None:
        """Reloads the pull request object."""
        try:
            # Check if the PR can be auto-merged
            self.pull_request = self.repo.git_client.get_pull_request(
                repository_id=self.repo.id,
                pull_request_id=self.number,
                project=self.repo.project_id,
            )

        except AzureDevOpsServiceError as e:
            e.message = f"Failed to get pull request status: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during PR status check: {str(ex)}"
            raise Exception(message)

    def merge(self) -> None:
        """Merges the pull request."""

        # Set PR to auto-complete and bypass rules
        completion_options = GitPullRequestCompletionOptions(
            delete_source_branch=self.repo.config(
                "completion_opts_delete_source_branch"
            ),
            merge_strategy=self.repo.config("completion_opts_merge_strategy"),
            bypass_policy=self.repo.config("completion_opts_bypass_policy"),
            bypass_reason=self.repo.config("completion_opts_bypass_reason"),
        )

        # Set auto-complete with completion options
        self.set_auto_complete(completion_options)

    def set_auto_complete(
        self, completion_options: GitPullRequestCompletionOptions
    ) -> None:
        """Sets the pull request to auto-complete with the specified completion options."""

        # Create a minimal update object with only auto-complete fields
        auto_complete_update = GitPullRequest()
        auto_complete_update.auto_complete_set_by = self.pull_request.created_by
        auto_complete_update.completion_options = completion_options

        try:
            updated_pr = self.repo.git_client.update_pull_request(
                git_pull_request_to_update=auto_complete_update,
                repository_id=self.repo.id,
                pull_request_id=self.number,
                project=self.repo.project_id,
            )
            self.repo.logger.info(
                f"Pull request #{updated_pr.pull_request_id} set to auto-complete."
            )
            # Update our local pull request object with the returned values
            self.pull_request = updated_pr
        except AzureDevOpsServiceError as e:
            e.message = f"Failed to set auto-complete on pull request #{self.number}: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during setting auto-complete: {str(ex)}"
            raise Exception(message)

    def update(
        self,
        completion_options: Optional[GitPullRequestCompletionOptions] = None,
        status: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Updates the pull request with allowed fields only."""

        # Handle auto-complete separately from other updates
        if completion_options:
            self.set_auto_complete(completion_options)
            return

        # Create update object with only allowed fields (no auto-complete fields)
        update_pr = GitPullRequest()

        if status:
            update_pr.status = status
        if title:
            update_pr.title = title
        if description:
            update_pr.description = description

        # Only proceed if we have fields to update
        if not any([status, title, description]):
            return

        try:
            self.pull_request = self.repo.git_client.update_pull_request(
                git_pull_request_to_update=update_pr,
                repository_id=self.repo.id,
                pull_request_id=self.number,
                project=self.repo.project_id,
            )
            self.repo.logger.info(
                f"Pull request #{self.pull_request.pull_request_id} updated successfully."
            )

        except AzureDevOpsServiceError as e:
            e.message = f"Failed to update pull request #{self.number}: {e.message}"
            raise AzureDevOpsServiceError(e)
        except Exception as ex:
            message = f"Unexpected error during PR update: {str(ex)}"
            raise Exception(message)

    @property
    def title(self) -> str:
        """Gets the pull request title."""
        return self.pull_request.title or ""

    @property
    def merged_at(self) -> datetime:
        """Gets the merged date of the short pull request."""
        return self.pull_request.closed_date or datetime.now(UTC)


class ADORelease(AbstractRelease):
    """Azure DevOps release object for creating and managing releases."""

    release: "PackageVersion"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tag_name = kwargs.get("tag_name", "")

    @property
    def tag_name(self) -> str:
        """Gets the tag name of the release."""
        if self._tag_name:
            return self._tag_name

        description = self.release.package_description or self.release.description
        if description:
            try:
                tag_name = json.loads(description).get("tag_name", "")
                if tag_name:
                    self._tag_name = tag_name
            except json.JSONDecodeError:
                pass

        if self._tag_name is None:
            self._tag_name = self.release.version

        return self._tag_name or ""

    @property
    def body(self) -> Union[str, None]:
        """Gets the body of the release."""
        return self.release.package_description if self.release else None

    @property
    def prerelease(self) -> bool:
        """Checks if the release is a pre-release."""
        if self.release and self.release.views and len(self.release.views) > 0:
            if self.release.views[0] is not None:
                return (
                    self.release.views[0].type == "release"
                    and self.release.views[0].name == PRERELEASE
                )

        return False

    @property
    def name(self) -> str:
        """Gets the name of the release."""
        return self.release.version or "" if self.release else ""

    @property
    def html_url(self) -> str:
        """Gets the HTML URL of the release."""
        return self.release.url or "" if self.release else ""

    @property
    def created_at(self) -> Optional[datetime]:
        """Gets the creation date of the release."""
        return self.release.publish_date if self.release else None

    @property
    def draft(self) -> bool:
        """Checks if the release is a draft."""
        return any(
            view
            for view in self.release.views or []
            if view.type == "implicit" and view.name.lower() == "local"
        )

    @property
    def tag_ref_name(self) -> str:
        """Gets the tag reference name of the release."""
        return "tags/" + self.tag_name

    @property
    def updateable(self) -> bool:
        """Checks if the release is updateable."""
        return True


class ADORepository(AbstractRepo):

    project_config: BaseProjectConfig
    connection: Connection
    git_client: GitClient
    repo: Optional[GitRepository]
    project: Optional[TeamProjectReference]

    def __init__(
        self, connection: Connection, config: BaseProjectConfig, **kwargs
    ) -> None:
        """Initializes the repository object."""
        super().__init__(**kwargs)
        self.connection = connection
        self.project_config = config
        self.service_type = kwargs.get("service_type", "azure_devops")
        self._service_config = kwargs.get("service_config")
        self.repo = None
        self.project = None
        self._package_name = kwargs.get("package_name", "")
        self.api_version = kwargs.get("api_version", None)
        self._tooling = None
        self._feed_client: Optional[FeedClient] = None
        self._package: Optional[Package] = None
        self._existing_prs = None

    def _init_repo(self) -> None:
        """Initializes the repository object."""
        repo_url = self.options.get("repository_url", self.project_config.repo_url)
        if repo_url is not None:
            _repo_owner, _repo_name, _host, _project = parse_repo_url(repo_url)

        self.repo_owner = (
            _repo_owner
            or self.options.get("repo_owner")
            or self.project_config.repo_owner
            or ""
        )
        self.repo_name = (
            _repo_name
            or self.options.get("repo_name")
            or self.project_config.repo_name
            or ""
        )

        self.git_client = self.connection.clients.get_git_client()

        self.repo = self.git_client.get_repository(self.repo_name, _project)
        self.project = self.repo.project if self.repo else None

    @property
    def id(self) -> Optional[str]:
        """Returns the ID of the repository."""
        if self.repo is None:
            raise ValueError("Repository is not set. Cannot access its ID.")
        return self.repo.id

    @property
    def project_id(self) -> Optional[str]:
        """Returns the project ID of the repository."""
        if self.project is None:
            raise ValueError("Project is not set. Cannot access its ID.")
        return self.project.id

    @property
    def service_config(self):
        if not self._service_config and self.project_config.keychain is not None:
            self._service_config = self.project_config.keychain.get_service(
                self.service_type
            )
        return self._service_config

    @property
    def owner_login(self) -> str:
        """Returns the owner login of the repository."""
        # Applicable only for Github
        return ""

    @property
    def package_name(self) -> str:
        """Returns the package name of the repository."""
        if self._package_name:
            return self._package_name

        self._package_name = self.project_config.project__package__name.replace(
            " ", "-"
        ).lower()

        return self._package_name

    @property
    def feed_name(self) -> str:
        fname: str = self.project_config.project__custom__ado_feedname or str(
            self.config("feed_name")
        )
        return fname.replace(" ", "-").lower()

    @property
    def organisation_artifact(self) -> bool:
        if (
            self.project_config.project__custom__ado_organisation_artifact is None
            and self.config("organisation_artifact")
            or self.project_config.project__custom__ado_organisation_artifact
        ):
            return True
        return False

    @property
    def feed_client(self) -> FeedClient:
        if self._feed_client is None:
            self._feed_client = self.connection.clients.get_feed_client()

        if self._feed_client:
            return self._feed_client
        raise AzureDevOpsClientError("Unable to get Feed Client.")

    @property
    def tooling(self):
        if self._tooling is None:
            self._tooling = get_simple_salesforce_connection(
                self.project_config,
                get_devhub_config(self.project_config),
                api_version=self.api_version
                or self.project_config.project__package__api_version,
                base_url="tooling",
            )
        return self._tooling

    def config(self, key: str) -> Optional[Union[str, bool]]:
        """Returns the plugin configuration for the ADO."""
        return self.project_config.lookup(
            f"plugins__azure_devops__config__{key}", default=None
        )

    def get_ref(self, ref_sha: str) -> Optional[ADORef]:
        """Gets a Reference object for the tag with the given SHA"""
        try:
            refs: list[GitRef] = self.git_client.get_refs(
                self.id,
                self.project_id,
                filter=(ref_sha or f"heads/{self.default_branch}"),
                include_statuses=True,
                latest_statuses_only=True,
                peel_tags=True,
                top=1,
            )

            if refs and (refs[0].peeled_object_id or refs[0].object_id):
                commit = self.get_commit(
                    refs[0].peeled_object_id or refs[0].object_id or ""
                )
                return ADORef(refs[0], sha=commit.sha)

            raise

        except Exception as e:
            raise ADOApiNotFoundError(
                f"Could not find reference for '{ref_sha}' on ADO. Error: {str(e)}"
            )

    def get_ref_for_tag(self, tag_name: str) -> Optional[ADORef]:
        """Gets a Reference object for the tag with the given name"""
        try:
            refs: list[GitRef] = self.git_client.get_refs(
                self.id,
                self.project_id,
                filter=f"tags/{tag_name}",
                include_statuses=True,
                latest_statuses_only=True,
                peel_tags=True,
            )

        except Exception as e:
            raise ADOApiNotFoundError(
                f"Could not find reference for 'tags/{tag_name}' on ADO. Error: {str(e)}"
            )

        if len(refs) > 1:
            raise ADOApiNotFoundError(f"More than one tag found for {tag_name}.")

        if len(refs) == 0:
            raise ADOApiNotFoundError(f"Could not find tag {tag_name}.")

        ref = refs[0]

        if ref.peeled_object_id is None:
            msg = f"Could not find tag '{tag_name}' with SHA {ref.object_id} on ADO."
            msg += f"\n{tag_name} is not an annotated tag."
            raise ADOApiNotFoundError(msg)

        return ADORef(ref=ref)

    def get_tag_by_ref(self, ref: ADORef, tag_name: Optional[str] = None) -> ADOTag:
        """Fetches a tag by reference, name from the given repository"""
        try:
            annotatedTag: GitAnnotatedTag = self.git_client.get_annotated_tag(
                self.project_id, self.id, ref.sha
            )

        except Exception as e:
            msg = f"Could not find tag '{tag_name}' with SHA {ref.sha} on ADO"
            if ref.type != "tag":
                msg += f"\n{tag_name} is not an annotated tag."
            msg += f"\nError: {str(e)}"
            raise AzureDevOpsClientError(msg)

        if annotatedTag is None:
            raise AzureDevOpsClientError("Annotated Tag not found.")

        return ADOTag(tag=annotatedTag)

    def create_tag(
        self,
        tag_name: str,
        message: str,
        sha: str,
        obj_type: str,
        tagger: dict = {},
        lightweight: bool = False,
    ) -> ADOTag:
        # Create a tag on the given repository

        clone_tag = GitAnnotatedTag()
        clone_tag.message = message
        clone_tag.name = tag_name

        clone_tag.tagged_object = GitObject()
        clone_tag.tagged_object.object_id = sha

        try:
            tag = self.git_client.create_annotated_tag(
                clone_tag, self.project_id, self.id
            )
        except Exception as e:
            self.logger.error(f"Error: Clone tag {e}")
            raise AzureDevOpsClientError(f"Could not create tag {tag_name} on ADO.")

        return ADOTag(tag=tag)

    def branch(self, branch_name) -> ADOBranch:
        # # Fetches a branch from the given repository
        return ADOBranch(self, branch_name)

    def branches(self) -> list[ADOBranch]:
        # # Fetches all branches from the given repository
        return ADOBranch.branches(self)

    def compare_commits(
        self, branch_name: str, commit: str, source: str
    ) -> ADOComparison:
        # # Compares the given branch with the given commit
        branch = ADOBranch(self, branch_name=branch_name)
        return ADOComparison.compare(self, branch.commit_id, commit)

    def merge(
        self, base: str, head: str, source: str, message: str = ""
    ) -> Union[ADOPullRequest, None]:

        body = (
            message + "\nThis pull request was automatically generated because "
            "an automated merge hit a merge conflict"
        )

        # Get open PRs for this source
        if base in self._get_existing_prs(source):
            self.logger.info(
                f"Pull request already exists for {source} into {base}. Skipping merge."
            )
            return None

        created_pr: ADOPullRequest = self.create_pull(
            base=base,
            head=source,
            body=body,
            title=f"Automerge {source} into {base}",
            options=self.options,
        )

        if created_pr.can_auto_merge() is True:
            created_pr.merge()
        elif not (self.options.get("create_pull_request_on_conflict")):
            created_pr.update(status="abandoned")
        else:
            self.logger.info(f"Merge conflict on branch {base}: Pull request created")
        return created_pr

    def _get_existing_prs(self, source_branch):
        """Returns the existing pull requests from the source branch
        to other branches that are candidates for merging."""
        if self._existing_prs is not None:
            return self._existing_prs

        self._existing_prs = []
        for pr in self.pull_requests(state="active", head=source_branch):
            if (
                pr.base_ref.startswith(self.options.get("branch_prefix", ""))
                and pr.head_ref == source_branch
            ):
                self._existing_prs.append(pr.base_ref)
        return self._existing_prs

    def pull_requests(
        self,
        state: Optional[str] = None,
        head: Optional[str] = None,
        base: Optional[str] = None,
        sort: Optional[str] = "createdDate",
        direction: Optional[str] = "desc",
        number: Optional[int] = -1,
        etag: Optional[str] = None,
    ) -> list[ADOPullRequest]:
        """Fetches all pull requests from the given repository"""
        return ADOPullRequest.pull_requests(
            self,
            state=state,
            head=head,
            base=base,
            sort=sort,
            direction=direction,
            number=number,
            etag=etag,
        )

    def create_pull(
        self,
        title: str,
        base: str,
        head: str,
        body: Optional[str] = None,
        maintainer_can_modify: Optional[bool] = None,
        options: Optional[dict] = {},
    ) -> ADOPullRequest:
        """Creates a pull request on the given repository"""
        if options:
            self.options.update(options)

        pull_request = ADOPullRequest.create_pull(
            self,
            title,
            base,
            head,
            body=body,
            maintainer_can_modify=maintainer_can_modify,
            options=self.options,
        )
        return pull_request

    def get_commit(self, commit_sha: str) -> ADOCommit:
        """Given a SHA1 hash, retrieve a Commit object from the REST API."""
        try:
            commit: GitCommit = self.git_client.get_commit(
                commit_sha, self.id, project=self.project_id
            )
            return ADOCommit(
                commit=commit,
                git_client=self.git_client,
                repo_id=self.id,
                project_id=self.project_id,
            )
        except AzureDevOpsServiceError:
            raise ADOApiNotFoundError(
                f"Could not find commit {commit_sha} on Azure DevOps"
            )

    def get_package(self) -> Optional[Package]:
        """Fetches a package from the given repository."""
        if self._package:
            return self._package

        artifacts: list[Package] = self.feed_client.get_packages(
            self.feed_name,
            project=(None if self.organisation_artifact else self.project_id),
            package_name_query=self.package_name,
            include_description=True,
        )
        pkgs: list[Package] = [
            pkg for pkg in artifacts if pkg.name == self.package_name
        ]
        if not pkgs:
            return None
        self._package = pkgs[0]
        return self._package

    def get_version_package(
        self, version_name: str
    ) -> Tuple[Optional[PackageVersion], Optional[Package]]:
        """Fetches a version from the given repository."""
        pkg = self.get_package()
        if not pkg:
            return None, None

        # ADO does not allow version name to be alphanumeric, so we need to filter by version name
        numeric_part = custom_to_semver(version_name, self.project_config)

        versions: list[PackageVersion] = self.feed_client.get_package_versions(
            self.feed_name,
            pkg.id,
            project=(None if self.organisation_artifact else self.project_id),
        )

        versions = [
            version for version in versions or [] if version.version == numeric_part
        ]
        if not versions:
            return None, pkg

        return versions[0], pkg

    def release_from_tag(self, tag_name: str) -> ADORelease:
        """Fetches a release from the given tag name."""
        try:
            version, _ = self.get_version_package(tag_name)

            if not version:
                raise ADOApiNotFoundError(
                    f"Version {tag_name} not found for package {self.package_name}"
                )
        except AzureDevOpsServiceError:
            message = f"Release for {tag_name} not found"
            raise ADOApiNotFoundError(message)
        except ADOApiNotFoundError as e:
            raise ADOApiNotFoundError(str(e))
        return ADORelease(release=version, tag_name=tag_name)

    @property
    def default_branch(self) -> str:
        """Returns the default branch of the repository."""
        default_branch: str = self.repo.default_branch or "" if self.repo else ""
        return default_branch.replace("refs/heads/", "")

    def archive(self, format: str, zip_content: Union[str, BytesIO], ref=None) -> bytes:
        """Archives the repository content as a zip file."""
        try:

            if ref is not None and ref == self.default_branch:
                branch = ADOBranch(self, ref)
                ref = branch.commit_id

            commit = self.get_commit(ref or "")

            byte_iter: Iterator[bytes] = self.git_client.get_tree_zip(
                self.id, commit.tree_id, project_id=self.project_id
            )
            zip_bytes = b"".join(byte_iter)

            if isinstance(zip_content, str):
                with open(zip_content, "wb") as f:
                    f.write(zip_bytes)
            elif hasattr(zip_content, "write"):
                zip_content.write(zip_bytes)

            return zip_bytes
        except AzureDevOpsServiceError:
            raise ADOApiNotFoundError(
                f"Could not find archive for {zip_content} for service {self.service_type}"
            )

    def full_name(self) -> str:
        """Returns the full name of the repository."""
        return self.repo.name or "" if self.repo else ""

    def publish_repo_package(self, feed, tag_name: str, description: str) -> None:
        """Publishes a package to the given feed."""
        repo_root = self.project_config.repo_root or "."
        artifact_path = os.path.join(
            repo_root, str(self.config("artifact_folder_path") or "")
        )

        if not os.path.exists(artifact_path):
            artifact_path = repo_root

        ctool = self.connection.get_client(
            "cumulusci_ado.utils.common.client_tool.client_tool_client.ClientToolClient"
        )

        ret = publish_package(
            ctool,
            feed.id,
            self.package_name,
            tag_name,
            artifact_path,
            description=description,
            scope=("organization" if self.organisation_artifact else "project"),
            organization=f"https://{self._service_config.url if self._service_config else ''}",
            project=(None if self.organisation_artifact else self.project_id),
            detect=None,
        )

        return ret

    def get_feed(self) -> Feed:
        """Fetches the feed for the given repository."""
        try:
            feed: Feed = self.feed_client.get_feed(
                self.feed_name,
                project=(None if self.organisation_artifact else self.project_id),
            )
        except AzureDevOpsServiceError:
            feed_instance = Feed(name=self.feed_name)
            feed: Feed = self.feed_client.create_feed(
                feed_instance,
                project=(None if self.organisation_artifact else self.project_id),
            )
        return feed

    def create_release(
        self,
        tag_name: str,
        name: str = "",
        body: str = "",
        draft: bool = False,
        prerelease: bool = False,
        options: dict = {},
    ) -> ADORelease:
        """Creates a release on the given repository."""

        feed: Feed = self.get_feed()

        try:
            numeric_part = custom_to_semver(tag_name, self.project_config)

            version, pkg = self.get_version_package(tag_name)

            description = {"tag_name": tag_name}

            if options.get("version_id"):
                description["version_id"] = options["version_id"]

            if version is None:
                self.publish_repo_package(
                    feed,
                    numeric_part,
                    description=json.dumps(description),
                )

                self._package = None
                version, pkg = self.get_version_package(tag_name)

            if not pkg:
                raise ADOApiNotFoundError(
                    f"Package {self.package_name} not found in feed {self.feed_name}"
                )

            if not version:
                raise ADOApiNotFoundError(
                    f"Version {tag_name} not found for package {self.package_name}"
                )

            release: PackageVersion = self.feed_client.get_package_version(
                feed.id,
                pkg.id,
                version.id,
                project=(None if self.organisation_artifact else self.project_id),
            )

            if draft:
                return ADORelease(release=release, tag_name=tag_name)

            feed_view = self.get_feed_view(feed, prerelease=prerelease)

            json_operation = JsonPatchOperation(
                op="add", path="/views/-", value=feed_view.name
            )
            pkg_version_details = PackageVersionDetails(views=json_operation)

            upack_api_client: UPackApiClient = (
                self.connection.clients.get_upack_api_client()
            )
            upack_api_client.update_package_version(
                pkg_version_details,
                self.feed_name,
                self.package_name,
                numeric_part,
                (None if self.organisation_artifact else self.project_id),
            )

            release.views = [feed_view]

            return ADORelease(release=release, tag_name=tag_name)
        except AzureDevOpsServiceError as e:
            raise ADOApiNotFoundError(
                f"Could not create release for {tag_name} on Azure DevOps: {str(e)}"
            )

    def get_feed_view(self, feed, prerelease: bool = False) -> FeedView:
        """Fetches the feed view for the given feed."""
        if prerelease:
            feed_view_name = PRERELEASE
        else:
            feed_view_name = RELEASE

        feed_view = self.feed_client.get_feed_view(
            feed.id,
            feed_view_name,
            project=(None if self.organisation_artifact else self.project_id),
        )

        if feed_view is None:
            feed_view = FeedView(name=feed_view_name, type="release")
            feed_view = self.feed_client.create_feed_view(
                feed_view,
                feed.id,
                project=(None if self.organisation_artifact else self.project_id),
            )

        return feed_view

    def releases(self) -> list[ADORelease]:
        """Fetches all releases from the given repository."""
        try:
            artifacts: list[Package] = self.feed_client.get_packages(
                self.feed_name,
                project=(None if self.organisation_artifact else self.project_id),
                package_name_query=self.package_name,
                include_all_versions=True,
            )

            versions = []
            for package in artifacts:
                versions.extend(
                    ADORelease(release=pkg_ver) for pkg_ver in package.versions or []
                )

            return versions
        except AzureDevOpsServiceError:
            raise ADOApiNotFoundError(
                f"Could not find releases for {self.package_name} on Azure DevOps"
            )

    def latest_release(self) -> Optional[ADORelease]:
        """Fetches the latest release from the given repository."""
        try:
            return self.get_latest_artifact()
        except AzureDevOpsServiceError:
            raise ADOApiNotFoundError(
                f"Could not find latest release for {self.package_name} on Azure DevOps"
            )

    def has_issues(self) -> bool:
        """Checks if the repository has issues enabled."""
        wit_client = self.connection.clients.get_work_item_tracking_client()

        # Try to list work item types, as there is no direct API to check if work items are enabled
        work_item_types = wit_client.get_work_item_types(project=self.project_id)
        if work_item_types:
            return True
        return False

    def get_pull_requests_by_commit(self, commit_sha) -> list[ADOPullRequest]:
        """Fetches all pull requests associated with the given commit SHA."""

        query_input = GitPullRequestQueryInput(
            items=[commit_sha],
            type="commit",  # This is the key value for commit-based queries
        )
        query = GitPullRequestQuery(queries=[query_input])

        pr_query: GitPullRequestQuery = self.git_client.get_pull_request_query(
            queries=query, repository_id=self.id, project=self.project_id
        )

        return [
            ADOPullRequest(repo=self, pull_request=pr) for pr in pr_query.results or []
        ]

    def get_pr_issue_labels(self, pull_request: ADOPullRequest) -> list[str]:
        """Fetches all labels associated with the given pull request."""
        # 1. Get work items linked to the pull request
        work_items_refs = self.git_client.get_pull_request_work_item_refs(
            self.id, pull_request.number, project=self.project_id
        )

        wit_client = self.connection.clients.get_work_item_tracking_client()
        # 2. For each work item, get tags
        labels = []
        for work_item_ref in work_items_refs:
            work_item = wit_client.get_work_item(
                work_item_ref.id, fields=["System.Tags"]
            )
            tags = work_item.fields.get("System.Tags", "")
            labels.extend(tags.split(";"))

        return labels

    def directory_contents(self, subfolder: str, return_as, ref: str) -> dict:
        """Fetches the contents of a directory in the repository."""
        try:
            version_type = "commit" if len(ref) == 40 else "branch"

            version_descriptor = GitVersionDescriptor(
                version=ref, version_type=version_type
            )
            items = self.git_client.get_items(
                repository_id=self.id,
                project=self.project_id,
                scope_path=subfolder,
                recursion_level="OneLevel",  # only one level deep should suffice.
                version_descriptor=version_descriptor,
            )

            contents = {}
            for item in items:
                name = item.path.split("/")[-1]
                if name == subfolder.split("/")[-1]:
                    continue
                contents[
                    name
                ] = item  # Wrap this in a custom class, but content values are not processed.
        except AzureDevOpsServiceError as e:
            # Handle the case where the directory does not exist
            raise ADOApiNotFoundError(
                f"Could not find directory {subfolder} on Azure DevOps: {str(e)}"
            )
        return contents

    @property
    def clone_url(self) -> str:
        """Fetches the clone URL for the repository."""
        return self.repo.remote_url or "" if self.repo else ""

    def file_contents(self, file_path: str, ref: str) -> StringIO:
        """Fetches the contents of a file in the repository."""
        from azure.devops.v7_0.git.models import GitVersionDescriptor

        version_type = "commit" if len(ref) == 40 else "branch"

        contents = self.git_client.get_item_content(
            repository_id=self.id,
            path=file_path,
            project=self.project_id,
            version_descriptor=GitVersionDescriptor(
                version_type=version_type,
                version=ref.split("/")[-1] if ref.startswith("refs/") else ref,
            ),
        )

        contents_io = StringIO(
            b"".join(chunk for chunk in contents).decode("utf-8") if contents else ""
        )
        contents_io.url = f"{file_path} from {self.repo_url}"  # type: ignore

        return contents_io

    def get_latest_artifact(self, prerelease: bool = False) -> Optional[ADORelease]:
        pkg = self.get_package()

        if not pkg:
            raise ADOApiNotFoundError(
                f"Could not find package {self.package_name} on Azure DevOps"
            )

        if prerelease:
            release_versions = [
                v
                for v in pkg.versions or []
                if v.views
                and any(getattr(view, "type", None) == "release" for view in v.views)
            ]
        else:
            release_versions = [
                v
                for v in pkg.versions or []
                if v.views
                and any(
                    view.name == RELEASE and getattr(view, "type", None) == "release"
                    for view in v.views
                )
            ]

        if any(release_versions):
            latest = max(release_versions, key=lambda v: v.version)
        else:
            return None

        version = self.feed_client.get_package_version(
            self.feed_name,
            pkg.id,
            latest.id,
            project=(None if self.organisation_artifact else self.project_id),
            is_deleted=False,
        )

        return ADORelease(release=version)

    def get_latest_prerelease(self) -> Optional[ADORelease]:
        """Fetches the latest prerelease from the repository."""
        try:
            return self.get_latest_artifact(prerelease=True)
        except AzureDevOpsServiceError:
            raise ADOApiNotFoundError(
                f"Could not find lastest release for {self.package_name} on Azure DevOps"
            )

    def create_commit_status(
        self,
        commit_id: str,
        context: str,
        state: str,
        description: str,
        target_url: str,
    ) -> ADOCommit:
        """Creates a commit status in the repository."""
        try:
            ctx = GitStatusContext(
                genre="cumulusci",
                name=context,
            )

            git_status = GitStatus(
                context=ctx,
                state=state,
                description=description,
                target_url=target_url,
            )

            status = self.git_client.create_commit_status(
                git_status, commit_id, self.id, self.project_id
            )

            if not status:
                raise AzureDevOpsServiceError(
                    f"Failed to create commit status for {commit_id} on Azure DevOps"
                )

            return self.get_commit(commit_id)
        except AzureDevOpsServiceError as e:
            raise ADOApiNotFoundError(
                f"Could not create commit status for {commit_id} on Azure DevOps: {str(e)}"
            )
