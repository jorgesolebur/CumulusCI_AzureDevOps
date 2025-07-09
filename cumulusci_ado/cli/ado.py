#!/usr/bin/env python3
"""
Command Line Interface for CumulusCI Plus Azure DevOps Plugin
"""

import argparse
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

from cumulusci_ado.__about__ import __version__


def run_command(cmd, capture_output=True, check=True):
    """Run a command and return the result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=capture_output, text=True, check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        return e


def get_version():
    """Get the version of the package."""
    try:
        return version("cumulusci-plus-azure-devops")
    except PackageNotFoundError:
        return __version__


def check_plugin_status():
    """Check if the plugin is properly installed and configured."""
    print("🔍 Checking CumulusCI Plus Azure DevOps Plugin Status...")
    print(f"📦 Package Version: {get_version()}")

    try:
        """Check if CCI Plus is available."""
        result = run_command("cci --version", check=False)
        if result.returncode != 0:
            print("❌ CumulusCI Plus is not installed or not available in PATH")
            print("Please install CumulusCI Plus first:")
            print("  pip install cumulusci-plus")
            return False

        if result.stdout.strip().find("CumulusCI Plus version:") == -1:
            print("❌ CumulusCI Plus version not found")
            return False

        print("✅ CumulusCI Plus is available.")

        if result.stdout.strip().find("CumulusCI Plus Azure DevOps:") == -1:
            print("❌ CumulusCI Plus Azure DevOps integration not found")
            return False

        print("✅ CumulusCI Plus Azure DevOps integration is available.")

    except ImportError as e:
        print(f"❌ Failed to import plugin package: {e}")
        return False

    print("\n🎉 Plugin is properly installed and ready to use!")
    print("\n📚 Next steps:")
    print("1. Configure your CumulusCI project to use this plugin")
    print("2. Add Azure DevOps service configuration")
    print("3. Use CumulusCI commands with Azure DevOps integration")

    return True


def show_help():
    """Show help information."""
    print("CumulusCI Plus Azure DevOps Plugin CLI")
    print("=" * 40)
    print()
    print("This is a plugin for CumulusCI Plus that adds Azure DevOps integration.")
    print(
        "It's not a standalone application, but extends CumulusCI Plus's functionality."
    )
    print()
    print("Commands:")
    print("  status    - Check plugin installation status")
    print("  version   - Show version information")
    print("  help      - Show this help message")
    print()
    print("Usage with CumulusCI Plus:")
    print("  This plugin integrates with CumulusCI Plus automatically when installed.")
    print("  Use standard CumulusCI Plus commands with Azure DevOps repositories.")
    print()
    print("Configuration:")
    print("  Add to your cumulusci.yml:")
    print("    plugins:")
    print("      azure_devops:")
    print("        config:")
    print("          feed_name: New Project Feed")
    print()
    print("For more information, see the documentation.")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CumulusCI Plus Azure DevOps Plugin CLI", prog="cumulusci-ado"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["status", "version", "help"],
        default="help",
        help="Command to run",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cumulusci-plus-azure-devops {get_version()}",
    )

    if len(sys.argv) == 1:
        show_help()
        return

    args = parser.parse_args()

    if args.command == "status":
        success = check_plugin_status()
        sys.exit(0 if success else 1)
    elif args.command == "version":
        print(f"cumulusci-plus-azure-devops {get_version()}")
    elif args.command == "help":
        show_help()
    else:
        show_help()


if __name__ == "__main__":
    main()
