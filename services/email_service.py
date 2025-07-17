import os
import logging
from typing import Optional
from frappeclient import FrappeClient

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.frappe_url = os.getenv('ERPNEXT_URL')
        self.username = os.getenv('ERPNEXT_USERNAME')  
        self.password = os.getenv('ERPNEXT_PASSWORD')
        self.from_name = os.getenv('FROM_NAME', 'Business Development')
        self.client = None
        
        if not self.frappe_url or not self.username or not self.password:
            logger.warning("Frappe credentials not configured. Emails will be simulated.")
        else:
            try:
                # Use exact same initialization as your working send_mail.py
                self.client = FrappeClient(self.frappe_url, self.username, self.password)
                logger.info("Email service initialized with Frappe client")
            except Exception as e:
                logger.error(f"Failed to initialize Frappe client: {str(e)}")
                self.client = None
    
    def send_email(self, to_email: str, subject: str, body: str, 
                   html_body: Optional[str] = None) -> bool:
        """Send email to recipient using Frappe's email system"""
        
        # If credentials not configured, simulate sending
        if self.client is None:
            logger.info(f"SIMULATED EMAIL SEND to {to_email}")
            logger.info(f"Subject: {subject}")
            logger.info(f"Body: {body[:100]}...")
            return True
        
        try:
            # Use HTML body if provided, otherwise convert text to HTML
            email_content = html_body if html_body else self._text_to_html(body)
            
            # Use exact same format as your working send_mail.py
            email_data = {
                "recipients": to_email,
                "subject": subject,
                "content": email_content,
                "as_html": True,
                "communication_medium": "Email",
                "send_email": 1
            }
            
            # Send email via Frappe API using exact same method
            response = self.client.post_api("frappe.core.doctype.communication.email.make", email_data)
            
            logger.info(f"Email sent successfully to {to_email}")
            logger.debug(f"Frappe response: {response}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False
    
    def _text_to_html(self, text: str) -> str:
        """Convert plain text to HTML format"""
        # Replace line breaks with HTML breaks
        html_text = text.replace('\n\n', '</p><p>').replace('\n', '<br>')
        return f"<p>{html_text}</p>"
    
    def validate_email(self, email: str) -> bool:
        """Basic email validation"""
        import re
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    def send_bulk_emails(self, recipients: list, subject: str, body: str) -> dict:
        """Send emails to multiple recipients"""
        results = {
            'sent': 0,
            'failed': 0,
            'errors': []
        }
        
        for recipient in recipients:
            if self.validate_email(recipient):
                if self.send_email(recipient, subject, body):
                    results['sent'] += 1
                else:
                    results['failed'] += 1
                    results['errors'].append(f"Failed to send to {recipient}")
            else:
                results['failed'] += 1
                results['errors'].append(f"Invalid email: {recipient}")
        
        return results
