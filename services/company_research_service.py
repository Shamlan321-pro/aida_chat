from frappeclient import FrappeClient
import logging
import json
from typing import Dict, Optional, List
from services.gmaps_service import BusinessData
from datetime import datetime

class CompanyResearchService:
    def __init__(self, client: FrappeClient):
        self.client = client
    
    def store_company_research(self, lead_name: str, business_data: BusinessData, 
                             personalization_content: Optional[str] = None) -> str:
        """
        Store detailed company research data linked to a lead
        Use Comment as primary method, ToDo as fallback
        """
        research_data = self._build_research_data(business_data, personalization_content)
        
        # Method 1: Try to store as Comment (confirmed working)
        try:
            return self._store_as_comment(lead_name, research_data)
        except Exception as e:
            logging.warning(f"Comment storage failed: {e}")
        
        # Method 2: Try to store as ToDo (confirmed working)
        try:
            return self._store_as_todo(lead_name, research_data)
        except Exception as e:
            logging.warning(f"ToDo storage failed: {e}")
        
        # Method 3: Store in lead description as formatted HTML
        try:
            return self._update_lead_description(lead_name, research_data)
        except Exception as e:
            logging.error(f"All storage methods failed: {e}")
            return None
    
    def _build_research_data(self, business_data: BusinessData, personalization_content: Optional[str] = None) -> Dict:
        """Build structured research data"""
        return {
            'business_name': business_data.business_name,
            'description': business_data.description,
            'website': business_data.website,
            'phone': business_data.phone,
            'email': business_data.email,
            'industry': business_data.industry,
            'company_size': business_data.company_size,
            'address': business_data.address,
            'social_profiles': business_data.social_profiles,
            'cid': business_data.cid,
            'personalization': personalization_content,
            'research_date': datetime.now().isoformat()
        }
    
    def _store_as_comment(self, lead_name: str, research_data: Dict) -> str:
        """Store as Comment record (confirmed working)"""
        content = self._format_research_content_html(research_data)
        
        comment = {
            'doctype': 'Comment',
            'comment_type': 'Comment',
            'reference_doctype': 'Lead',
            'reference_name': lead_name,
            'content': content
        }
        
        result = self.client.insert(comment)
        logging.info(f"Stored research as comment: {result.get('name')}")
        return result.get('name')
    
    def _store_as_todo(self, lead_name: str, research_data: Dict) -> str:
        """Store as ToDo record (confirmed working)"""
        content = self._format_research_content_text(research_data)
        
        todo = {
            'doctype': 'ToDo',
            'description': f"🔍 Company Research: {research_data['business_name']}\n\n{content}",
            'reference_type': 'Lead',
            'reference_name': lead_name,
            'status': 'Open',
            'priority': 'Medium'
        }
        
        result = self.client.insert(todo)
        logging.info(f"Stored research as todo: {result.get('name')}")
        return result.get('name')
    
    def _update_lead_description(self, lead_name: str, research_data: Dict) -> str:
        """Update lead description with research data"""
        content = self._format_research_content_html(research_data)
        
        # Get current lead
        lead = self.client.get_doc('Lead', lead_name)
        current_desc = lead.get('description', '')
        
        # Append research data as HTML
        research_section = f"""
        <div style="border-top: 2px solid #007bff; margin-top: 20px; padding-top: 15px;">
            <h4 style="color: #007bff;">🔍 Company Research Data</h4>
            {content}
        </div>
        """
        
        updated_desc = f"{current_desc}{research_section}" if current_desc else research_section
        
        # Update lead
        lead['description'] = updated_desc
        self.client.update(lead)
        
        logging.info(f"Updated lead description with research: {lead_name}")
        return lead_name
    
    def _format_research_content_html(self, research_data: Dict) -> str:
        """Format research data as HTML content with dark mode compatibility"""
        html_parts = []
        
        if research_data.get('description'):
            html_parts.append(f"""
            <div class="mb-3">
                <strong>📋 Business Description:</strong>
                <div class="text-muted small mt-2 p-3 border-left border-primary bg-light rounded">
                    {research_data['description']}
                </div>
            </div>
            """)
        
        # Company details section
        details = []
        if research_data.get('industry'):
            details.append(f"🏢 <strong>Industry:</strong> {research_data['industry']}")
        if research_data.get('company_size'):
            details.append(f"👥 <strong>Company Size:</strong> {research_data['company_size']}")
        if research_data.get('website'):
            details.append(f"🌐 <strong>Website:</strong> <a href='{research_data['website']}'>{research_data['website']}</a>")
        
        if details:
            html_parts.append(f"""
            <div class="mb-3">
                <strong>Company Details:</strong><br>
                <div class="mt-2">
                    {' • '.join(details)}
                </div>
            </div>
            """)
        
        # Social media section
        if research_data.get('social_profiles'):
            social_links = []
            for platform, url in research_data['social_profiles'].items():
                if url:
                    social_links.append(f"<a href='{url}' target='_blank' class='text-primary'>{platform}</a>")
            
            if social_links:
                html_parts.append(f"""
                <div class="mb-3">
                    <strong>📱 Social Media:</strong><br>
                    <div class="mt-2">
                        {' • '.join(social_links)}
                    </div>
                </div>
                """)
        
        # Personalization section
        if research_data.get('personalization'):
            html_parts.append(f"""
            <div class="mb-3">
                <strong>🤖 AI Personalization:</strong>
                <div class="alert alert-info mt-2">
                    {research_data['personalization']}
                </div>
            </div>
            """)
        
        # Metadata
        html_parts.append(f"""
        <div class="mt-3 pt-3 border-top text-muted small">
            📅 Research Date: {research_data.get('research_date', 'Unknown')}<br>
            🆔 Google CID: {research_data.get('cid', 'N/A')}
        </div>
        """)
        
        return f'<div class="company-research-data">{" ".join(html_parts)}</div>'
    
    def _format_research_content_text(self, research_data: Dict) -> str:
        """Format research data as plain text content"""
        content_parts = []
        
        if research_data.get('description'):
            content_parts.append(f"📋 BUSINESS DESCRIPTION:\n{research_data['description']}")
        
        if research_data.get('industry'):
            content_parts.append(f"🏢 INDUSTRY: {research_data['industry']}")
        
        if research_data.get('company_size'):
            content_parts.append(f"👥 COMPANY SIZE: {research_data['company_size']}")
        
        if research_data.get('website'):
            content_parts.append(f"🌐 WEBSITE: {research_data['website']}")
        
        if research_data.get('social_profiles'):
            content_parts.append("📱 SOCIAL MEDIA:")
            for platform, url in research_data['social_profiles'].items():
                if url:
                    content_parts.append(f"  • {platform}: {url}")
        
        if research_data.get('personalization'):
            content_parts.append(f"🤖 AI PERSONALIZATION:\n{research_data['personalization']}")
        
        content_parts.append(f"📅 Research Date: {research_data.get('research_date', 'Unknown')}")
        content_parts.append(f"🆔 Google CID: {research_data.get('cid', 'N/A')}")
        
        return '\n\n'.join(content_parts)
    
    def get_company_research(self, lead_name: str) -> Optional[str]:
        """Retrieve stored company research for a lead"""
        # Try to get from Comments first
        try:
            comments = self.client.get_list('Comment',
                filters={
                    'reference_doctype': 'Lead',
                    'reference_name': lead_name
                },
                fields=['name', 'content'],
                order_by='creation desc',
                limit_page_length=1
            )
            if comments:
                return comments[0]['content']
        except Exception as e:
            logging.warning(f"Failed to get comments: {e}")
        
        # Try to get from ToDo
        try:
            todos = self.client.get_list('ToDo',
                filters={
                    'reference_type': 'Lead',
                    'reference_name': lead_name
                },
                fields=['name', 'description'],
                order_by='creation desc',
                limit_page_length=1
            )
            if todos:
                return todos[0]['description']
        except Exception as e:
            logging.warning(f"Failed to get todos: {e}")
        
        return None
    
    def get_all_leads_with_research(self) -> List[Dict]:
        """Get all leads that have research data for campaign purposes"""
        leads_with_research = []
        
        try:
            # Get leads that have comments
            comments = self.client.get_list('Comment',
                filters={'reference_doctype': 'Lead'},
                fields=['reference_name', 'content'],
                limit_page_length=100
            )
            
            for comment in comments:
                lead_name = comment['reference_name']
                try:
                    lead = self.client.get_doc('Lead', lead_name)
                    leads_with_research.append({
                        'lead_name': lead_name,
                        'company_name': lead.get('company_name'),
                        'email': lead.get('email_id'),
                        'research_data': comment['content']
                    })
                except:
                    continue
        except Exception as e:
            logging.error(f"Failed to get leads with research: {e}")
        
        return leads_with_research
