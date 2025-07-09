from importlib.metadata import PackageNotFoundError, version

from cumulusci.cli.utils import check_latest_version, parse_version
from cumulusci.core.utils import import_global
from cumulusci.plugins.plugin_base import PluginBase
from cumulusci.vcs.vcs_source import VCSSource

from cumulusci_ado import __version__
from cumulusci_ado.utils.ado import get_ado_cci_plus_upgrade_command

import_global("cumulusci_ado.vcs.ado.service.AzureDevOpsService")

VCSSource.register(
    "azure_devops", "cumulusci_ado.vcs.ado.source.azure_devops.ADOSource"
)

from cumulusci_ado.vcs.ado.dependencies.ado_dependencies import VCS_ADO as dep
from cumulusci_ado.vcs.ado.dependencies.ado_resolvers import VCS_ADO as res

assert (
    dep == res
), "VCS_ADO must match in dependencies and resolvers. (Assertion done to load the ado dependencies and resolvers correctly)"


class AzureDevOpsPlugin(PluginBase):
    """Plugin for Azure DevOps integration."""

    plugin_config_file = "cumulusci_plugin.yml"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._api_name = "cumulusci-plus-azure-devops"

    @property
    def version(self) -> str:
        """Returns the version of the plugin."""
        try:
            return version(self._api_name)
        except PackageNotFoundError:
            return "0.0.0"

    def initialize(self) -> None:
        """Initialize the plugin."""
        super().initialize()

    def teardown(self) -> None:
        """Tear down the plugin."""
        super().teardown()

    def check_latest_version(self):
        """Override this method to check for the latest version of the plugin."""
        check_latest_version(
            pkg=self._api_name,
            installed_version=parse_version(__version__),
            tstamp_file="cumulus_ado_timestamp",
            message=f"An update to {self.name} is available. To install the update, run this command: {get_ado_cci_plus_upgrade_command()}",
        )
