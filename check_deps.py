#!/usr/bin/env python3
"""
Check dependencies manually
"""


def check_dependencies():
    """Check if all required dependencies are installed."""
    print("🔍 Checking Dependencies...")

    dependencies = [
        ("cumulusci-plus", "cumulusci"),
        ("azure-devops", "azure.devops"),
        ("requests", "requests"),
        ("humanfriendly", "humanfriendly"),
        ("distro", "distro"),
        ("packaging", "packaging"),
    ]

    all_good = True
    for dep_name, import_name in dependencies:
        try:
            __import__(import_name)
            print(f"✅ {dep_name} is installed")
        except ImportError:
            print(f"❌ {dep_name} is NOT installed")
            all_good = False

    if all_good:
        print("\n✅ All dependencies are properly installed!")
    else:
        print("\n❌ Some dependencies are missing!")

    return all_good


if __name__ == "__main__":
    check_dependencies()
