from typing import List, Dict, Optional
from services.gmaps_service import BusinessData
import logging
import re

class LeadFilterService:
    def __init__(self):
        self.filter_criteria = {}
    
    def parse_filter_requirements(self, user_input: str) -> Dict:
        """
        Parse user input to extract filtering requirements
        Enhanced to handle multiple filters like "with phone numbers and websites"
        """
        filters = {}
        user_input_lower = user_input.lower()
        
        # Check for website requirements
        website_patterns = [
            r'(?:with|having|who\s+have?)\s+.*?(?:and\s+)?(?:a\s+)?websites?',
            r'(?:with|having|who\s+have?)\s+(?:a\s+)?websites?',
            r'that\s+have?\s+(?:a\s+)?websites?'
        ]
        
        for pattern in website_patterns:
            if re.search(pattern, user_input_lower):
                filters['requires_website'] = True
                break
        
        # Check for phone/mobile requirements  
        phone_patterns = [
            r'(?:with|having|who\s+have?)\s+.*?(?:and\s+)?(?:phone\s+numbers?|mobile\s+numbers?|phone|mobile)',
            r'(?:with|having|who\s+have?)\s+(?:phone\s+numbers?|mobile\s+numbers?|phone|mobile)',
            r'that\s+have?\s+(?:phone\s+numbers?|mobile\s+numbers?|phone|mobile)'
        ]
        
        for pattern in phone_patterns:
            if re.search(pattern, user_input_lower):
                filters['requires_phone'] = True
                break
        
        # Check for email requirements
        email_patterns = [
            r'(?:with|having|who\s+have?)\s+.*?(?:and\s+)?(?:emails?|email\s+addresses?)',
            r'(?:with|having|who\s+have?)\s+(?:emails?|email\s+addresses?)',
            r'that\s+have?\s+(?:emails?|email\s+addresses?)'
        ]
        
        for pattern in email_patterns:
            if re.search(pattern, user_input_lower):
                filters['requires_email'] = True
                break
        
        # Check for social media requirements
        social_patterns = [
            r'(?:with|having|who\s+have?)\s+.*?(?:and\s+)?(?:social\s+media|social\s+profiles?|linkedin|twitter|facebook)',
            r'(?:with|having|who\s+have?)\s+(?:social\s+media|social\s+profiles?)',
            r'that\s+have?\s+(?:social\s+media|social\s+profiles?)'
        ]
        
        for pattern in social_patterns:
            if re.search(pattern, user_input_lower):
                filters['requires_social'] = True
                break
        
        # Check for specific social platforms
        if re.search(r'(?:with|having)\s+.*?linkedin', user_input_lower):
            filters['requires_linkedin'] = True
        if re.search(r'(?:with|having)\s+.*?twitter', user_input_lower):
            filters['requires_twitter'] = True
        if re.search(r'(?:with|having)\s+.*?facebook', user_input_lower):
            filters['requires_facebook'] = True
        
        # Enhanced "and" detection for multiple requirements
        if ' and ' in user_input_lower:
            # Split by "and" and check each part
            parts = user_input_lower.split(' and ')
            for part in parts:
                if 'website' in part:
                    filters['requires_website'] = True
                if any(word in part for word in ['phone', 'mobile', 'number']):
                    filters['requires_phone'] = True
                if 'email' in part:
                    filters['requires_email'] = True
                if any(word in part for word in ['social', 'linkedin', 'twitter', 'facebook']):
                    filters['requires_social'] = True
        
        # Company size requirements (unchanged)
        size_patterns = [
            r'(?:with|having)\s+(?:more than|over|\>)\s*(\d+)\s*(?:employees?|people)',
            r'(?:with|having)\s+(?:less than|under|\<)\s*(\d+)\s*(?:employees?|people)',
            r'(?:with|having)\s+(\d+)\s*(?:\-|to)\s*(\d+)\s*(?:employees?|people)'
        ]
        
        for pattern in size_patterns:
            match = re.search(pattern, user_input_lower)
            if match:
                if 'more than' in pattern or 'over' in pattern:
                    filters['min_employees'] = int(match.group(1))
                elif 'less than' in pattern or 'under' in pattern:
                    filters['max_employees'] = int(match.group(1))
                else:
                    filters['min_employees'] = int(match.group(1))
                    filters['max_employees'] = int(match.group(2))
                break
        
        return filters
    
    def filter_businesses(self, businesses: List[BusinessData], filters: Dict) -> List[BusinessData]:
        """
        Filter businesses based on criteria
        """
        if not filters:
            return businesses
        
        filtered = []
        
        for business in businesses:
            if self._meets_criteria(business, filters):
                filtered.append(business)
        
        logging.info(f"Filtered {len(businesses)} businesses down to {len(filtered)} based on criteria: {filters}")
        return filtered
    
    def _meets_criteria(self, business: BusinessData, filters: Dict) -> bool:
        """Check if a business meets the filtering criteria"""
        
        # Website requirement
        if filters.get('requires_website'):
            if not business.website or business.website.strip() == '':
                return False
        
        # Phone requirement
        if filters.get('requires_phone'):
            if not business.phone or business.phone.strip() == '':
                return False
        
        # Email requirement
        if filters.get('requires_email'):
            if not business.email or business.email.strip() == '':
                return False
        
        # Social media requirement
        if filters.get('requires_social'):
            if not business.social_profiles or not any(business.social_profiles.values()):
                return False
        
        # Specific social platform requirements
        if filters.get('requires_linkedin'):
            if not business.social_profiles or not business.social_profiles.get('LinkedIn'):
                return False
        
        if filters.get('requires_twitter'):
            twitter_keys = ['Twitter', 'X (Twitter)', 'X']
            if not business.social_profiles or not any(business.social_profiles.get(key) for key in twitter_keys):
                return False
        
        if filters.get('requires_facebook'):
            if not business.social_profiles or not business.social_profiles.get('Facebook'):
                return False
        
        # Company size requirements
        if filters.get('min_employees') or filters.get('max_employees'):
            company_size = business.company_size
            if company_size:
                employee_count = self._extract_employee_count(company_size)
                if employee_count:
                    if filters.get('min_employees') and employee_count < filters['min_employees']:
                        return False
                    if filters.get('max_employees') and employee_count > filters['max_employees']:
                        return False
                else:
                    # If we can't determine size but size is required, exclude
                    if filters.get('min_employees') or filters.get('max_employees'):
                        return False
        
        return True
    
    def _extract_employee_count(self, company_size: str) -> Optional[int]:
        """Extract approximate employee count from company size string"""
        if not company_size:
            return None
        
        size_lower = company_size.lower()
        
        # Common patterns
        size_mappings = {
            '1-10': 5,
            '11-50': 30,
            '51-200': 125,
            '201-500': 350,
            '501-1000': 750,
            '1001-5000': 3000,
            '5001-10000': 7500,
            '10000+': 15000,
            'self-employed': 1,
            'freelance': 1
        }
        
        for size_key, count in size_mappings.items():
            if size_key.lower() in size_lower:
                return count
        
        # Try to extract numbers
        numbers = re.findall(r'\d+', company_size)
        if numbers:
            return int(numbers[0])
        
        return None
    
    def get_filter_summary(self, filters: Dict) -> str:
        """Generate a human-readable summary of applied filters"""
        if not filters:
            return "No filters applied"
        
        summary_parts = []
        
        if filters.get('requires_website'):
            summary_parts.append("must have website")
        if filters.get('requires_phone'):
            summary_parts.append("must have phone number")
        if filters.get('requires_email'):
            summary_parts.append("must have email")
        if filters.get('requires_social'):
            summary_parts.append("must have social media")
        if filters.get('requires_linkedin'):
            summary_parts.append("must have LinkedIn")
        if filters.get('requires_twitter'):
            summary_parts.append("must have Twitter")
        if filters.get('requires_facebook'):
            summary_parts.append("must have Facebook")
        
        if filters.get('min_employees'):
            summary_parts.append(f"minimum {filters['min_employees']} employees")
        if filters.get('max_employees'):
            summary_parts.append(f"maximum {filters['max_employees']} employees")
        
        return "Filters: " + ", ".join(summary_parts)
