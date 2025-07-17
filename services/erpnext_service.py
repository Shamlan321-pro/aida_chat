from frappeclient import FrappeClient
import logging
import json
from typing import Dict, List, Optional
from services.gmaps_service import BusinessData
from services.company_research_service import CompanyResearchService

class ERPNextService:
    def __init__(self, url: str, username: str, password: str):
        self.url = url
        self.username = username
        self.password = password
        self.client = None
        self.valid_industries = None
        self.research_service = None
        self._authenticate()
        self._load_valid_industries()
        
        # Initialize research service after authentication
        if self.client:
            self.research_service = CompanyResearchService(self.client)

    def _authenticate(self):
        """Authenticate with ERPNext using login method"""
        try:
            self.client = FrappeClient(self.url)
            self.client.login(self.username, self.password)
            logging.info(f"Successfully authenticated with ERPNext at {self.url}")
            logging.info(f"Connected as user: {self.username}")
            
        except Exception as e:
            logging.error(f"Failed to authenticate with ERPNext: {e}")
            raise Exception(f"ERPNext authentication failed: {e}")
    
    def get_lead_fields(self) -> Dict:
        """Get available fields for Lead doctype"""
        try:
            if not self.client:
                raise Exception("ERPNext client not authenticated")
            
            # Try to get Lead doctype meta using get_doc
            try:
                meta = self.client.get_doc('DocType', 'Lead')
                
                if not meta:
                    raise Exception("Lead DocType not found")
                
                fields = {}
                doctype_fields = meta.get('fields', [])
                
                if not doctype_fields:
                    logging.warning("No fields found in Lead DocType, using default fields")
                    return self._get_default_lead_fields()
                
                for field in doctype_fields:
                    if isinstance(field, dict):
                        fieldname = field.get('fieldname')
                        if fieldname:
                            fields[fieldname] = {
                                'label': field.get('label', ''),
                                'fieldtype': field.get('fieldtype', ''),
                                'required': field.get('reqd', 0),
                                'options': field.get('options', '')
                            }
                
                logging.info(f"Retrieved {len(fields)} fields for Lead doctype")
                return fields
                
            except Exception as e:
                logging.warning(f"Could not get Lead DocType meta: {e}")
                return self._get_default_lead_fields()
        
        except Exception as e:
            logging.error(f"Failed to get Lead fields: {e}")
            logging.info("Using default Lead fields as fallback")
            return self._get_default_lead_fields()
    
    def _get_default_lead_fields(self) -> Dict:
        """Return default Lead fields as fallback"""
        return {
            'lead_name': {'label': 'Lead Name', 'fieldtype': 'Data', 'required': 1},
            'company_name': {'label': 'Company Name', 'fieldtype': 'Data', 'required': 0},
            'email_id': {'label': 'Email', 'fieldtype': 'Data', 'required': 0},
            'phone': {'label': 'Phone', 'fieldtype': 'Data', 'required': 0},
            'website': {'label': 'Website', 'fieldtype': 'Data', 'required': 0},
            'source': {'label': 'Source', 'fieldtype': 'Link', 'required': 0},
            'status': {'label': 'Status', 'fieldtype': 'Select', 'required': 0},
            'type': {'label': 'Type', 'fieldtype': 'Select', 'required': 0},
            'industry': {'label': 'Industry', 'fieldtype': 'Link', 'required': 0},
            'address_line1': {'label': 'Address Line 1', 'fieldtype': 'Data', 'required': 0},
            'notes': {'label': 'Notes', 'fieldtype': 'Text', 'required': 0}
        }
    
    def create_lead(self, business_data: BusinessData, personalization_content: Optional[str] = None) -> str:
        """
        Create a lead in ERPNext from business data and store detailed research
        """
        if not self.client:
            raise Exception("ERPNext client not authenticated")
        
        # Use comprehensive mapping to include all business information
        lead_data = self._map_business_to_lead_basic(business_data)
        
        # Add personalization content to description if available
        if personalization_content:
            if 'description' in lead_data:
                lead_data['description'] += f"\n\nAI-Generated Outreach:\n{personalization_content}"
            else:
                lead_data['description'] = f"AI-Generated Outreach:\n{personalization_content}"
        
        lead_doc = {
            'doctype': 'Lead',
            **lead_data
        }
        
        try:
            response = self.client.insert(lead_doc)
            
            if response and isinstance(response, dict):
                lead_name = response.get('name')
                if lead_name:
                    logging.info(f"Successfully created lead: {lead_name} for {business_data.business_name}")
                    
                    # Add comprehensive note with all business information
                    try:
                        note_added = self.add_comprehensive_note_to_lead(lead_name, business_data, personalization_content)
                        if note_added:
                            logging.info(f"✅ Added comprehensive note to lead: {lead_name}")
                        else:
                            logging.warning(f"⚠️ Failed to add note to lead: {lead_name}")
                    except Exception as note_error:
                        logging.warning(f"⚠️ Note addition failed: {note_error}")
                    
                    # Store detailed research data separately as a comment
                    if self.research_service and (business_data.description or business_data.social_profiles or personalization_content):
                        try:
                            research_ref = self.research_service.store_company_research(
                                lead_name, business_data, personalization_content
                            )
                            if research_ref:
                                logging.info(f"✅ Stored detailed research: {research_ref}")
                            else:
                                logging.warning("⚠️ Research storage returned no reference")
                        except Exception as e:
                            logging.warning(f"❌ Failed to store research data: {e}")
                    
                    return lead_name
                else:
                    logging.warning(f"Lead created but no name returned for {business_data.business_name}")
                    return f"LEAD-{business_data.business_name[:20]}"
            else:
                logging.warning(f"Unexpected response type: {type(response)} for {business_data.business_name}")
                return f"LEAD-{business_data.business_name[:20]}"
        
        except Exception as e:
            error_msg = str(e)
            
            # If description field causes issues, try with notes field instead
            if "description" in error_msg.lower() or "TypeError" in error_msg:
                logging.warning(f"Description field issue for {business_data.business_name}, trying with notes field")
                
                # Move description content to notes
                if 'description' in lead_doc:
                    lead_doc['notes'] = lead_doc['description']
                    del lead_doc['description']
                
                try:
                    response = self.client.insert(lead_doc)
                    if response and isinstance(response, dict):
                        lead_name = response.get('name', f"LEAD-{business_data.business_name[:20]}")
                        logging.info(f"Created lead with notes field: {lead_name}")
                        return lead_name
                except Exception as e2:
                    logging.error(f"Notes field also failed: {e2}")
            
            # If it's a link validation error, try creating without problematic fields
            if "LinkValidationError" in error_msg or "Could not find" in error_msg:
                logging.warning(f"Link validation failed for {business_data.business_name}, trying minimal version")
                
                # Create minimal lead without link fields but with all contact info
                minimal_lead = {
                    'doctype': 'Lead',
                    'lead_name': business_data.business_name,
                    'status': 'Lead',
                    'source': 'Google Maps API'
                }
                
                # Add all available contact information
                if business_data.phone:
                    minimal_lead['phone'] = business_data.phone
                if business_data.email:
                    minimal_lead['email_id'] = business_data.email
                if business_data.website:
                    minimal_lead['website'] = business_data.website
                if business_data.address:
                    minimal_lead['address_line1'] = business_data.address
                
                # Add comprehensive description with all business info
                description_parts = [f"Company: {business_data.business_name}"]
                if business_data.description:
                    description_parts.append(f"Description: {business_data.description}")
                if business_data.industry:
                    description_parts.append(f"Industry: {business_data.industry}")
                if business_data.social_profiles:
                    for platform, url in business_data.social_profiles.items():
                        if url:
                            description_parts.append(f"{platform}: {url}")
                if personalization_content:
                    description_parts.append(f"Personalized Content: {personalization_content}")
                
                minimal_lead['description'] = '\n'.join(description_parts)
                
                try:
                    response = self.client.insert(minimal_lead)
                    if response and isinstance(response, dict):
                        lead_name = response.get('name', f"LEAD-{business_data.business_name[:20]}")
                        logging.info(f"Created minimal lead: {lead_name} for {business_data.business_name}")
                        return lead_name
                except Exception as e2:
                    logging.error(f"Even minimal lead creation failed: {e2}")
                    raise Exception(f"Lead creation failed: {e2}")
            else:
                logging.error(f"Failed to create lead for {business_data.business_name}: {e}")
                raise Exception(f"Lead creation failed: {e}")

    def _map_business_to_lead_basic(self, business_data: BusinessData) -> Dict:
        """Map business data to comprehensive ERPNext Lead fields"""
        lead_data = {
            'lead_name': business_data.business_name,
            'company_name': business_data.business_name,
            'source': 'Google Maps API',
            'status': 'Open'
        }
        
        # Add all available contact information
        if business_data.phone:
            lead_data['phone'] = business_data.phone
        
        if business_data.email:
            lead_data['email_id'] = business_data.email
        
        if business_data.website:
            lead_data['website'] = business_data.website
        
        if business_data.address:
            lead_data['address_line1'] = business_data.address
        
        # Handle industry with validation
        if business_data.industry:
            mapped_industry = self._map_industry(business_data.industry)
            if mapped_industry:
                lead_data['industry'] = mapped_industry
        
        # Build comprehensive description with all business information
        description_parts = []
        
        # Add business description
        if business_data.description and business_data.description.strip():
            description_parts.append(f"Business Overview:\n{business_data.description}")
        
        # Add contact information
        contact_info = []
        if business_data.phone:
            contact_info.append(f"Phone: {business_data.phone}")
        if business_data.email:
            contact_info.append(f"Email: {business_data.email}")
        if business_data.website:
            contact_info.append(f"Website: {business_data.website}")
        if business_data.address:
            contact_info.append(f"Address: {business_data.address}")
        
        if contact_info:
            description_parts.append("Contact Information:\n" + "\n".join(contact_info))
        
        # Add industry information
        if business_data.industry:
            if not self._map_industry(business_data.industry):
                description_parts.append(f"Industry: {business_data.industry}")
        
        # Add company size
        if business_data.company_size:
            description_parts.append(f"Company Size: {business_data.company_size}")
        
        # Add social profiles
        if business_data.social_profiles:
            description_parts.append("Social Media Profiles:")
            for platform, url in business_data.social_profiles.items():
                if url:
                    description_parts.append(f"• {platform}: {url}")
        
        # Add Google CID for reference
        if business_data.cid:
            description_parts.append(f"Google CID: {business_data.cid}")
        
        # Add lead source timestamp
        from datetime import datetime
        description_parts.append(f"Source: Google Maps API - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Use description field for comprehensive notes
        if description_parts:
            comprehensive_description = '\n\n'.join(description_parts)
            lead_data['description'] = comprehensive_description
        
        return lead_data

    def get_lead_research(self, lead_name: str) -> Optional[str]:
        """Get detailed research data for a lead"""
        if self.research_service:
            return self.research_service.get_company_research(lead_name)
        return None

    def get_leads_for_campaign(self) -> List[Dict]:
        """Get all leads with research data for outreach campaigns"""
        if self.research_service:
            return self.research_service.get_all_leads_with_research()
        return []

    def _load_valid_industries(self):
        """Load valid industries from ERPNext to avoid validation errors"""
        try:
            industries = self.client.get_list('Industry', fields=['name'], limit_page_length=100)
            if industries:
                self.valid_industries = set(industry['name'] for industry in industries)
                logging.info(f"Loaded {len(self.valid_industries)} valid industries from ERPNext")
            else:
                self.valid_industries = set()
                logging.warning("No industries found in ERPNext")
        except Exception as e:
            logging.warning(f"Could not load industries: {e}")
            self.valid_industries = set()
    
    def _map_industry(self, api_industry: str) -> Optional[str]:
        """Map API industry to valid ERPNext industry"""
        if not api_industry or not self.valid_industries:
            return None
        
        # Direct match
        if api_industry in self.valid_industries:
            return api_industry
        
        # Common mappings
        industry_mappings = {
            'Software Development': 'Information Technology',
            'Information Technology & Services': 'Information Technology',
            'Embedded Software Products': 'Information Technology',
            'Computer Software': 'Information Technology',
            'Internet': 'Information Technology',
            'Technology': 'Information Technology',
            'IT Services': 'Information Technology',
            'Software': 'Information Technology'
        }
        
        # Try mapped value
        mapped = industry_mappings.get(api_industry)
        if mapped and mapped in self.valid_industries:
            return mapped
        
        # Try partial matching
        api_lower = api_industry.lower()
        for valid_industry in self.valid_industries:
            if 'technology' in api_lower and 'technology' in valid_industry.lower():
                return valid_industry
            if 'software' in api_lower and 'software' in valid_industry.lower():
                return valid_industry
            if 'information' in api_lower and 'information' in valid_industry.lower():
                return valid_industry
        
        # No valid mapping found
        logging.warning(f"No valid industry mapping found for: {api_industry}")
        return None

    def _map_business_to_lead(self, business_data: BusinessData, personalization_content: Optional[str] = None) -> Dict:
        """Map business data to ERPNext Lead fields"""
        print(f"\n=== MAPPING DEBUG for {business_data.business_name} ===")
        print(f"Business description from API: '{business_data.description}'")
        
        lead_data = {
            'lead_name': business_data.business_name,
            'company_name': business_data.business_name,
            'source': 'Google Maps API',
            'status': 'Lead'
        }
        
        # Only add fields that have values to avoid validation errors
        if business_data.phone:
            lead_data['phone'] = business_data.phone
        
        if business_data.email:
            lead_data['email_id'] = business_data.email
        
        if business_data.website:
            lead_data['website'] = business_data.website
        
        # Handle industry with validation
        if business_data.industry:
            mapped_industry = self._map_industry(business_data.industry)
            if mapped_industry:
                lead_data['industry'] = mapped_industry
                logging.info(f"Mapped industry '{business_data.industry}' to '{mapped_industry}'")
            else:
                logging.warning(f"Skipping invalid industry: {business_data.industry}")
        
        # Handle address
        if business_data.address:
            lead_data['address_line1'] = business_data.address
        
        # Build comprehensive description with all business information
        description_parts = []
        
        # Add business description first (this includes website overview)
        if business_data.description and business_data.description.strip():
            description_parts.append(f"Business Overview:\n{business_data.description}")
            print(f"Added business description: {business_data.description[:100]}...")
        else:
            print("No business description found")
        
        # Add original industry if it couldn't be mapped
        if business_data.industry and not self._map_industry(business_data.industry):
            description_parts.append(f"Industry: {business_data.industry}")
        
        # Add social profiles
        if business_data.social_profiles:
            description_parts.append("Social Media Profiles:")
            for platform, url in business_data.social_profiles.items():
                if url:
                    description_parts.append(f"• {platform}: {url}")
        
        # Add company size
        if business_data.company_size:
            description_parts.append(f"Company Size: {business_data.company_size}")
        
        # Add Google CID for reference
        if business_data.cid:
            description_parts.append(f"Google CID: {business_data.cid}")
        
        # Add personalization content
        if personalization_content:
            description_parts.append(f"AI-Generated Outreach:\n{personalization_content}")
        
        # Add lead source timestamp
        from datetime import datetime
        description_parts.append(f"Source: Google Maps API - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Use description field (confirmed to work)
        if description_parts:
            comprehensive_description = '\n\n'.join(description_parts)
            lead_data['description'] = comprehensive_description
            print(f"Final description length: {len(comprehensive_description)}")
            print(f"Description preview: {comprehensive_description[:200]}...")
        
        print(f"Final lead_data keys: {list(lead_data.keys())}")
        print("=== END MAPPING DEBUG ===\n")
        
        return lead_data
    
    def bulk_create_leads(self, businesses: List[BusinessData], get_personalization: bool = True) -> List[str]:
        """Create multiple leads in bulk"""
        created_leads = []
        
        for business in businesses:
            try:
                personalization_content = None
                if get_personalization and business.website:
                    # This would call the web scraper service
                    personalization_content = f"Website analysis for {business.business_name}"
                
                lead_name = self.create_lead(business, personalization_content)
                created_leads.append(lead_name)
            
            except Exception as e:
                logging.error(f"Failed to create lead for {business.business_name}: {e}")
                continue
        
        return created_leads

    def add_note_to_lead(self, lead_name: str, note_content: str) -> bool:
        """Add a note to a lead using the Frappe client"""
        try:
            note = {
                'doctype': 'Comment',
                'comment_type': 'Comment',
                'reference_doctype': 'Lead',
                'reference_name': lead_name,
                'content': note_content
            }
            
            result = self.client.insert(note)
            if result and isinstance(result, dict):
                logging.info(f"✅ Note added successfully to lead {lead_name}: {result.get('name')}")
                return True
            else:
                logging.warning(f"⚠️ Note creation returned unexpected result: {result}")
                return False
                
        except Exception as e:
            logging.error(f"❌ Failed to add note to lead {lead_name}: {e}")
            return False
    
    def add_comprehensive_note_to_lead(self, lead_name: str, business_data: BusinessData, personalization_content: Optional[str] = None) -> bool:
        """Add a comprehensive note with all business information to a lead"""
        try:
            note_parts = []
            
            # Add business overview
            if business_data.description:
                note_parts.append(f"📋 BUSINESS OVERVIEW:\n{business_data.description}")
            
            # Add contact information
            contact_info = []
            if business_data.phone:
                contact_info.append(f"📞 Phone: {business_data.phone}")
            if business_data.email:
                contact_info.append(f"📧 Email: {business_data.email}")
            if business_data.website:
                contact_info.append(f"🌐 Website: {business_data.website}")
            if business_data.address:
                contact_info.append(f"📍 Address: {business_data.address}")
            
            if contact_info:
                note_parts.append("📞 CONTACT INFORMATION:\n" + "\n".join(contact_info))
            
            # Add industry and company size
            company_info = []
            if business_data.industry:
                company_info.append(f"🏢 Industry: {business_data.industry}")
            if business_data.company_size:
                company_info.append(f"👥 Company Size: {business_data.company_size}")
            
            if company_info:
                note_parts.append("🏢 COMPANY DETAILS:\n" + "\n".join(company_info))
            
            # Add social profiles
            if business_data.social_profiles:
                social_info = ["📱 SOCIAL MEDIA PROFILES:"]
                for platform, url in business_data.social_profiles.items():
                    if url:
                        social_info.append(f"• {platform}: {url}")
                note_parts.append("\n".join(social_info))
            
            # Add personalization content
            if personalization_content:
                note_parts.append(f"🤖 AI-GENERATED OUTREACH:\n{personalization_content}")
            
            # Add metadata
            from datetime import datetime
            note_parts.append(f"📅 Research Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            note_parts.append(f"🆔 Google CID: {business_data.cid}")
            note_parts.append("🔍 Source: Google Maps API")
            
            comprehensive_note = '\n\n'.join(note_parts)
            
            return self.add_note_to_lead(lead_name, comprehensive_note)
            
        except Exception as e:
            logging.error(f"❌ Failed to create comprehensive note for lead {lead_name}: {e}")
            return False
