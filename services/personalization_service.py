import google.generativeai as genai
from typing import Dict, Optional, List
from services.gmaps_service import BusinessData
import logging

class PersonalizationService:
    def __init__(self, gemini_api_key: str):
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
    
    def generate_personalized_email(self, business_data: BusinessData, website_content: Optional[str] = None) -> str:
        """
        Generate personalized outreach email based on business data and website content
        
        Args:
            business_data: Business information from API
            website_content: Scraped website content
        
        Returns:
            Personalized email content
        """
        context = self._build_context(business_data, website_content)
        
        prompt = f"""
        Create a personalized business outreach email based on the following information:
        
        {context}
        
        The email should:
        - Be professional and engaging
        - Reference specific details about their business
        - Propose a relevant solution or partnership
        - Be concise (under 200 words)
        - Have a clear call-to-action
        - Include proper email structure with subject line
        
        Generate the email content with subject line:
        """
        
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        
        except Exception as e:
            logging.error(f"Failed to generate personalized email: {e}")
            return f"Failed to generate personalized email: {e}"
    
    def _build_context(self, business_data: BusinessData, website_content: Optional[str] = None) -> str:
        """Build context for email generation"""
        context_parts = [
            f"Company Name: {business_data.business_name}",
        ]
        
        if business_data.industry:
            context_parts.append(f"Industry: {business_data.industry}")
        
        if business_data.company_size:
            context_parts.append(f"Company Size: {business_data.company_size}")
        
        if business_data.description:
            context_parts.append(f"Description: {business_data.description}")
        
        if website_content:
            context_parts.append(f"Website Content Analysis: {website_content}")
        
        if business_data.social_profiles:
            platforms = list(business_data.social_profiles.keys())
            context_parts.append(f"Social Media Presence: {', '.join(platforms)}")
        
        return '\n'.join(context_parts)
    
    def generate_follow_up_sequence(self, business_data: BusinessData, website_content: Optional[str] = None) -> List[str]:
        """Generate a sequence of follow-up emails"""
        emails = []
        
        # Initial outreach
        emails.append(self.generate_personalized_email(business_data, website_content))
        
        # Follow-up email
        follow_up_prompt = f"""
        Create a follow-up email for {business_data.business_name} assuming no response to the initial outreach.
        
        Business context: {self._build_context(business_data, website_content)}
        
        The follow-up should:
        - Be shorter and more direct
        - Add value or new perspective
        - Have a softer call-to-action
        - Reference the previous email subtly
        """
        
        try:
            response = self.model.generate_content(follow_up_prompt)
            emails.append(response.text.strip())
        except Exception as e:
            logging.error(f"Failed to generate follow-up email: {e}")
        
        return emails
