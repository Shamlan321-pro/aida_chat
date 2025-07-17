import frappe
from frappe import _
from aida_ui_app.api.permission import has_app_permission

def get_context(context):
    if not has_app_permission():
        raise frappe.PermissionError(_("You don't have permission to access this page"))
    
    context.no_cache = 1
    context.title = _("Aida AI")
    
    # Add any additional context needed for the page
    context.api_base_url = frappe.conf.get('aida_api_url', 'http://localhost:5000')
    context.google_maps_api_key = frappe.conf.get('google_maps_api_key', '')
    
    # Get user info
    context.user = frappe.session.user
    context.user_roles = frappe.get_roles()
    
    return context 