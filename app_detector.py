"""
Frappe Site Apps Detector Module

This module detects all installed apps on a Frappe site using FrappeClient
with username/password authentication.
"""

import json
import logging
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
from frappeclient import FrappeClient


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
    """
    Detects installed apps on a Frappe site using various methods
    since frappe.get_installed_apps is not available.
    """
    
    def __init__(self, site_url: str, username: str, password: str):
        """
        Initialize the apps detector.
        
        Args:
            site_url: The Frappe site URL (e.g., 'https://yoursite.frappe.cloud')
            username: Username for authentication
            password: Password for authentication
        """
        self.site_url = site_url.rstrip('/')
        self.username = username
        self.password = password
        self.client = None
        self.logger = logging.getLogger(__name__)
        
    def connect(self) -> bool:
        """
        Establish connection to the Frappe site.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.client = FrappeClient(self.site_url, self.username, self.password)
            # Test connection by making a simple API call
            self.client.get_api("frappe.auth.get_logged_user")
            self.logger.info(f"Successfully connected to {self.site_url}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to {self.site_url}: {str(e)}")
            return False
    
    def get_installed_apps(self) -> List[FrappeApp]:
        """
        Get all installed apps using multiple detection methods.
        
        Returns:
            List[FrappeApp]: List of installed Frappe applications
        """
        if not self.client:
            if not self.connect():
                raise ConnectionError("Failed to connect to Frappe site")
        
        apps = []
        
        # Method 1: Try to get apps from Module Def doctype
        try:
            apps_from_modules = self._get_apps_from_modules()
            if apps_from_modules:
                apps.extend(apps_from_modules)
        except Exception as e:
            self.logger.warning(f"Failed to get apps from modules: {str(e)}")
        
        # Method 2: Try to get apps from Desktop Icon doctype
        try:
            apps_from_desktop = self._get_apps_from_desktop_icons()
            if apps_from_desktop:
                # Merge with existing apps, avoid duplicates
                existing_names = {app.name for app in apps}
                for app in apps_from_desktop:
                    if app.name not in existing_names:
                        apps.append(app)
        except Exception as e:
            self.logger.warning(f"Failed to get apps from desktop icons: {str(e)}")
        
        # Method 3: Try to get apps from DocType list
        try:
            apps_from_doctypes = self._get_apps_from_doctypes()
            if apps_from_doctypes:
                existing_names = {app.name for app in apps}
                for app in apps_from_doctypes:
                    if app.name not in existing_names:
                        apps.append(app)
        except Exception as e:
            self.logger.warning(f"Failed to get apps from doctypes: {str(e)}")
        
        # Method 4: Try system settings or version info
        try:
            apps_from_system = self._get_apps_from_system_info()
            if apps_from_system:
                existing_names = {app.name for app in apps}
                for app in apps_from_system:
                    if app.name not in existing_names:
                        apps.append(app)
                    else:
                        # Update version info for existing apps
                        for existing_app in apps:
                            if existing_app.name == app.name and existing_app.version == "Unknown":
                                existing_app.version = app.version
                                existing_app.description = app.description
        except Exception as e:
            self.logger.warning(f"Failed to get apps from system info: {str(e)}")
        
        # Method 5: Try to get version from frappe.get_version
        try:
            self._update_versions(apps)
        except Exception as e:
            self.logger.warning(f"Failed to update versions: {str(e)}")
        
        # Remove duplicates and sort
        unique_apps = self._remove_duplicates(apps)
        return sorted(unique_apps, key=lambda x: x.name)
    
    def _get_apps_from_modules(self) -> List[FrappeApp]:
        """Get apps from Module Def doctype."""
        try:
            modules = self.client.get_list("Module Def", 
                                         fields=["name", "app_name", "custom"],
                                         limit_page_length=0)
            
            apps = []
            app_names = set()
            
            for module in modules:
                app_name = module.get("app_name", "").strip()
                if app_name and app_name not in app_names:
                    app_names.add(app_name)
                    apps.append(FrappeApp(
                        name=app_name,
                        title=app_name.replace("_", " ").title(),
                        version="Unknown",
                        is_custom=bool(module.get("custom", 0))
                    ))
            
            return apps
        except Exception as e:
            self.logger.error(f"Error getting apps from modules: {str(e)}")
            return []
    
    def _update_versions(self, apps: List[FrappeApp]) -> None:
        """Try to update version information for apps."""
        for app in apps:
            if app.version == "Unknown":
                try:
                    # Try to get version using frappe.get_version
                    version_info = self.client.get_api("frappe.get_version", {"app": app.name})
                    if version_info:
                        app.version = str(version_info)
                except Exception:
                    try:
                        # Try alternative method
                        version_info = self.client.get_api("frappe.utils.get_version", {"app": app.name})
                        if version_info:
                            app.version = str(version_info)
                    except Exception:
                        pass
    
    def _get_apps_from_desktop_icons(self) -> List[FrappeApp]:
        """Get apps from Desktop Icon doctype."""
        try:
            icons = self.client.get_list("Desktop Icon",
                                       fields=["name", "app", "label", "module_name"],
                                       limit_page_length=0)
            
            apps = []
            app_names = set()
            
            for icon in icons:
                app_name = icon.get("app", "").strip()
                if app_name and app_name not in app_names:
                    app_names.add(app_name)
                    apps.append(FrappeApp(
                        name=app_name,
                        title=icon.get("label", app_name.replace("_", " ").title()),
                        version="Unknown"
                    ))
            
            return apps
        except Exception as e:
            self.logger.error(f"Error getting apps from desktop icons: {str(e)}")
            return []
    
    def _get_apps_from_doctypes(self) -> List[FrappeApp]:
        """Get apps by analyzing DocTypes and their modules."""
        try:
            # First try with app_name field
            try:
                doctypes = self.client.get_list("DocType",
                                              fields=["name", "module", "app_name", "custom"],
                                              limit_page_length=0)
            except Exception:
                # If app_name field is not available, try without it
                doctypes = self.client.get_list("DocType",
                                              fields=["name", "module", "custom"],
                                              limit_page_length=0)
            
            apps = []
            app_names = set()
            
            for doctype in doctypes:
                # First try app_name field if available
                app_name = doctype.get("app_name", "").strip()
                
                # If no app_name, try to infer from module
                if not app_name:
                    module = doctype.get("module", "").strip()
                    if module:
                        # Common module to app mappings
                        app_name = self._infer_app_from_module(module)
                
                if app_name and app_name not in app_names:
                    app_names.add(app_name)
                    apps.append(FrappeApp(
                        name=app_name,
                        title=app_name.replace("_", " ").title(),
                        version="Unknown",
                        is_custom=bool(doctype.get("custom", 0))
                    ))
            
            return apps
        except Exception as e:
            self.logger.warning(f"Could not get apps from doctypes: {str(e)}")
            return []
    
    def _get_apps_from_system_info(self) -> List[FrappeApp]:
        """Get apps from system information or version data."""
        try:
            # Try to get system information
            system_info = self.client.get_api("frappe.utils.change_log.get_versions")
            
            if isinstance(system_info, dict):
                apps = []
                for app_name, version_info in system_info.items():
                    if isinstance(version_info, dict):
                        version = version_info.get("version", "Unknown")
                        title = version_info.get("title", app_name.replace("_", " ").title())
                        description = version_info.get("description", "")
                        
                        apps.append(FrappeApp(
                            name=app_name,
                            title=title,
                            version=version,
                            description=description
                        ))
                
                return apps
                
        except Exception as e:
            self.logger.error(f"Error getting apps from system info: {str(e)}")
        
        return []
    
    def _infer_app_from_module(self, module_name: str) -> str:
        """Infer app name from module name using common patterns."""
        # Add module mappings for contacts app
        module_to_app = {
            "Core": "frappe",
            "Custom": "frappe",
            "Desk": "frappe",
            "Email": "frappe",
            "Integrations": "frappe",
            "Printing": "frappe",
            "Social": "frappe",
            "Website": "frappe",
            "Workflow": "frappe",
            "Accounts": "erpnext",
            "Assets": "erpnext",
            "Buying": "erpnext",
            "CRM": "erpnext",
            "HR": "erpnext",
            "Manufacturing": "erpnext",
            "Projects": "erpnext",
            "Selling": "erpnext",
            "Stock": "erpnext",
            "Support": "erpnext",
            "Utilities": "erpnext",
            "Quality Management": "erpnext",
            "Regional": "erpnext",
            "Healthcare": "healthcare",
            "Education": "education",
            "Agriculture": "agriculture",
            "Non Profit": "non_profit",
            # Ecommerce integrations modules
            "Amazon": "ecommerce_integrations",
            "Ecommerce Integrations": "ecommerce_integrations",
            "shopify": "ecommerce_integrations",
            "unicommerce": "ecommerce_integrations",
            "Zenoti": "ecommerce_integrations",
            # Contacts app modules
            "Contacts": "contacts",
        }
        
        return module_to_app.get(module_name, module_name.lower().replace(" ", "_"))
    
    def _remove_duplicates(self, apps: List[FrappeApp]) -> List[FrappeApp]:
        """Remove duplicate apps based on name."""
        seen = set()
        unique_apps = []
        
        for app in apps:
            if app.name not in seen:
                seen.add(app.name)
                unique_apps.append(app)
        
        return unique_apps
    
    def get_all_doctypes_by_app(self) -> Dict[str, List[str]]:
        """
        Get all doctypes grouped by their parent app.
        
        Returns:
            Dict[str, List[str]]: Dictionary mapping app names to their doctypes
        """
        if not self.client:
            if not self.connect():
                return {}
        
        try:
            # Get all doctypes with their modules
            all_doctypes = self.client.get_list("DocType",
                                              fields=["name", "module", "custom", "istable"],
                                              limit_page_length=0)
            
            # Group doctypes by app
            doctypes_by_app = {}
            
            for doctype in all_doctypes:
                module = doctype.get("module", "").strip()
                if module:
                    # Infer app from module
                    app_name = self._infer_app_from_module(module)
                    
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
            self.logger.error(f"Error getting doctypes by app: {str(e)}")
            return {}
    
    def get_doctypes_for_app(self, app_name: str) -> List[Dict]:
        """
        Get all doctypes for a specific app with detailed information.
        
        Args:
            app_name: Name of the app
            
        Returns:
            List[Dict]: List of doctype information
        """
        if not self.client:
            if not self.connect():
                return []
        
        try:
            # Get all doctypes
            all_doctypes = self.client.get_list("DocType",
                                              fields=["name", "module", "custom", "istable", "editable_grid", "is_submittable"],
                                              limit_page_length=0)
            
            app_doctypes = []
            
            for doctype in all_doctypes:
                module = doctype.get("module", "").strip()
                if module:
                    # Check if this doctype belongs to the requested app
                    inferred_app = self._infer_app_from_module(module)
                    
                    if inferred_app == app_name:
                        doctype_info = {
                            "name": doctype.get("name", ""),
                            "module": module,
                            "is_custom": bool(doctype.get("custom", 0)),
                            "is_table": bool(doctype.get("istable", 0)),
                            "editable_grid": bool(doctype.get("editable_grid", 0)),
                            "is_submittable": bool(doctype.get("is_submittable", 0))
                        }
                        app_doctypes.append(doctype_info)
            
            return sorted(app_doctypes, key=lambda x: x["name"])
            
        except Exception as e:
            self.logger.error(f"Error getting doctypes for app {app_name}: {str(e)}")
            return []
    def get_app_details(self, app_name: str) -> Optional[Dict]:
        """
        Get detailed information about a specific app.
        
        Args:
            app_name: Name of the app to get details for
            
        Returns:
            Dict: App details or None if not found
        """
        if not self.client:
            if not self.connect():
                return None
        
        try:
            # Get comprehensive app info
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
            try:
                modules = self.client.get_list("Module Def",
                                             filters={"app_name": app_name},
                                             fields=["name", "custom"],
                                             limit_page_length=0)
                app_info["modules"] = [m["name"] for m in modules]
            except Exception:
                # If direct filtering fails, get all modules and filter manually
                try:
                    all_modules = self.client.get_list("Module Def",
                                                     fields=["name", "app_name", "custom"],
                                                     limit_page_length=0)
                    app_info["modules"] = [m["name"] for m in all_modules 
                                         if m.get("app_name") == app_name]
                except Exception:
                    pass
            
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
            self.logger.error(f"Error getting details for app {app_name}: {str(e)}")
            return None
    
    def print_apps_summary(self, apps: List[FrappeApp]) -> None:
        """Print a formatted summary of installed apps."""
        print(f"\n{'='*60}")
        print(f"INSTALLED APPS ON {self.site_url}")
        print(f"{'='*60}")
        print(f"Total Apps Found: {len(apps)}")
        print(f"{'='*60}")
        
        for i, app in enumerate(apps, 1):
            print(f"{i:2d}. {app.name}")
            print(f"    Title: {app.title}")
            print(f"    Version: {app.version}")
            if app.description:
                print(f"    Description: {app.description}")
            if app.is_custom:
                print(f"    Type: Custom App")
            print()
    
    def print_detailed_app_info(self, app_name: str) -> None:
        """Print detailed information about a specific app."""
        details = self.get_app_details(app_name)
        if not details:
            print(f"Could not get details for app: {app_name}")
            return
        
        print(f"\n{'='*80}")
        print(f"DETAILED INFO FOR APP: {app_name.upper()}")
        print(f"{'='*80}")
        print(f"Version: {details['version']}")
        print(f"Total DocTypes: {details['doctype_count']}")
        print(f"Custom DocTypes: {details['custom_doctype_count']}")
        print(f"Table DocTypes: {details['table_doctype_count']}")
        print(f"Submittable DocTypes: {details['submittable_doctype_count']}")
        
        if details['modules']:
            print(f"\nModules ({len(details['modules'])}):")
            for module in details['modules']:
                print(f"  - {module}")
        
        if details['doctypes']:
            print(f"\nDocTypes ({len(details['doctypes'])}):")
            for doctype in details['doctypes']:
                print(f"  - {doctype}")
        
        print(f"{'='*80}")
    
    def print_all_doctypes_by_app(self) -> None:
        """Print all doctypes grouped by their parent app."""
        doctypes_by_app = self.get_all_doctypes_by_app()
        
        if not doctypes_by_app:
            print("Could not retrieve doctypes by app")
            return
        
        print(f"\n{'='*80}")
        print("ALL DOCTYPES BY APP")
        print(f"{'='*80}")
        
        for app_name, doctypes in doctypes_by_app.items():
            print(f"\n{app_name.upper()} ({len(doctypes)} doctypes):")
            print("-" * 50)
            
            # Group by categories
            regular_doctypes = [dt for dt in doctypes if not dt["is_custom"] and not dt["is_table"]]
            custom_doctypes = [dt for dt in doctypes if dt["is_custom"]]
            table_doctypes = [dt for dt in doctypes if dt["is_table"]]
            
            if regular_doctypes:
                print(f"  Regular DocTypes ({len(regular_doctypes)}):")
                for dt in regular_doctypes:
                    print(f"    - {dt['name']} (Module: {dt['module']})")
            
            if custom_doctypes:
                print(f"  Custom DocTypes ({len(custom_doctypes)}):")
                for dt in custom_doctypes:
                    print(f"    - {dt['name']} (Module: {dt['module']})")
            
            if table_doctypes:
                print(f"  Table DocTypes ({len(table_doctypes)}):")
                for dt in table_doctypes:
                    print(f"    - {dt['name']} (Module: {dt['module']})")
        
        print(f"\n{'='*80}")
    
    def export_doctypes_to_json(self, filename: str = "frappe_doctypes.json") -> bool:
        """Export all doctypes by app to a JSON file."""
        try:
            doctypes_by_app = self.get_all_doctypes_by_app()
            
            if not doctypes_by_app:
                print("No doctypes found to export")
                return False
            
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(doctypes_by_app, f, indent=2, ensure_ascii=False)
            
            print(f"DocTypes exported successfully to {filename}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error exporting doctypes: {str(e)}")
            return False


def main():
    """Example usage of the FrappeAppsDetector."""
    # Example configuration
    SITE_URL = "http://46.62.138.17:8000/"
    USERNAME = "Administrator"
    PASSWORD = "admin"
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    # Create detector instance
    detector = FrappeAppsDetector(SITE_URL, USERNAME, PASSWORD)
    
    try:
        # Get installed apps
        print("Detecting installed apps...")
        apps = detector.get_installed_apps()
        
        # Print summary
        detector.print_apps_summary(apps)
        
        # Print all doctypes by app
        print("\nGetting all doctypes by app...")
        detector.print_all_doctypes_by_app()
        
        # Export to JSON
        detector.export_doctypes_to_json()
        
        # Get detailed info for each app
        print("\nDetailed app information:")
        for app in apps:
            detector.print_detailed_app_info(app.name)
        
    except Exception as e:
        print(f"Error: {str(e)}")


if __name__ == "__main__":
    main()