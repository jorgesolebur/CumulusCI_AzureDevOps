from functools import lru_cache
from typing import TYPE_CHECKING, List, Optional, Type

import requests
from azure.devops.connection import Connection
from azure.devops.exceptions import AzureDevOpsAuthenticationError
from cumulusci.core.config import BaseProjectConfig, ServiceConfig
from cumulusci.vcs.base import VCSService
from msrest.authentication import BasicAuthentication

from cumulusci_ado.utils.ado import parse_repo_url

if TYPE_CHECKING:
    from cumulusci_ado.vcs.ado import ADORelease, ADORepository
    from cumulusci_ado.vcs.ado.dependencies.ado_dependencies import ADODynamicDependency
    from cumulusci_ado.vcs.ado.generator import (
        ADOParentPullRequestNotesGenerator,
        ADOReleaseNotesGenerator,
    )


@lru_cache(50)
def get_ado_service_for_url(project_config, url: str) -> Optional["AzureDevOpsService"]:
    # Note: This function is defined after the class, so we need to access it dynamically
    # to avoid circular import issues
    return AzureDevOpsService.get_service_for_url(project_config, url)


class AzureDevOpsService(VCSService):
    service_type: str = "azure_devops"
    _repo: Optional["ADORepository"]

    def __init__(self, config: BaseProjectConfig, name: Optional[str] = None, **kwargs):
        """Initializes the ADO service with the given project configuration.
        Args:
            config (BaseProjectConfig): The configuration for the ADO service.
            name (str): The name or alias of the VCS service.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(config, name, **kwargs)
        # Set azure variables
        self.repo_url = kwargs.get("repository_url", self.config.repo_url)
        self.connection = self.__class__.get_api_connection(self.service_config)
        self.core_client = self.connection.clients.get_core_client()
        self._repo = None

    @classmethod
    def validate_service(cls, options: dict, keychain) -> dict:
        personal_access_token = options["token"]
        organization_url = options["url"]

        cls.validate_duplicate_service(keychain, organization_url)

        try:
            connection = cls._authenticate(personal_access_token, organization_url)
            core_client = connection.clients.get_core_client()
            base_url = core_client.config.base_url
            assert organization_url in base_url, f"https://{organization_url}"
        except Exception as e:
            raise AzureDevOpsAuthenticationError(f"Authentication Error. ({str(e)})")

        return options

    @classmethod
    def validate_duplicate_service(cls, keychain, organization_url):
        """Check if the service is already configured in the keychain."""
        services = keychain.get_services_for_type(cls.service_type)
        if not services:
            return True

        hosts = [service.url for service in services]
        if hosts.count(organization_url) > 1:
            raise AzureDevOpsAuthenticationError(
                f"More than one Azure Devops service configured for domain {organization_url}."
            )
        return True

    @staticmethod
    def _authenticate(
        token: str, org_url: str, session: Optional[requests.Session] = None
    ) -> Connection:
        """Authenticate to Azure DevOps using a personal access token."""
        organization_url = f"https://{org_url}"

        credentials = BasicAuthentication("", token)
        if session is not None:
            credentials.signed_session(session)
        connection = Connection(base_url=organization_url, creds=credentials)

        # Get a client (the "core" client provides access to projects, teams, etc)
        connection.authenticate()

        return connection

    @classmethod
    def get_api_connection(
        cls, service_config, session: Optional[requests.Session] = None
    ) -> Connection:
        return cls._authenticate(service_config.token, service_config.url, session)

    @classmethod
    def get_service_for_url(
        cls,
        project_config: BaseProjectConfig,
        url: str,
        service_alias: Optional[str] = None,
    ) -> Optional["AzureDevOpsService"]:
        """Returns the service configuration for the given URL."""
        _owner, _repo_name, host, project = parse_repo_url(url)

        configured_services: list[ServiceConfig] = []

        if project_config.keychain is not None:
            # Check if the service is already configured in the keychain
            configured_services = project_config.keychain.get_services_for_type(
                cls.service_type
            )

        service_by_host = {
            service.url.rstrip("/"): service for service in configured_services
        }

        azure_url = f"{host}/{_owner}"

        # Check when connecting to server, but not when creating new service as this would always catch
        if azure_url is not None and list(service_by_host.keys()).count(azure_url) == 0:
            project_config.logger.debug(
                f"No Azure DevOps service configured for domain {azure_url} : {url}."
            )
            return None

        if azure_url is not None:
            service_config = service_by_host[azure_url]

        vcs_service = AzureDevOpsService(
            project_config,
            name=service_config.name,
            service_config=service_config,
            logger=project_config.logger,
            repository_url=url,
        )
        project_config.logger.info(
            f"Azure DevOps service configured for domain {host} : {url}."
        )
        return vcs_service

    @property
    def dynamic_dependency_class(self) -> Type["ADODynamicDependency"]:
        """Returns the dynamic dependency class for the Azure DevOps service."""
        from cumulusci_ado.vcs.ado.dependencies.ado_dependencies import (
            ADODynamicDependency,
        )

        return ADODynamicDependency

    def get_repository(self, options: dict = {}) -> Optional["ADORepository"]:
        """Returns the Azure DevOps repository."""
        if self._repo is None:
            from cumulusci_ado.vcs.ado import ADORepository

            self._repo = ADORepository(
                self.connection,
                self.config,
                logger=self.logger,
                service_type=self.service_type,
                service_config=self.service_config,
                options=options,
            )
            self._repo._init_repo()  # type: ignore
        return self._repo

    def parse_repo_url(self) -> List[str]:
        owner, repo_name, host, project = parse_repo_url(self.repo_url)
        return [host or "", owner or "", repo_name or "", project or ""]

    def get_committer(self, repo: "ADORepository"):
        """Returns the committer for the Azure DevOps repository."""
        from cumulusci.tasks.github.util import CommitDir

        return CommitDir(repo.repo, logger=self.logger)

    def markdown(
        self, release: "ADORelease", mode: str = "gfm", context: str = ""
    ) -> str:
        """Converts the given text to Azure DevOps-flavored Markdown."""
        release_html = ""
        # TODO: Implement the markdown logic
        # self.github.markdown(
        #     release,
        #     mode=mode,
        #     context=context,
        # )
        return release_html

    def release_notes_generator(self, options: dict) -> "ADOReleaseNotesGenerator":
        from cumulusci_ado.vcs.ado.generator import ADOReleaseNotesGenerator

        github_info = {
            "github_owner": self.config.repo_owner,
            "github_repo": self.config.repo_name,
            "github_username": self.service_config.username,
            "github_password": self.service_config.password,
            "default_branch": self.config.project__git__default_branch,
            "prefix_beta": self.config.project__git__prefix_beta,
            "prefix_prod": self.config.project__git__prefix_release,
        }

        generator = ADOReleaseNotesGenerator(
            self.core_client,
            github_info,
            self.config.project__git__release_notes__parsers.values(),
            options["tag"],
            options.get("last_tag"),
            options.get("link_pr", False),
            options.get("publish", False),
            False,  # self.get_repository().has_issues
            options.get("include_empty", False),
            version_id=options.get("version_id"),
            trial_info=options.get("trial_info", False),
            sandbox_date=options.get("sandbox_date", None),
            production_date=options.get("production_date", None),
        )

        # TODO: Implement the generator logic

        return generator

    def parent_pr_notes_generator(
        self, repo: "ADORepository"
    ) -> "ADOParentPullRequestNotesGenerator":
        """Returns the parent pull request notes generator for the ADO repository."""
        from cumulusci_ado.vcs.ado.generator import ADOParentPullRequestNotesGenerator

        # TODO: Implement the parent PR notes generator logic
        return ADOParentPullRequestNotesGenerator(
            self.core_client, repo.repo, self.config
        )
