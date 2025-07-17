import json
import logging
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
from frappeclient import FrappeClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class FrappeApp:
    """Represents a Frappe application with its details."""
    name: str
    title: str
    version: str
    description: str = ""
    author: str = ""
    is_custom: bool = False

class FrappeAppsDetector:
    def __init__(self, site_url: str, username: str, password: str):
        self.site_url = site_url
        self.username = username
        self.password = password
        self.client = None
        self.logger = logger

    def connect(self) -> bool:
        try:
            self.client = FrappeClient(self.site_url, self.username, self.password)
            logger.info(f"Successfully connected to {self.site_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Frappe site: {str(e)}")
            return False

    def _get_all_doctypes_paginated(self) -> List[Dict]:
        """Get all doctypes using pagination."""
        all_doctypes = []
        start = 0
        page_size = 200
        while True:
            doctypes_page = self.client.get_list(
                "DocType",
                fields=["name", "module", "app_name", "custom", "istable"],
                limit_start=start,
                limit_page_length=page_size
            )
            if not doctypes_page:
                break
            all_doctypes.extend(doctypes_page)
            start += page_size
        return all_doctypes

    def _get_module_app_mapping(self) -> Dict[str, str]:
        """Fetch accurate module-to-app mapping from Module Def."""
        try:
            modules = self.client.get_list(
                "Module Def",
                fields=["name", "app_name"],
                limit_page_length=0
            )
            return {m["name"]: m["app_name"] for m in modules if m.get("app_name")}
        except Exception as e:
            logger.warning(f"Failed to fetch Module Def records: {str(e)}")
            return {}

    def _infer_app_from_module(self, module_name: str, module_to_app_map: Dict[str, str]) -> str:
        """Infer app name from module name using the accurate mapping."""
        return module_to_app_map.get(module_name, module_name.lower().replace(" ", "_"))

    def _get_apps_from_doctypes(self) -> List[FrappeApp]:
        """Get apps by analyzing DocTypes and their modules."""
        try:
            # First try with app_name field
            doctypes = self.client.get_list(
                "DocType",
                fields=["name", "module", "app_name", "custom"],
                limit_page_length=0
            )
        except Exception:
            # If app_name field is not available, try without it
            doctypes = self.client.get_list(
                "DocType",
                fields=["name", "module", "custom"],
                limit_page_length=0
            )

        apps = []
        app_names = set()
        module_to_app_map = self._get_module_app_mapping()

        for doctype in doctypes:
            app_name = doctype.get("app_name", "").strip()
            if not app_name:
                module = doctype.get("module", "").strip()
                if module:
                    app_name = self._infer_app_from_module(module, module_to_app_map)

            if app_name and app_name not in app_names:
                app_names.add(app_name)
                apps.append(FrappeApp(
                    name=app_name,
                    title=app_name.replace("_", " ").title(),
                    version="Unknown",
                    is_custom=bool(doctype.get("custom", 0))
                ))

        return apps

    def _get_all_doctypes_by_app(self) -> Dict[str, List[Dict]]:
        """Get all doctypes grouped by their parent app."""
        if not self.client:
            if not self.connect():
                return {}

        try:
            # Fetch all doctypes with their modules
            all_doctypes = self._get_all_doctypes_paginated()

            # Get accurate module-to-app mapping
            module_to_app_map = self._get_module_app_mapping()

            # Group doctypes by app
            doctypes_by_app = {}
            for doctype in all_doctypes:
                module = doctype.get("module", "").strip()
                if module:
                    # Infer app from module
                    app_name = self._infer_app_from_module(module, module_to_app_map)
                    if app_name not in doctypes_by_app:
                        doctypes_by_app[app_name] = []

                    doctype_info = {
                        "name": doctype.get("name", ""),
                        "module": module,
                        "is_custom": bool(doctype.get("custom", 0)),
                        "is_table": bool(doctype.get("istable", 0))
                    }
                    doctypes_by_app[app_name].append(doctype_info)

            # Sort doctypes within each app
            for app_name in doctypes_by_app:
                doctypes_by_app[app_name].sort(key=lambda x: x["name"])

            return doctypes_by_app
        except Exception as e:
            logger.error(f"Error getting doctypes by app: {str(e)}")
            return {}

    def print_apps_summary(self, apps: List[FrappeApp]) -> None:
        """Print a formatted summary of installed apps."""
        print(f"{'='*60}")
        print(f"INSTALLED APPS ON {self.site_url}")
        print(f"{'='*60}")
        print(f"Total Apps Found: {len(apps)}")
        print(f"{'='*60}")
        for i, app in enumerate(apps, 1):
            print(f"{i:2d}. {app.name}")
            print(f" Title: {app.title}")
            print(f" Version: {app.version}")
            if app.description:
                print(f" Description: {app.description}")
            if app.is_custom:
                print(f" Type: Custom App")
            print()

    def print_detailed_app_info(self, app_name: str) -> None:
        """Print detailed information about a specific app."""
        details = self.get_app_details(app_name)
        if not details:
            print(f"Could not get details for app: {app_name}")
            return

        print(f"{'='*80}")
        print(f"DETAILED INFO FOR APP: {app_name.upper()}")
        print(f"{'='*80}")
        print(f"Version: {details['version']}")
        print(f"Total DocTypes: {details['doctype_count']}")
        print(f"Custom DocTypes: {details['custom_doctype_count']}")
        print(f"Table DocTypes: {details['table_doctype_count']}")
        print(f"Submittable DocTypes: {details['submittable_doctype_count']}")

        if details['modules']:
            print(f"Modules ({len(details['modules'])}):")
            for module in details['modules']:
                print(f" - {module}")

        if details['doctypes']:
            print(f"DocTypes ({len(details['doctypes'])}):")
            # Group doctypes by module for better readability
            doctypes_by_module = {}
            for dt in details['doctypes']:
                module = dt['module']
                if module not in doctypes_by_module:
                    doctypes_by_module[module] = []
                doctypes_by_module[module].append(dt)

            for module, doctypes in sorted(doctypes_by_module.items()):
                print(f"{module} Module:")
                for dt in doctypes:
                    print(f" - {dt['name']} (Module: {dt['module']})")

    def export_doctypes_to_json(self, filename: str = "frappe_doctypes.json") -> bool:
        """Export all doctypes by app to a JSON file."""
        try:
            doctypes_by_app = self.get_all_doctypes_by_app()
            if not doctypes_by_app:
                print("No doctypes found to export")
                return False

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(doctypes_by_app, f, indent=2, ensure_ascii=False)
            print(f"Exported doctypes to {filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to export doctypes: {str(e)}")
            return False

    def get_app_details(self, app_name: str) -> Optional[Dict]:
        """Get detailed information about a specific app."""
        if not self.client:
            if not self.connect():
                return None

        try:
            app_info = {
                "name": app_name,
                "modules": [],
                "doctypes": [],
                "doctype_count": 0,
                "custom_doctype_count": 0,
                "table_doctype_count": 0,
                "submittable_doctype_count": 0,
                "version": "Unknown"
            }

            # Get modules for this app
            modules = self.client.get_list(
                "Module Def",
                filters={"app_name": app_name},
                fields=["name", "custom"],
                limit_page_length=0
            )
            app_info["modules"] = [m["name"] for m in modules]

            # Get detailed doctypes for this app
            doctypes = self.get_doctypes_for_app(app_name)
            app_info["doctypes"] = [dt["name"] for dt in doctypes]
            app_info["doctype_count"] = len(doctypes)
            app_info["custom_doctype_count"] = len([dt for dt in doctypes if dt["is_custom"]])
            app_info["table_doctype_count"] = len([dt for dt in doctypes if dt["is_table"]])
            app_info["submittable_doctype_count"] = len([dt for dt in doctypes if dt["is_submittable"]])

            # Try to get version info
            try:
                version_info = self.client.get_api("frappe.get_version", {"app": app_name})
                if version_info:
                    app_info["version"] = str(version_info)
            except Exception:
                pass

            return app_info
        except Exception as e:
            logger.error(f"Error getting details for app {app_name}: {str(e)}")
            return None

    def get_doctypes_for_app(self, app_name: str) -> List[Dict]:
        """Get all doctypes belonging to a specific app."""
        try:
            doctypes = self.client.get_list(
                "DocType",
                filters={"app_name": app_name},
                fields=["name", "module", "custom", "istable", "is_submittable"],
                limit_page_length=0
            )
            return doctypes
        except Exception as e:
            logger.error(f"Error getting doctypes for app {app_name}: {str(e)}")
            return []

def main():
    # Configuration
    SITE_URL = "http://46.62.138.17:8000"
    USERNAME = "Administrator"
    PASSWORD = "admin"

    detector = FrappeAppsDetector(SITE_URL, USERNAME, PASSWORD)

    # Connect to the Frappe site
    if not detector.connect():
        print("Failed to connect to Frappe site. Exiting.")
        return

    # Method 1: Try to get apps via frappe.get_installed_apps
    try:
        apps_dict = {}
        response = detector.client.get_api("frappe.get_installed_apps")
        if isinstance(response, dict) and 'message' in response:
            apps = response['message']
            for app in apps:
                apps_dict[app] = {
                    "name": app,
                    "title": app.replace("_", " ").title(),
                    "version": "Unknown"
                }
    except Exception as e:
        logger.warning(f"frappe.get_installed_apps API call failed: {str(e)}. Falling back to module analysis.")

    # Method 2: Discover apps via doctype module analysis
    if not apps_dict:
        logger.info("Discovering apps via doctype module analysis (fallback)...")
        apps = detector._get_apps_from_doctypes()
        apps_dict = {app.name: {"name": app.name, "title": app.title, "version": app.version} for app in apps}

    # Print apps summary
    print("Detected Installed Apps:")
    detector.print_apps_summary(list(apps_dict.values()))

    # Export doctypes to JSON
    detector.export_doctypes_to_json()

    # Get detailed info for each app
    print("Detailed app information:")
    for app in apps_dict:
        detector.print_detailed_app_info(app)

if __name__ == "__main__":
    main()