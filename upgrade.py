#!/usr/bin/env python3
"""
Upgrade script for cumulusci-plus-azure-devops and its dependencies

This script helps users properly upgrade the package and its dependencies
when using pipx, which has limitations with dependency upgrades.
"""

import argparse
import json
import subprocess
import sys


def run_command(cmd, capture_output=True, check=True):
    """Run a command and return the result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=capture_output, text=True, check=check
        )
        return result
    except subprocess.CalledProcessError as e:
        return e


def get_pipx_info():
    """Get information about pipx installation."""
    result = run_command("pipx list --json", check=False)
    if result.returncode != 0:
        print("❌ pipx not found or not working properly")
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("❌ Could not parse pipx list output")
        return None


def detect_installation_method():
    """Detect how the package was installed."""
    pipx_info = get_pipx_info()
    if not pipx_info:
        return None

    venvs = pipx_info.get("venvs", {})

    # Check if installed as main package
    if "cumulusci-plus-azure-devops" in venvs:
        return "main_package"

    # Check if injected into cumulusci-plus
    if "cumulusci-plus" in venvs:
        # Get the venv info for cumulusci-plus
        venv_info = venvs["cumulusci-plus"]
        venv_dir = venv_info.get("pyvenv_cfg", {}).get("home", "").replace("/bin", "")

        if not venv_dir:
            # Fallback: try to find venv directory from metadata
            metadata = venv_info.get("metadata", {})
            if metadata:
                # pipx stores venv path info, let's try to extract it
                try:
                    # Check using pipx run which should use the right environment
                    result = run_command(
                        "pipx run --spec cumulusci-plus python -c \"import pkg_resources; pkgs = [d.project_name for d in pkg_resources.working_set if 'cumulusci-plus-azure-devops' in d.project_name]; print('found' if pkgs else 'not_found')\"",
                        check=False,
                    )
                    if result.returncode == 0 and "found" in result.stdout:
                        return "injected"
                except Exception:
                    pass  # noqa: E722
        else:
            # Use the venv path directly
            try:
                python_exe = f"{venv_dir}/bin/python"
                result = run_command(
                    f"{python_exe} -c \"import pkg_resources; pkgs = [d.project_name for d in pkg_resources.working_set if 'cumulusci-plus-azure-devops' in d.project_name]; print('found' if pkgs else 'not_found')\"",
                    check=False,
                )
                if result.returncode == 0 and "found" in result.stdout:
                    return "injected"
            except Exception:
                pass  # noqa: E722

    return "unknown"


def check_package_installed(package_name):
    """Check if a package is installed via pipx."""
    pipx_info = get_pipx_info()
    if not pipx_info:
        return False

    venvs = pipx_info.get("venvs", {})
    return package_name in venvs


def get_dependency_versions(package_name, installation_method=None):
    """Get versions of dependencies in the pipx environment."""
    print(f"📦 Checking dependency versions for {package_name}...")

    if installation_method == "injected":
        # For injected plugins, check the main cumulusci-plus environment directly
        # Try to get venv path from pipx info
        pipx_info = get_pipx_info()
        if pipx_info and "cumulusci-plus" in pipx_info.get("venvs", {}):
            try:
                # Use a simple approach - find the pipx venv directory
                result = run_command(
                    "pipx list | grep -A 1 'package cumulusci-plus'", check=False
                )
                if result.returncode == 0:
                    # Try common pipx venv locations
                    import os

                    home_dir = os.path.expanduser("~")
                    possible_paths = [
                        f"{home_dir}/.local/pipx/venvs/cumulusci-plus/bin/python",
                        f"{home_dir}/.local/share/pipx/venvs/cumulusci-plus/bin/python",  # Some pipx versions
                    ]

                    for python_path in possible_paths:
                        if os.path.exists(python_path):
                            cmd = f"{python_path} -c \"import pkg_resources; deps = [str(d) for d in pkg_resources.working_set if d.project_name in ['cumulusci-plus', 'cumulusci-plus-azure-devops', 'azure-devops', 'requests', 'humanfriendly', 'distro', 'packaging']]; print('\\n'.join(sorted(deps)))\""
                            result = run_command(cmd, check=False)
                            if result.returncode == 0:
                                return result.stdout.strip().split("\n")
            except Exception:
                pass  # noqa: E722 # pragma: no cover

        # Fallback to pipx run if direct access fails
        cmd = "pipx run --spec cumulusci-plus python -c \"import pkg_resources; deps = [str(d) for d in pkg_resources.working_set if d.project_name in ['cumulusci-plus', 'cumulusci-plus-azure-devops', 'azure-devops', 'requests', 'humanfriendly', 'distro', 'packaging']]; print('\\n'.join(sorted(deps)))\""
    else:
        # For main package installations
        cmd = f"pipx run --spec {package_name} python -c \"import pkg_resources; deps = [str(d) for d in pkg_resources.working_set if d.project_name in ['cumulusci-plus', 'azure-devops', 'requests', 'humanfriendly', 'distro', 'packaging']]; print('\\n'.join(sorted(deps)))\""

    result = run_command(cmd, check=False)
    if result.returncode == 0:
        return result.stdout.strip().split("\n")
    else:
        print("❌ Could not check dependency versions")
        return []


def upgrade_package(package_name, force_reinstall=False, installation_method=None):
    """Upgrade the package based on installation method."""
    print(f"🔄 Upgrading {package_name}...")

    if installation_method == "injected":
        print("📦 Detected: Plugin injected into cumulusci-plus environment")

        if force_reinstall:
            print("🔄 Force reinstalling to ensure dependencies are updated...")
            # Uninstall main package
            print("🗑️  Uninstalling cumulusci-plus...")
            result = run_command("pipx uninstall cumulusci-plus", check=False)

            # Reinstall main package
            print("📦 Reinstalling cumulusci-plus...")
            result = run_command("pipx install cumulusci-plus", capture_output=False)
            if result.returncode != 0:
                print("❌ Failed to reinstall cumulusci-plus!")
                return False

            # Re-inject plugin
            print("💉 Re-injecting plugin...")
            result = run_command(
                f"pipx inject cumulusci-plus {package_name} --include-apps --force",
                capture_output=False,
            )
            if result.returncode == 0:
                print("✅ Force reinstallation completed successfully!")
                return True
        else:
            # Normal upgrade for injected plugin
            print("📦 Upgrading main package...")
            result = run_command("pipx upgrade cumulusci-plus", capture_output=False)
            if result.returncode != 0:
                print("❌ Failed to upgrade main package!")
                return False

            print("💉 Updating injected plugin...")
            result = run_command(
                f"pipx inject cumulusci-plus {package_name} --include-apps --force",
                capture_output=False,
            )
            if result.returncode == 0:
                print("✅ Upgrade completed successfully!")
                return True
            else:
                print("❌ Plugin injection failed!")
                return False

    elif installation_method == "main_package":
        print("📦 Detected: Plugin installed as main package")

        if force_reinstall:
            print("🔄 Force reinstalling to ensure dependencies are updated...")

            # Uninstall first
            print("🗑️  Uninstalling current version...")
            result = run_command(f"pipx uninstall {package_name}", check=False)
            if result.returncode != 0:
                print(f"⚠️  Could not uninstall {package_name}: {result.stderr}")

            # Reinstall
            print("📦 Reinstalling latest version...")
            result = run_command(
                f"pipx install {package_name} --include-deps", capture_output=False
            )
            if result.returncode == 0:
                print("✅ Reinstallation completed successfully!")
                return True
            else:
                print("❌ Reinstallation failed!")
                return False
        else:
            # Try normal upgrade
            result = run_command(f"pipx upgrade {package_name}", capture_output=False)
            if result.returncode == 0:
                print("✅ Upgrade completed successfully!")
                return True
            else:
                print("❌ Upgrade failed!")
                return False
    else:
        print("⚠️  Unknown installation method, using standard upgrade...")
        # Fallback to standard upgrade
        result = run_command(f"pipx upgrade {package_name}", capture_output=False)
        if result.returncode == 0:
            print("✅ Upgrade completed successfully!")
            return True
        else:
            print("❌ Upgrade failed!")
            return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Upgrade cumulusci-plus-azure-devops and its dependencies"
    )
    parser.add_argument(
        "--force-reinstall",
        action="store_true",
        help="Force reinstall to ensure dependencies are updated",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check current versions, don't upgrade",
    )

    args = parser.parse_args()

    package_name = "cumulusci-plus-azure-devops"

    print("🚀 CumulusCI Plus Azure DevOps Upgrade Tool")
    print("=" * 50)

    # Detect installation method
    print("🔍 Detecting installation method...")
    installation_method = detect_installation_method()

    if installation_method == "injected":
        print("✅ Detected: Plugin injected into cumulusci-plus environment")
        print("   This is the recommended installation method!")
    elif installation_method == "main_package":
        print("✅ Detected: Plugin installed as main package with --include-deps")
    elif installation_method == "unknown":
        print("⚠️  Could not determine installation method")
        print("   Will attempt standard upgrade...")
    else:
        print(f"❌ {package_name} does not appear to be installed via pipx")
        print("   Available installation methods:")
        print(
            "   1. pipx install cumulusci-plus && pipx inject cumulusci-plus cumulusci-plus-azure-devops --include-apps"
        )
        print("   2. pipx install cumulusci-plus-azure-devops --include-deps")
        sys.exit(1)

    # Show current dependency versions
    print("\n📊 Current dependency versions:")
    deps = get_dependency_versions(package_name, installation_method)
    for dep in deps:
        if dep.strip():
            print(f"   {dep}")

    if args.check_only:
        print("\n✅ Version check completed!")
        return

    print("\n🔄 Upgrade Options:")
    if installation_method == "injected":
        print("1. Normal upgrade (upgrade main app + update plugin)")
        print("2. Force reinstall (reinstall main app + re-inject plugin)")
    else:
        print("1. Normal upgrade (recommended)")
        print("2. Force reinstall (if dependencies seem outdated)")

    if not args.force_reinstall:
        try:
            choice = input("\nChoose option (1 or 2): ").strip()
            if choice == "2":
                args.force_reinstall = True
        except KeyboardInterrupt:
            print("\n❌ Upgrade cancelled by user")
            sys.exit(1)

    # Perform upgrade
    success = upgrade_package(package_name, args.force_reinstall, installation_method)

    if success:
        print("\n🎉 Upgrade completed!")
        print("\n📊 Updated dependency versions:")
        deps = get_dependency_versions(package_name, installation_method)
        for dep in deps:
            if dep.strip():
                print(f"   {dep}")

        print("\n✅ Test your installation:")
        if installation_method == "injected":
            print("   cci --version")
            print("   cumulusci-ado status")
        else:
            print("   cumulusci-ado status")
        print("   cci-ado version")
    else:
        print("\n❌ Upgrade failed!")
        print("💡 Try running with --force-reinstall option")
        sys.exit(1)


if __name__ == "__main__":
    main()
