import frappe

def has_app_permission():
    """Check if user has permission to access Aida AI app"""
    # Allow access to System Manager and users with the 'Aida AI User' role
    user = frappe.session.user
    if user == 'Administrator' or 'System Manager' in frappe.get_roles(user):
        return True
        
    return 'Aida AI User' in frappe.get_roles(user) 