import logging
import os
import re
import sys
from typing import Optional, Tuple
from urllib.parse import ParseResult, urlparse

try:
    import colorama  # type: ignore
except ImportError:
    # coloredlogs only installs colorama on Windows
    pass

from cumulusci.core.config.project_config import BaseProjectConfig
from cumulusci.core.exceptions import CumulusCIFailure
from cumulusci.utils import CUMULUSCI_PATH
from cumulusci.utils.git import EMPTY_URL_MESSAGE

from cumulusci_ado.utils.common.artifacttool import ArtifactToolInvoker
from cumulusci_ado.utils.common.artifacttool_updater import ArtifactToolUpdater
from cumulusci_ado.utils.common.external_tool import (
    ProgressReportingExternalToolInvoker,
)

logger = logging.getLogger("cumulusci_ado")

PIP_UPDATE_CMD = "pip install --upgrade cumulusci-plus-azure-devops"
PIPX_UPDATE_CMD = "pipx upgrade cumulusci-plus-azure-devops"


def parse_repo_url(
    url: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Parses a given URI into Owner, Repo Name, Host and Project

    Parameters
    ----------
    url: str
        A Azure URI. Examples: ["https://user@dev.azure.com/[org|user]/project/_git/repo", "git@ssh.dev.azure.com:v3/[user|org]/project/repo"]
    Returns
    -------
    Tuple: (Optional[str], Optional[str], Optional[str], Optional[str])
        Returns (owner, name, host, project)
    """
    if not url:
        raise ValueError(EMPTY_URL_MESSAGE)

    formatted_url = f"ssh://{url}" if url.startswith("git") else url
    parse_result: ParseResult = urlparse(formatted_url)

    host: str = parse_result.hostname or ""
    host = host.replace("ssh.", "") if url.startswith("git") else host

    url_parts = re.split("/|@|:", parse_result.path.rstrip("/").lstrip("/"))
    url_parts = list(filter(None, url_parts))

    name: Optional[str] = url_parts[-1]
    if name.endswith(".git"):
        name = name[:-4]

    owner: Optional[str] = url_parts[0]
    project: Optional[str] = url_parts[1] if len(url_parts) > 1 else None

    return (owner, name, host, project)


def publish_package(
    client_tool,
    feed,
    name,
    version,
    path,
    description=None,
    scope="organization",
    organization=None,
    project=None,
    detect=None,
):
    """Publish a package to a feed.
    :param scope: Scope of the feed: 'project' if the feed was created in a project, and 'organization' otherwise.
    :type scope: str
    :param feed: Name or ID of the feed.
    :type feed: str
    :param name: Name of the package, e.g. 'foo-package'.
    :type name: str
    :param version: Version of the package, e.g. '1.0.0'.
    :type version: str
    :param description: Description of the package.
    :type description: str
    :param path: Directory containing the package contents.
    :type path: str
    """
    if os.name == "nt" and "colorama" in sys.modules:  # pragma: no cover
        colorama.init()  # Needed for humanfriendly spinner to display correctly

    if scope == "project":
        if project is None:
            raise CumulusCIFailure("--scope 'project' requires a value in --project")
    else:
        if project is not None:
            raise CumulusCIFailure(
                "--scope 'project' is required when specifying a value in --project"
            )

    artifact_tool = ArtifactToolInvoker(
        client_tool, ProgressReportingExternalToolInvoker(), ArtifactToolUpdater()
    )
    return artifact_tool.publish_universal(
        organization, project, feed, name, version, description, path
    )


def download_package(
    client_tool,
    feed,
    name,
    version,
    path,
    file_filter=None,
    scope="organization",
    organization=None,
    project=None,
    detect=None,
):
    """Download a package.
    :param scope: Scope of the feed: 'project' if the feed was created in a project, and 'organization' otherwise.
    :type scope: str
    :param feed: Name or ID of the feed.
    :type feed: str
    :param name: Name of the package, e.g. 'foo-package'.
    :type name: str
    :param version: Version of the package, e.g. 1.0.0.
    :type version: str
    :param path: Directory to place the package contents.
    :type path: str
    :param file_filter: Wildcard filter for file download.
    :type file_filter: str
    """
    if os.name == "nt" and "colorama" in sys.modules:  # pragma: no cover
        colorama.init()  # Needed for humanfriendly spinner to display correctly

    if scope == "project":
        if project is None:
            raise CumulusCIFailure("--scope 'project' requires a value in --project")
    else:
        if project is not None:
            raise CumulusCIFailure(
                "--scope 'project' is required when specifying a value in --project"
            )

    artifact_tool = ArtifactToolInvoker(
        client_tool, ProgressReportingExternalToolInvoker(), ArtifactToolUpdater()
    )
    return artifact_tool.download_universal(
        organization, project, feed, name, version, path, file_filter
    )


def custom_to_semver(version_str: str, project_config: BaseProjectConfig) -> str:
    """
    Converts a custom version string with an optional prerelease prefix and any separator
    to a valid SemVer 2.0 string, using named regex groups.
    Examples:
      'beta_0.1.0.1'  -> '0.1.0-build.1'
      'beta/0.1.0.1'  -> '0.1.0-build.1'
      'alpha-1.2.3'   -> '1.2.3-alpha'
      '1.2.3.4'       -> '1.2.3-build4'
      '1.2.3'         -> '1.2.3'
    """
    # Regex with named groups: 'prefix' and 'numbers'
    match = re.match(
        r"^(?P<prefix>[a-zA-Z0-9]+)[^\d.]+(?P<numbers>[\d.]+)$", version_str
    )
    if match:
        prefix = match.group("prefix")
        numbers = match.group("numbers")
    else:
        prefix, numbers = "", version_str

    parts = numbers.split(".")
    if len(parts) < 3:
        raise ValueError("Version must have at least major.minor.patch")

    major, minor, patch = parts[:3]
    build = parts[3] if len(parts) > 3 else None

    project_build_prefix = (
        "build" if project_config.project__custom__ado_build is None else prefix
    )

    if prefix and build:
        semver = f"{major}.{minor}.{patch}-{project_build_prefix}.{build}"
    elif prefix:
        semver = f"{major}.{minor}.{patch}-{prefix}"
    elif build:
        semver = f"{major}.{minor}.{patch}-{project_build_prefix}.{build}"
    else:
        semver = f"{major}.{minor}.{patch}"

    return semver


def sanitize_path_name(path_name: str) -> str:
    """Sanitizes the branch name to be used in URLs."""

    if path_name.startswith("refs/heads/"):
        path_name = path_name[len("refs/heads/") :]

    return path_name


def get_ado_cci_plus_upgrade_command():
    return PIPX_UPDATE_CMD if "pipx" in CUMULUSCI_PATH.lower() else PIP_UPDATE_CMD
