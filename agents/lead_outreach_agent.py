import os
import logging
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import google.generativeai as genai
from services.erpnext_service import ERPNextService
from services.wiki_service import WikiService
from services.email_service import EmailService

logger = logging.getLogger(__name__)

class LeadOutreachAgent:
    def __init__(self, url: str, username: str, password: str):
        # Initialize ERPNext service with correct parameters
        self.erpnext_service = ERPNextService(
            url=url,
            username=username,
            password=password
        )
        self.wiki_service = WikiService(
            base_url=url,
            username=username,
            password=password
        )
        self.email_service = EmailService()
        
        # Configure Gemini AI
        genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Configuration
        self.max_daily_outreach = int(os.getenv('MAX_DAILY_OUTREACH', '50'))
        self.batch_size = int(os.getenv('LEAD_BATCH_SIZE', '10'))
        self.enable_scoring = os.getenv('ENABLE_LEAD_SCORING', 'true').lower() == 'true'
        self.min_score = float(os.getenv('MIN_LEAD_SCORE', '0.6'))
    
    def get_leads_for_outreach(self, limit: int = None, filters: Dict = None) -> List[Dict]:
        """Get leads that are ready for outreach with enhanced filtering"""
        if limit is None:
            limit = self.max_daily_outreach
        
        try:
            # Use the available method from ERPNext service
            if hasattr(self.erpnext_service, 'get_leads_for_campaign'):
                all_leads = self.erpnext_service.get_leads_for_campaign()
            else:
                # Use fallback method with corrected field names
                all_leads = self._get_leads_fallback()
            
            # Apply filtering
            filtered_leads = []
            for lead in all_leads:
                if self._should_include_lead(lead, filters):
                    filtered_leads.append(lead)
            
            if self.enable_scoring:
                filtered_leads = self._score_and_filter_leads(filtered_leads)
            
            return filtered_leads[:limit]
        except Exception as e:
            logger.error(f"Error fetching leads: {str(e)}")
            return []
    
    def _get_leads_fallback(self) -> List[Dict]:
        """Fallback method to get leads directly from Frappe with correct field names"""
        try:
            # Use the frappe client directly to get leads
            from frappeclient import FrappeClient
            client = FrappeClient(
                os.getenv('ERPNEXT_URL'),
                os.getenv('ERPNEXT_USERNAME'),
                os.getenv('ERPNEXT_PASSWORD')
            )
            
            # Get leads using only permitted fields
            leads = client.get_list('Lead', fields=[
                'name', 'lead_name', 'company_name', 'email_id', 
                'industry', 'status', 'no_of_employees'
            ])
            
            return leads
        except Exception as e:
            logger.error(f"Fallback lead fetch failed: {str(e)}")
            return []

    def _should_include_lead(self, lead: Dict, filters: Dict = None) -> bool:
        """Check if lead should be included based on filters"""
        # Basic validation
        if not lead.get('email_id'):
            return False
        
        # Apply custom filters
        if filters:
            if 'industry' in filters:
                lead_industry = lead.get('industry', '').lower()
                if filters['industry'].lower() not in lead_industry:
                    return False
            
            if 'min_employees' in filters:
                emp_count = int(lead.get('no_of_employees', 0) or 0)
                if emp_count < filters['min_employees']:
                    return False
            
            if 'max_employees' in filters:
                emp_count = int(lead.get('no_of_employees', 0) or 0)
                if emp_count > filters['max_employees']:
                    return False
        
        return True
    
    def _score_and_filter_leads(self, leads: List[Dict]) -> List[Dict]:
        """Score leads based on various criteria"""
        scored_leads = []
        
        for lead in leads:
            score = 0.0
            
            # Score based on company size
            if lead.get('no_of_employees'):
                emp_count = int(lead.get('no_of_employees', 0))
                if emp_count > 100:
                    score += 0.3
                elif emp_count > 50:
                    score += 0.2
                elif emp_count > 10:
                    score += 0.1
            
            # Score based on industry match (if available)
            if lead.get('industry'):
                score += 0.2
            
            # Score based on lead source quality
            lead_source = lead.get('source', '').lower()
            if 'website' in lead_source or 'referral' in lead_source:
                score += 0.3
            elif 'email' in lead_source:
                score += 0.2
            
            # Score based on company description availability
            if lead.get('company_description'):
                score += 0.2
            
            lead['lead_score'] = score
            
            if score >= self.min_score:
                scored_leads.append(lead)
        
        # Sort by score descending
        return sorted(scored_leads, key=lambda x: x['lead_score'], reverse=True)
    
    def generate_personalized_email(self, lead: Dict, company_profile: str, outreach_template: str) -> Dict[str, str]:
        """Generate personalized email content using AI"""
        prompt = f"""
        You are a professional business development representative. Generate a personalized outreach email based on the following information:

        MY COMPANY PROFILE:
        {company_profile}

        OUTREACH GUIDELINES:
        {outreach_template}

        LEAD INFORMATION:
        - Company: {lead.get('company_name', 'N/A')}
        - Contact: {lead.get('lead_name', 'N/A')}
        - Email: {lead.get('email_id', 'N/A')}
        - Industry: {lead.get('industry', 'N/A')}
        - Company Size: {lead.get('no_of_employees', 'N/A')} employees
        - Company Description: {lead.get('company_description', 'N/A')}

        Generate a professional email with:
        1. Personalized subject line
        2. Email body that's concise, valuable, and relevant
        3. Clear call-to-action
        4. Professional tone

        Format your response as:
        SUBJECT: [subject line]
        BODY: [email body]
        """
        
        try:
            response = self.model.generate_content(prompt)
            email_content = response.text
            
            # Parse subject and body
            lines = email_content.split('\n')
            subject = ""
            body = ""
            
            for i, line in enumerate(lines):
                if line.startswith('SUBJECT:'):
                    subject = line.replace('SUBJECT:', '').strip()
                elif line.startswith('BODY:'):
                    body = '\n'.join(lines[i:]).replace('BODY:', '').strip()
                    break
            
            return {
                'subject': subject,
                'body': body
            }
        except Exception as e:
            logger.error(f"Error generating email content: {str(e)}")
            return {
                'subject': f"Partnership Opportunity with {lead.get('company_name', 'Your Company')}",
                'body': "I hope this email finds you well. I'd love to discuss a potential partnership opportunity that could benefit both our companies. Would you be available for a brief call this week?"
            }
    
    def send_outreach_email(self, lead: Dict, email_content: Dict[str, str]) -> bool:
        """Send outreach email to a lead"""
        try:
            success = self.email_service.send_email(
                to_email=lead['email_id'],
                subject=email_content['subject'],
                body=email_content['body']
            )
            
            if success:
                # Update lead with outreach timestamp
                self._update_lead_outreach_status(lead['name'])
                logger.info(f"Outreach email sent to {lead['email_id']}")
            
            return success
        except Exception as e:
            logger.error(f"Error sending outreach email to {lead['email_id']}: {str(e)}")
            return False
    
    def _update_lead_outreach_status(self, lead_name: str):
        """Update lead with outreach timestamp"""
        try:
            logger.info(f"Updated outreach status for lead: {lead_name}")
        except Exception as e:
            logger.error(f"Error updating lead outreach status: {str(e)}")
    
    def run_targeted_campaign(self, target_count: int = None, filters: Dict = None) -> Dict[str, int]:
        """Run automated outreach campaign with targeting"""
        if target_count is None:
            target_count = self.max_daily_outreach
        
        logger.info(f"Starting targeted outreach campaign for {target_count} leads with filters: {filters}")
        
        try:
            # Get company knowledge
            company_profile = self.wiki_service.get_company_profile()
            outreach_template = self.wiki_service.get_outreach_template()
            
            # Get leads for outreach
            leads = self.get_leads_for_outreach(limit=target_count, filters=filters)
            
            stats = {
                'total_leads': len(leads),
                'emails_sent': 0,
                'emails_failed': 0,
                'filters_applied': filters or {},
                'campaign_start': datetime.now().isoformat()
            }
            
            # Process leads in batches
            for i in range(0, len(leads), self.batch_size):
                batch = leads[i:i + self.batch_size]
                
                for lead in batch:
                    try:
                        # Generate personalized email
                        email_content = self.generate_personalized_email(
                            lead, company_profile, outreach_template
                        )
                        
                        # Send email
                        if self.send_outreach_email(lead, email_content):
                            stats['emails_sent'] += 1
                        else:
                            stats['emails_failed'] += 1
                        
                        # Small delay between emails
                        import time
                        time.sleep(2)
                        
                    except Exception as e:
                        logger.error(f"Error processing lead {lead.get('name', 'Unknown')}: {str(e)}")
                        stats['emails_failed'] += 1
            
            stats['campaign_end'] = datetime.now().isoformat()
            logger.info(f"Targeted outreach campaign completed. Stats: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Campaign failed: {str(e)}")
            return {
                'error': str(e),
                'emails_sent': 0,
                'emails_failed': 0
            }
    
    def get_campaign_stats(self, days: int = 7) -> Dict:
        """Get campaign statistics for the last N days"""
        try:
            # Use the available method from ERPNext service
            if hasattr(self.erpnext_service, 'get_leads_for_campaign'):
                all_leads = self.erpnext_service.get_leads_for_campaign()
            else:
                # Use fallback method
                all_leads = self._get_leads_fallback()
            
            stats = {
                'period_days': days,
                'total_leads_in_system': len(all_leads),
                'leads_with_email': len([l for l in all_leads if l.get('email_id')]),
                'by_industry': {},
                'by_status': {},
                'response_rate': 0.0  # This would need tracking
            }
            
            # Aggregate by industry and status (since source field isn't permitted)
            for lead in all_leads:
                industry = lead.get('industry', 'Unknown')
                status = lead.get('status', 'Unknown')
                
                stats['by_industry'][industry] = stats['by_industry'].get(industry, 0) + 1
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting campaign stats: {str(e)}")
            return {'error': str(e)}

# CLI interface for testing
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Lead Outreach Agent')
    parser.add_argument('--count', type=int, default=10, help='Number of leads to contact')
    parser.add_argument('--dry-run', action='store_true', help='Generate emails without sending')
    parser.add_argument('--industry', type=str, help='Filter by industry')
    parser.add_argument('--min-employees', type=int, help='Minimum number of employees')
    parser.add_argument('--stats', action='store_true', help='Show campaign statistics')
    
    args = parser.parse_args()
    
    agent = LeadOutreachAgent()
    
    if args.stats:
        stats = agent.get_campaign_stats()
        print(json.dumps(stats, indent=2))
    elif args.dry_run:
        filters = {}
        if args.industry:
            filters['industry'] = args.industry
        if args.min_employees:
            filters['min_employees'] = args.min_employees
            
        leads = agent.get_leads_for_outreach(limit=args.count, filters=filters)
        company_profile = agent.wiki_service.get_company_profile()
        outreach_template = agent.wiki_service.get_outreach_template()
        
        for lead in leads[:3]:  # Show first 3 as examples
            email_content = agent.generate_personalized_email(
                lead, company_profile, outreach_template
            )
            print(f"\n--- Email for {lead['company_name']} ---")
            print(f"Subject: {email_content['subject']}")
            print(f"Body:\n{email_content['body']}")
    else:
        filters = {}
        if args.industry:
            filters['industry'] = args.industry
        if args.min_employees:
            filters['min_employees'] = args.min_employees
            
        stats = agent.run_targeted_campaign(target_count=args.count, filters=filters)
        print(f"Campaign Results: {json.dumps(stats, indent=2)}")