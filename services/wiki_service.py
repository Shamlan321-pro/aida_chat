import requests
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class WikiService:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self._authenticate()
    
    def _authenticate(self):
        """Authenticate with ERPNext"""
        try:
            login_url = f"{self.base_url}/api/method/login"
            response = self.session.post(login_url, data={
                'usr': self.username,
                'pwd': self.password
            })
            
            if response.status_code == 200:
                logger.info("Wiki service authenticated successfully")
            else:
                raise Exception(f"Authentication failed: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Wiki authentication error: {str(e)}")
            raise
    
    def get_company_profile(self) -> str:
        """Get company profile from Wiki"""
        try:
            # Try to get from Wiki Page first
            wiki_url = f"{self.base_url}/api/resource/Wiki Page"
            params = {
                'filters': '[["title", "=", "Company Profile"]]',  # Fixed filter syntax
                'fields': '["content", "title"]'
            }
            
            response = self.session.get(wiki_url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('data'):
                    return data['data'][0].get('content', '')
            
            # Fallback to default profile
            return self._get_default_company_profile()
            
        except Exception as e:
            logger.error(f"Error getting company profile: {str(e)}")
            return self._get_default_company_profile()
    
    def get_outreach_template(self) -> str:
        """Get outreach template from Wiki"""
        try:
            wiki_url = f"{self.base_url}/api/resource/Wiki Page"
            params = {
                'filters': '[["title", "=", "Outreach Template"]]',  # Fixed filter syntax
                'fields': '["content", "title"]'
            }
            
            response = self.session.get(wiki_url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('data'):
                    return data['data'][0].get('content', '')
            
            # Fallback to default template
            return self._get_default_outreach_template()
            
        except Exception as e:
            logger.error(f"Error getting outreach template: {str(e)}")
            return self._get_default_outreach_template()
    
    def _get_default_company_profile(self) -> str:
        """Default company profile"""
        return """
        We are a technology consulting company that helps businesses modernize their operations through custom software solutions, automation, and digital transformation services.
        
        Our expertise includes:
        - Custom software development
        - Business process automation
        - CRM and ERP implementations
        - Data analytics and reporting
        - Cloud migration services
        
        We work with mid-market companies looking to scale their operations and improve efficiency through technology.
        """
    
    def _get_default_outreach_template(self) -> str:
        """Default outreach template"""
        return """
        Guidelines for outreach emails:
        
        1. Keep emails concise (under 150 words)
        2. Lead with value proposition relevant to their business
        3. Reference their company specifically
        4. Include a clear, low-commitment call-to-action
        5. Professional yet personable tone
        6. Suggest a brief 15-minute discovery call
        
        Call-to-action options:
        - "Would you be open to a brief 15-minute call to discuss how we might help?"
        - "I'd love to share some insights that might be relevant to [company]. Are you available for a quick call this week?"
        - "Would you be interested in seeing how similar companies in your industry have solved this challenge?"
        
        Avoid:
        - Generic templates
        - Overly sales-y language
        - Long paragraphs
        - Multiple asks in one email
        """
    
    def create_wiki_page(self, title: str, content: str) -> bool:
        """Create a new wiki page"""
        try:
            url = f"{self.base_url}/api/resource/Wiki Page"
            data = {
                'title': title,
                'content': content,
                'published': 1
            }
            
            response = self.session.post(url, json=data)
            
            if response.status_code in [200, 201]:
                logger.info(f"Wiki page '{title}' created successfully")
                return True
            else:
                logger.error(f"Failed to create wiki page: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating wiki page: {str(e)}")
            return False
    
    def update_wiki_page(self, title: str, content: str) -> bool:
        """Update existing wiki page"""
        try:
            # First, get the page name
            wiki_url = f"{self.base_url}/api/resource/Wiki Page"
            params = {
                'filters': f'[["title", "=", "{title}"]]',
                'fields': '["name"]'
            }
            
            response = self.session.get(wiki_url, params=params)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('data'):
                    page_name = data['data'][0]['name']
                    
                    # Update the page
                    update_url = f"{self.base_url}/api/resource/Wiki Page/{page_name}"
                    update_data = {'content': content}
                    
                    update_response = self.session.put(update_url, json=update_data)
                    
                    if update_response.status_code == 200:
                        logger.info(f"Wiki page '{title}' updated successfully")
                        return True
            
            logger.error(f"Failed to update wiki page '{title}'")
            return False
            
        except Exception as e:
            logger.error(f"Error updating wiki page: {str(e)}")
            return False
