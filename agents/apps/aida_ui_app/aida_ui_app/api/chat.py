import frappe
from frappe import _
import requests
import json

@frappe.whitelist()
def init_session():
    """Initialize a new chat session with the Aida AI agent"""
    try:
        # Get configuration from site_config.json
        aida_api_url = frappe.conf.get('aida_api_url', 'http://localhost:5000')
        google_api_key = frappe.conf.get('google_maps_api_key', '')
        
        # Initialize session with the Aida AI agent
        response = requests.post(f"{aida_api_url}/init_session", json={
            'erpnext_url': frappe.utils.get_url(),
            'username': frappe.session.user,
            'password': 'session_token',
            'api_key': frappe.session.user,
            'api_secret': frappe.session.sid,
            'google_api_key': google_api_key
        })
        
        if response.status_code == 200:
            data = response.json()
            return {
                'session_id': data.get('session_id'),
                'message': data.get('message')
            }
        else:
            frappe.log_error(f"Failed to initialize Aida AI session: {response.text}", "Aida AI Error")
            return {'error': _("Failed to initialize AI assistant")}
            
    except Exception as e:
        frappe.log_error(f"Error initializing Aida AI session: {str(e)}", "Aida AI Error")
        return {'error': _("Could not connect to AI assistant")}

@frappe.whitelist()
def send_message(session_id, message):
    """Send a message to the Aida AI agent"""
    try:
        if not session_id or not message:
            return {'error': _("Invalid request parameters")}
            
        aida_api_url = frappe.conf.get('aida_api_url', 'http://localhost:5000')
        
        # Send message to Aida AI agent
        response = requests.post(f"{aida_api_url}/chat", json={
            'session_id': session_id,
            'message': message
        })
        
        if response.status_code == 200:
            data = response.json()
            return {
                'response': data.get('response'),
                'context': data.get('context')
            }
        else:
            frappe.log_error(f"Failed to get response from Aida AI: {response.text}", "Aida AI Error")
            return {'error': _("Failed to get response from AI assistant")}
            
    except Exception as e:
        frappe.log_error(f"Error sending message to Aida AI: {str(e)}", "Aida AI Error")
        return {'error': _("Could not communicate with AI assistant")} 