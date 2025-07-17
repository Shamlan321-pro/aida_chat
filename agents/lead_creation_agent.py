import logging
from typing import List, Dict, Optional
from services.gmaps_service import GMapsDataExtractor
from services.erpnext_service import ERPNextService
from services.personalization_service import PersonalizationService
from services.lead_filter_service import LeadFilterService

class LeadCreationAgent:
    def __init__(self, 
                 gmaps_api_key: str,
                 erpnext_url: str,
                 erpnext_username: str,
                 erpnext_password: str,
                 gemini_api_key: Optional[str] = None):
        
        self.gmaps_service = GMapsDataExtractor(gmaps_api_key)
        self.erpnext_service = ERPNextService(erpnext_url, erpnext_username, erpnext_password)
        self.filter_service = LeadFilterService()
        
        self.personalization_service = None
        if gemini_api_key:
            self.personalization_service = PersonalizationService(gemini_api_key)
        
        # Test ERPNext connection immediately
        try:
            # Verify connection works by getting a few leads
            test_leads = self.erpnext_service.client.get_list('Lead', fields=['name'], limit_page_length=1)
            logging.info(f"ERPNext connection verified, found {len(test_leads)} existing leads")
        except Exception as e:
            logging.error(f"ERPNext connection test failed: {e}")
            raise Exception(f"ERPNext connection failed: {e}")

    def create_leads(self, 
                    business_type: str,
                    location: str,
                    count: int,
                    user_input: str = "",
                    generate_personalization: bool = True) -> Dict:
        """
        Main method to create leads based on user request with filtering
        
        Args:
            business_type: Type of business to search for
            location: Geographic location
            count: Number of leads to create
            user_input: Additional user input for filtering
            generate_personalization: Whether to generate personalized content
        
        Returns:
            Summary of created leads
        """
        logging.info(f"Creating {count} leads for {business_type} in {location}")
        
        # Parse filtering requirements from user input
        filters = self.filter_service.parse_filter_requirements(user_input)
        filter_summary = self.filter_service.get_filter_summary(filters)
        logging.info(filter_summary)
        
        try:
            # Step 1: Test ERPNext connection first
            try:
                lead_fields = self.erpnext_service.get_lead_fields()
                logging.info(f"ERPNext connection successful. Available fields: {len(lead_fields)}")
            except Exception as e:
                logging.error(f"ERPNext connection failed: {e}")
                return {
                    'success': False,
                    'message': f'ERPNext connection failed: {str(e)}',
                    'created_leads': []
                }
            
            # Step 2: Get business data from Google Maps API (get more to account for filtering)
            try:
                # Get extra businesses to account for filtering
                search_count = max(count * 3, 20) if filters else count
                
                businesses = self.gmaps_service.search_businesses(
                    query=business_type,
                    location=location,
                    count=search_count
                )
                
                if not businesses:
                    return {
                        'success': False,
                        'message': 'No businesses found matching criteria',
                        'created_leads': []
                    }
                
                logging.info(f"Found {len(businesses)} businesses from API")
                
                # Apply filters
                if filters:
                    filtered_businesses = self.filter_service.filter_businesses(businesses, filters)
                    if not filtered_businesses:
                        return {
                            'success': False,
                            'message': f'No businesses found matching filter criteria. {filter_summary}',
                            'created_leads': [],
                            'filter_summary': filter_summary
                        }
                    
                    # Limit to requested count
                    businesses = filtered_businesses[:count]
                    logging.info(f"After filtering: {len(businesses)} businesses meet criteria")
                else:
                    businesses = businesses[:count]
                
            except Exception as e:
                logging.error(f"Google Maps API failed: {e}")
                return {
                    'success': False,
                    'message': f'Failed to get business data: {str(e)}',
                    'created_leads': []
                }
            
            # Step 3: Create leads in ERPNext
            created_leads = []
            personalized_content = {}
            failed_leads = []
            
            for business in businesses:
                try:
                    # Generate personalization content if requested
                    personalization = None
                    if generate_personalization and self.personalization_service:
                        try:
                            website_content = self.gmaps_service.get_website_content(business)
                            personalization = self.personalization_service.generate_personalized_email(
                                business, website_content
                            )
                            personalized_content[business.business_name] = personalization
                        except Exception as e:
                            logging.warning(f"Personalization failed for {business.business_name}: {e}")
                    
                    # Create lead in ERPNext
                    lead_name = self.erpnext_service.create_lead(business, personalization)
                    created_leads.append({
                        'lead_name': lead_name,
                        'business_name': business.business_name,
                        'website': business.website,
                        'industry': business.industry
                    })
                
                except Exception as e:
                    logging.error(f"Failed to process {business.business_name}: {e}")
                    failed_leads.append({
                        'business_name': business.business_name,
                        'error': str(e)
                    })
                    continue
            
            success_count = len(created_leads)
            total_found = len(businesses)
            
            result = {
                'success': success_count > 0,
                'message': f'Successfully created {success_count}/{total_found} leads',
                'created_leads': created_leads,
                'failed_leads': failed_leads,
                'personalized_content': personalized_content,
                'total_found': total_found
            }
            
            if filters:
                result['filter_summary'] = filter_summary
                result['filters_applied'] = filters
            
            return result
        
        except Exception as e:
            logging.error(f"Lead creation process failed: {e}")
            return {
                'success': False,
                'message': f'Lead creation process failed: {str(e)}',
                'created_leads': []
            }
    
    def get_lead_summary(self, lead_names: List[str]) -> List[Dict]:
        """Get summary of created leads from ERPNext"""
        summaries = []
        
        for lead_name in lead_names:
            try:
                lead = self.erpnext_service.client.get_doc('Lead', lead_name)
                summaries.append({
                    'name': lead['name'],
                    'lead_name': lead.get('lead_name'),
                    'company_name': lead.get('company_name'),
                    'status': lead.get('status'),
                    'source': lead.get('source'),
                    'email': lead.get('email_id'),
                    'phone': lead.get('phone'),
                    'website': lead.get('website')
                })
            except Exception as e:
                logging.error(f"Failed to get lead {lead_name}: {e}")
        
        return summaries
