import requests
import json
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

@dataclass
class BusinessData:
    business_name: str
    description: str
    cid: str
    social_profiles: Dict[str, str]
    decision_makers: Optional[Dict]
    website: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None

class GMapsDataExtractor:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.gmapsdataextractor.com/search"
        self.headers = {'X-API-Key': api_key}
    
    def search_businesses(self, query: str, location: str = "", count: int = 10) -> List[BusinessData]:
        """
        Search for businesses using the Google Maps API
        
        Args:
            query: Business type/category to search for
            location: Geographic location to search in
            count: Number of results to return
        
        Returns:
            List of BusinessData objects
        """
        search_query = f"{query}"
        if location:
            search_query += f" in {location}"
        
        params = {
            'q': search_query,
            'n': count,
            'social': 'true',
            'leads': 'true',
            'fields': 'businessName,cid,description,phone,email,address,website'
        }
        
        try:
            logging.info(f"Making API request to {self.base_url}")
            logging.info(f"Query: {search_query}")
            
            response = requests.get(self.base_url, headers=self.headers, params=params, timeout=30)
            
            logging.info(f"API Response Status: {response.status_code}")
            
            if response.status_code != 200:
                logging.error(f"API request failed with status {response.status_code}")
                logging.error(f"Response: {response.text}")
                raise Exception(f"API request failed with status {response.status_code}")
            
            # Check if response is empty
            if not response.text:
                logging.error("API returned empty response")
                raise Exception("API returned empty response")
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse JSON response: {e}")
                logging.error(f"Response text: {response.text[:500]}...")
                raise Exception(f"Invalid JSON response from API: {e}")
            
            # Check if data is None or empty
            if data is None:
                logging.error("API returned null data")
                raise Exception("API returned null data")
            
            logging.info(f"API returned data with keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
            return self._parse_response(data)
        
        except requests.Timeout:
            logging.error("API request timed out")
            raise Exception("API request timed out")
        except requests.RequestException as e:
            logging.error(f"API request failed: {e}")
            raise Exception(f"API request failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in API call: {e}")
            raise
    
    def _parse_response(self, response_data: Dict) -> List[BusinessData]:
        """Parse API response into BusinessData objects"""
        if not response_data:
            logging.warning("Empty response data")
            return []
        
        if not isinstance(response_data, dict):
            logging.error(f"Expected dict, got {type(response_data)}")
            return []
        
        # Check for error in response
        if 'error' in response_data:
            error_msg = response_data.get('error', 'Unknown API error')
            logging.error(f"API returned error: {error_msg}")
            raise Exception(f"API Error: {error_msg}")
        
        data_list = response_data.get('data', [])
        
        if not data_list:
            logging.warning("No data found in API response")
            logging.info(f"Response structure: {json.dumps(response_data, indent=2)[:500]}...")
            return []
        
        businesses = []
        
        for item in data_list:
            if not isinstance(item, dict):
                logging.warning(f"Skipping invalid item: {item}")
                continue
            
            try:
                business = self._parse_business_item(item)
                businesses.append(business)
            except Exception as e:
                logging.warning(f"Failed to parse business item: {e}")
                continue
        
        logging.info(f"Successfully parsed {len(businesses)} businesses")
        return businesses
    
    def _parse_business_item(self, item: Dict) -> BusinessData:
        """Parse individual business item"""
        # Extract basic info
        business_name = item.get('businessName', 'Unknown Business')
        description = item.get('description', '')
        cid = item.get('cid', '')
        
        # Debug: Print the raw item to see what we're getting
        print(f"\n=== API DEBUG: {business_name} ===")
        print(f"Raw description: '{description}'")
        print(f"Available keys: {list(item.keys())}")
        
        # Extract social profiles and look for additional description data
        social_data = item.get('socialData')
        social_profiles = {}
        website_overview = None
        
        if social_data and isinstance(social_data, dict):
            social_profiles = social_data.get('socialProfiles', {})
            print(f"Social data keys: {list(social_data.keys())}")
            
            # Check all possible sources of description/overview data
            meta_tags_data = social_data.get('metaTagsData')
            if meta_tags_data:
                print(f"Meta tags data: {meta_tags_data}")
                if isinstance(meta_tags_data, dict):
                    website_overview = meta_tags_data.get('description') or meta_tags_data.get('title')
                    if website_overview:
                        print(f"Found website overview: {website_overview[:100]}...")
            
            # Check analytics data
            analytics = social_data.get('analytics')
            if analytics:
                print(f"Analytics data: {analytics}")
            
            # Check other potential description sources
            for key in ['description', 'summary', 'about', 'bio']:
                if key in social_data and social_data[key]:
                    print(f"Found {key}: {social_data[key]}")
        else:
            print("No social data found")
        
        # Extract decision maker insights for additional company info
        decision_makers = item.get('decisionMakers')
        company_description = None
        
        # Initialize company details
        website = None
        phone = None
        email = None
        address = None
        industry = None
        company_size = None
        
        # Extract from decision makers if available
        if decision_makers and isinstance(decision_makers, dict):
            insights = decision_makers.get('insights', [])
            if insights and isinstance(insights, list) and len(insights) > 0:
                insight = insights[0]
                if isinstance(insight, dict):
                    company_description = insight.get('company.description', '')
                    if company_description:
                        print(f"Found company description from insights: {company_description[:100]}...")
                    website = insight.get('company.website')
                    phone = insight.get('company.phone')
                    address = insight.get('company.address')
                    industry = insight.get('company.industry')
                    company_size = insight.get('company.size')
        
        # Combine all description sources
        combined_description = description
        
        if company_description and company_description != description:
            if combined_description:
                combined_description += f"\n\nCompany Profile: {company_description}"
            else:
                combined_description = company_description
        
        if website_overview and website_overview != description and website_overview != company_description:
            if combined_description:
                combined_description += f"\n\nWebsite Overview: {website_overview}"
            else:
                combined_description = website_overview
        
        print(f"Final combined description: '{combined_description[:200]}...' (length: {len(combined_description)})")
        print("=== END API DEBUG ===\n")
        
        return BusinessData(
            business_name=business_name,
            description=combined_description,
            cid=cid,
            social_profiles=social_profiles,
            decision_makers=decision_makers,
            website=website,
            phone=phone,
            email=email,
            address=address,
            industry=industry,
            company_size=company_size
        )

    def get_website_content(self, business_data: BusinessData) -> Optional[str]:
        """
        Extract website content for personalization
        This would use the web scraper functionality from the API
        """
        if not business_data.website:
            return None
        
        # Implementation would depend on API's web scraper endpoint
        # For now, return placeholder
        return f"Website content analysis for {business_data.business_name}"
