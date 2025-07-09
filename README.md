# cumulusci-azure-devops

A plugin for [CumulusCI](https://github.com/SFDO-Tooling/CumulusCI) supporting Azure DevOps integration.

## Features

- Integrates Azure DevOps pipelines with CumulusCI
- Provides tasks and utilities for Azure DevOps automation

## Installation

### Recommended Installation (with conflict checking)

For the best experience, use our custom installation script that checks for conflicts:

```bash
# Download and run the installation script
curl -O https://raw.githubusercontent.com/jorgesolebur/CumulusCI_AzureDevOps/main/install.py
python install.py
```

### Manual Installation via pipx (recommended)

**Option 1: Install main app, then inject plugin (recommended)**

```bash
# Install cumulusci-plus first to get cci and snowfakery commands
pipx install cumulusci-plus

# Inject the plugin into the same environment for proper plugin discovery
pipx inject cumulusci-plus cumulusci-plus-azure-devops --include-apps
```

**Option 2: Single installation with dependency scripts**

```bash
# Install with --include-deps to expose dependency console scripts
pipx install cumulusci-plus-azure-devops --include-deps
```

**Option 3: Basic installation (Azure DevOps plugin only)**

```bash
pipx install cumulusci-plus-azure-devops
```

> **Why Option 1 is recommended**: CumulusCI plugins need to be in the same environment as the main application for proper plugin discovery. Using `pipx inject` ensures the plugin is installed in the same virtual environment as `cumulusci-plus`.

### Manual Installation via pip

```bash
pip install cumulusci-plus-azure-devops
```

## Upgrading

### Recommended Upgrade Method

Use our custom upgrade script for the best experience:

```bash
# Download and run the upgrade script
curl -O https://raw.githubusercontent.com/jorgesolebur/CumulusCI_AzureDevOps/main/upgrade.py
python upgrade.py
```

### Manual Upgrade via pipx

**For injected installations (recommended method):**

```bash
# Upgrade main package and update plugin
pipx upgrade cumulusci-plus
pipx inject cumulusci-plus cumulusci-plus-azure-devops --include-apps --force

# Or force reinstall everything
pipx uninstall cumulusci-plus
pipx install cumulusci-plus
pipx inject cumulusci-plus cumulusci-plus-azure-devops --include-apps
```

**For --include-deps installations:**

```bash
# Standard upgrade
pipx upgrade cumulusci-plus-azure-devops

# Or force reinstall
pipx uninstall cumulusci-plus-azure-devops
pipx install cumulusci-plus-azure-devops --include-deps
```

### Why Use the Upgrade Script?

The upgrade process varies depending on how you installed the package. Our upgrade script:

- ✅ **Detects installation method** (injected vs main package vs unknown)
- ✅ **Applies correct upgrade process** for each method
- ✅ **Handles plugin discovery** properly for injected installations
- ✅ **Checks current dependency versions** before and after upgrade
- ✅ **Provides installation-specific guidance** and next steps
- ✅ **Ensures dependencies are updated** to latest versions

**Installation Method Detection:**

- **Injected**: Plugin installed via `pipx inject` (recommended) ✅
- **Main Package**: Plugin installed via `--include-deps` ✅
- **Unknown**: Fallback for edge cases ⚠️

### Important Notes

- **Plugin Discovery**: For CumulusCI to discover and use this plugin, it must be installed in the same environment as `cumulusci-plus`. This is why Option 1 (using `pipx inject`) is recommended.
- **Conflict Warning**: This package is designed to work with `cumulusci-plus` (version 5.0.0+), not the original `cumulusci` package. Having both installed may cause conflicts.
- **pipx vs pip**: We recommend using `pipx` for CLI tools as it provides better isolation and prevents dependency conflicts.
- **Dependencies**: All required dependencies (including `cumulusci-plus`, `azure-devops`, etc.) are automatically installed.
- **Console Scripts**: After installation, you'll have access to these commands:
  - `cci` and `snowfakery` (from cumulusci-plus)
  - `cumulusci-ado` and `cci-ado` (from this plugin)

### Available Console Scripts

**From cumulusci-plus:**

- `cci` - Main CumulusCI command
- `snowfakery` - Data generation tool

**From this plugin:**

- `cumulusci-ado` - Azure DevOps plugin CLI
- `cci-ado` - Short alias for plugin CLI

### Plugin Discovery

**✅ Plugin is discoverable when:**

- Installed via `pipx inject` (Option 1)
- Installed via `--include-deps` (Option 2)

**❌ Plugin is NOT discoverable when:**

- Installed as separate pipx packages
- Main app and plugin are in different environments

When the plugin is properly discoverable, you can use it in your `cumulusci.yml` configuration and CumulusCI will automatically find and load it.

## Usage

### Plugin Management Commands

After installation, you can use these commands to manage the plugin:

```bash
# Check plugin installation status
cumulusci-ado status

# Show version information
cumulusci-ado version

# Get help
cumulusci-ado help

# Short alias versions
cci-ado status
cci-ado version
cci-ado help
```

### CumulusCI Integration

Add the plugin to your `cumulusci.yml`:

```yaml
plugins:
  azure_devops:
    path: cumulusci_ado
```

Or use via the CumulusCI CLI if installed as a package. The plugin will automatically integrate with CumulusCI when installed.

## Development

- Requires Python 3.11+
- See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines (if available)

## License

GNU General Public License v3.0

## Links

- [CumulusCI](https://github.com/SFDO-Tooling/CumulusCI)
- [Azure DevOps Python API](https://github.com/microsoft/azure-devops-python-api)
