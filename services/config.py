import os
from typing import Optional

class Config:
    # Google Maps API
    GMAPS_API_KEY: str = os.getenv('GMAPS_API_KEY', 'maps-5fb677d3af7c885418cef1106eadfaf603303a50a7972ef9')
    
    # ERPNext Configuration
    ERPNEXT_URL: str = os.getenv('ERPNEXT_URL', 'https://app.taskforgehq.com')
    ERPNEXT_USERNAME: str = os.getenv('ERPNEXT_USERNAME', 'Administrator')
    ERPNEXT_PASSWORD: str = os.getenv('ERPNEXT_PASSWORD', 'admin')
    
    # Google Gemini for personalization
    GEMINI_API_KEY: Optional[str] = os.getenv('AIzaSyB1xxasBK7ZSHyuXPbD6UMcGp1MBj5FuMo')
    
    # Logging
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration"""
        missing_fields = []
        
        if not cls.GMAPS_API_KEY or cls.GMAPS_API_KEY == 'maps-xxxxx':
            missing_fields.append('GMAPS_API_KEY')
        
        if not cls.ERPNEXT_URL or cls.ERPNEXT_URL == 'https://your-erpnext-site.com':
            missing_fields.append('ERPNEXT_URL')
        
        if not cls.ERPNEXT_USERNAME or cls.ERPNEXT_USERNAME == 'your-username':
            missing_fields.append('ERPNEXT_USERNAME')
        
        if not cls.ERPNEXT_PASSWORD or cls.ERPNEXT_PASSWORD == 'your-password':
            missing_fields.append('ERPNEXT_PASSWORD')
        
        if missing_fields:
            print(f"Missing or invalid configuration for: {', '.join(missing_fields)}")
            return False
        
        return True
