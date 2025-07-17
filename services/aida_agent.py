import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
import logging
import google.generativeai as genai
import uuid
import time
import os
import re

from langchain.agents import Tool, AgentExecutor, create_react_agent
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import BaseMessage, HumanMessage, AIMessage
from langchain.callbacks.base import BaseCallbackHandler # Not used, but kept as it was in original imports
from langchain_core.runnables.history import RunnableWithMessageHistory

from frappeclient import FrappeClient

# Import CRM agents and services
try:
    import sys
    import os
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, parent_dir)
    
    from agents.lead_creation_agent import LeadCreationAgent
    from agents.lead_outreach_agent import LeadOutreachAgent
    CRM_FUNCTIONALITY_AVAILABLE = True
except ImportError as e:
    print(f"Warning: CRM functionality not available: {e}")
    # Create placeholder classes
    class LeadCreationAgent:
        def __init__(self, *args, **kwargs):
            pass
        def create_leads(self, *args, **kwargs):
            return {"error": "CRM functionality not available"}
    
    class LeadOutreachAgent:
        def __init__(self, *args, **kwargs):
            pass
        def generate_outreach_email(self, *args, **kwargs):
            return "CRM functionality not available"
    
    CRM_FUNCTIONALITY_AVAILABLE = False

try:
    from services.gmaps_service import GMapsDataExtractor
    from services.personalization_service import PersonalizationService
    from services.email_service import EmailService
    from config import Config
    CRM_AVAILABLE = True
except ImportError as e:
    logging.warning(f"CRM functionality not available: {e}")
    CRM_AVAILABLE = False

# Add MongoDB import
try:
    from pymongo import MongoClient
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    print("⚠️ MongoDB not available. Install with: pip install pymongo")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MongoMemoryManager:
    """MongoDB-based memory manager for persistent conversation storage."""
    
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017/", db_name: str = "aida_ai_conversations"):
        self.mongo_available = MONGODB_AVAILABLE
        self.client = None
        self.db = None
        self.conversations = None

        if self.mongo_available:
            try:
                self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000) # 5 second timeout
                # The ismaster command is cheap and does not require auth.
                self.client.admin.command('ismaster')
                self.db = self.client[db_name]
                self.conversations = self.db.conversations
                logger.info("🍃 MongoDB connection established for memory management.")
                # Ensure indexes exist
                self.conversations.create_index([("session_id", 1), ("timestamp", -1)])
            except (ConnectionFailure, ServerSelectionTimeoutError) as e:
                logger.warning(f"MongoDB connection failed: {e}. Falling back to in-memory storage.")
                self.mongo_available = False
            except Exception as e:
                logger.error(f"Unexpected error during MongoDB connection: {e}. Falling back to in-memory storage.")
                self.mongo_available = False
        
        if not self.mongo_available:
            logger.info("📝 Using in-memory conversation storage as MongoDB is not available or failed to connect.")
            self._in_memory_conversations = {} # For conversation history fallback

    def store_conversation(self, session_id: str, user_message: str, ai_response: str, 
                             query_result: Dict[str, Any] = None, doctype: str = None):
        """Store conversation message with context."""
        conversation_entry = {
            "session_id": session_id,
            "timestamp": datetime.now(),
            "user_message": user_message,
            "ai_response": ai_response,
            "query_result": query_result, # Store the full result for potential future context
            "doctype": doctype
        }
        
        if self.mongo_available:
            try:
                result = self.conversations.insert_one(conversation_entry)
                logger.debug(f"Stored conversation in MongoDB: {result.inserted_id}")
            except Exception as e:
                logger.error(f"Failed to store conversation in MongoDB: {e}. Falling back to in-memory for this entry.")
                self._store_in_memory(session_id, conversation_entry)
        else:
            self._store_in_memory(session_id, conversation_entry)

    def _store_in_memory(self, session_id: str, entry: Dict[str, Any]):
        """Helper to store entries in in-memory dict."""
        if session_id not in self._in_memory_conversations:
            self._in_memory_conversations[session_id] = []
        # Corrected typo: `self._in_in_memory_conversations` to `self._in_memory_conversations`
        self._in_memory_conversations[session_id].append(entry)
        # Keep history limited to a reasonable number of turns
        if len(self._in_memory_conversations[session_id]) > 20: 
            self._in_memory_conversations[session_id] = self._in_memory_conversations[session_id][-20:]

    def get_recent_context(self, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent conversation context."""
        if self.mongo_available:
            try:
                cursor = self.conversations.find(
                    {"session_id": session_id}
                ).sort("timestamp", -1).limit(limit)
                return list(cursor)
            except Exception as e:
                logger.error(f"Failed to retrieve from MongoDB: {e}. Falling back to in-memory.")
                context = self._in_memory_conversations.get(session_id, [])
                return context[-limit:] if context else []
        
        context = self._in_memory_conversations.get(session_id, [])
        return context[-limit:] if context else []

    def get_last_query_result(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get the last stored query result for context."""
        recent = self.get_recent_context(session_id, 1)
        if recent and recent[0].get("query_result"):
            return recent[0]["query_result"]
        return None

class AidaERPNextAgent:
    """
    Aida AI - A friendly ERPNext assistant using LangChain and Gemini 2.0 Flash.
    Enhanced with dynamic app and doctype discovery, improved CRUD, better conversational memory,
    onboarding/Q&A functionality, and separated lead creation system.
    """
    
    def __init__(self, erpnext_url: str, username: str, password: str, google_api_key: str, 
                 mongo_uri: str = None, session_id: str = None, site_base_url: str = None):
        """
        Initialize the Aida ERPNext Agent with enhanced onboarding and separated lead creation capabilities.
        
        Args:
            erpnext_url: ERPNext server URL
            username: ERPNext username
            password: ERPNext password
            google_api_key: Google AI API key
            mongo_uri: MongoDB connection string (optional)
            session_id: Unique session identifier (optional)
            site_base_url: Base URL for generating clickable links (optional, defaults to erpnext_url)
        """
        self.site_base_url = site_base_url or erpnext_url
        # Call the original initialization logic
        self._original_init(erpnext_url, username, password, google_api_key, mongo_uri, session_id)
    
    def _original_init(self, erpnext_url: str, username: str, password: str, google_api_key: str, 
                      mongo_uri: str = None, session_id: str = None):
        pass  # This method body was removed during cleanup
    
    def discover_installed_apps(self) -> Dict[str, Any]:
        """Use hardcoded app list to avoid permission issues with frappe.get_installed_apps."""
        logger.info("Using hardcoded app list to avoid permission issues.")
        
        # Use hardcoded fallback apps to avoid permission issues
        fallback_apps_list = [
            {'name': 'erpnext', 'title': 'ERPNext', 'description': 'Main ERP application', 'version': 'Unknown', 'doctype_count': 0, 'doctypes': []},
            {'name': 'frappe', 'title': 'Frappe Framework', 'description': 'Core framework', 'version': 'Unknown', 'doctype_count': 0, 'doctypes': []},
            {'name': 'hrms', 'title': 'HRMS', 'description': 'Human Resources Management', 'version': 'Unknown', 'doctype_count': 0, 'doctypes': []},
            {'name': 'helpdesk', 'title': 'Helpdesk', 'description': 'Customer Support', 'version': 'Unknown', 'doctype_count': 0, 'doctypes': []}
        ]
        
        apps_dict = {}
        for app in fallback_apps_list:
            apps_dict[app['name']] = app
        
        logger.info(f"📱 Using hardcoded app list: {list(apps_dict.keys())}")
        return apps_dict
    
    def discover_doctypes(self) -> Dict[str, Any]:
        """Discover available doctypes with enhanced error handling."""
        doctypes_dict = {}
        
        try:
            logger.info("Discovering doctypes...")
            
            doctypes_response = self._safe_erpnext_call(
                'get_list',
                'DocType',
                fields=['name', 'module', 'custom', 'is_submittable', 'track_changes', 'description'],
                filters={'istable': 0},
                limit_page_length=2000 # Increased limit
            )
            
            if doctypes_response:
                module_counts = {}
                
                for doctype in doctypes_response:
                    name = doctype.get('name', '')
                    module = doctype.get('module', 'Unknown')
                    description = doctype.get('description', '')
                    
                    if module not in module_counts:
                        module_counts[module] = 0
                    module_counts[module] += 1
                    
                    # Map module to app (using the same logic as app discovery fallback or improve here)
                    app_name = "unknown_app"
                    # Try to find which discovered app this doctype belongs to
                    for app_key, app_info in self.installed_apps.items():
                        if name in app_info.get('doctypes', []): # Check if this doctype was already associated during app discovery
                            app_name = app_key
                            break
                    if app_name == "unknown_app": # If not found by direct mapping, try module-to-app mapping heuristics
                        module_to_app_rough = {
                            'Accounts': 'erpnext', 'Selling': 'erpnext', 'Buying': 'erpnext', 'Stock': 'erpnext',
                            'Manufacturing': 'erpnext', 'Projects': 'erpnext', 'HR': 'hrms', 'CRM': 'erpnext',
                            'Support': 'helpdesk', 'Core': 'frappe', 'Website': 'frappe', 'Desk': 'frappe',
                            'Email': 'frappe', 'Printing': 'frappe', 'Custom': 'frappe', 'Automation': 'frappe',
                            'Integrations': 'frappe', 'Contacts': 'frappe', 'Social': 'frappe', 'Geo': 'frappe',
                            'Workflow': 'frappe', 'Data Migration': 'frappe', 'Drive': 'drive', 'File Manager': 'drive',
                            'Insights': 'insights', 'Dashboard': 'insights', 'Query Report': 'insights', 'Report': 'insights',
                            'Wiki': 'wiki', 'Knowledge Base': 'wiki', 'Raven': 'raven', 'Communication': 'raven',
                            'Payroll': 'hrms', 'Loan Management': 'hrms', 'Regional': 'erpnext', 'Assets': 'erpnext',
                            'Utilities': 'erpnext', 'Quality Management': 'erpnext', 'Agriculture': 'erpnext',
                            'Education': 'erpnext', 'Healthcare': 'erpnext', 'Non Profit': 'erpnext',
                            'Restaurant': 'erpnext', 'Retail': 'erpnext', 'Issue': 'helpdesk'
                        }
                        app_name = module_to_app_rough.get(module, module.lower().replace(' ', '_'))

                    doctypes_dict[name] = {
                        'name': name,
                        'module': module,
                        'app': app_name,
                        'custom': bool(doctype.get('custom', 0)),
                        'is_submittable': bool(doctype.get('is_submittable', 0)),
                        'track_changes': bool(doctype.get('track_changes', 0)),
                        'category': self._categorize_doctype(name, module),
                        'description': description # Storing description
                    }
                
                logger.info(f"📋 Discovered {len(doctypes_dict)} doctypes across {len(module_counts)} modules.")
                
                sorted_modules = sorted(module_counts.items(), key=lambda x: x[1], reverse=True)
                for module, count in sorted_modules[:8]:
                    logger.info(f"      - {module}: {count} doctypes")
                
        except Exception as e:
            logger.error(f"Error discovering doctypes: {str(e)}. Using hardcoded fallback doctypes.")
            common_doctypes = [
                'Customer', 'Supplier', 'Item', 'Sales Order', 'Purchase Order',
                'Sales Invoice', 'Purchase Invoice', 'Payment Entry', 'Lead', 'Quotation', 'Opportunity',
                'Project', 'Task', 'Employee', 'Leave Application', 'Expense Claim',
                'Journal Entry', 'GL Entry', 'Stock Entry', 'Delivery Note', 'Purchase Receipt'
            ]
            for doctype_name in common_doctypes:
                doctypes_dict[doctype_name] = {
                    'name': doctype_name,
                    'module': 'Unknown',
                    'app': 'erpnext',
                    'custom': False,
                    'is_submittable': False,
                    'track_changes': False,
                    'category': self._categorize_doctype(doctype_name, 'Unknown'),
                    'description': f"Common ERPNext doctype for {doctype_name}."
                }
            logger.info(f"Using fallback list of {len(doctypes_dict)} common doctypes.")
            
        return doctypes_dict

    def _categorize_doctype(self, doctype_name: str, module_name: str) -> str:
        """Categorize doctypes for better organization and search."""
        doctype_name_lower = doctype_name.lower()
        module_name_lower = module_name.lower()

        if "sale" in doctype_name_lower or "customer" in doctype_name_lower or "quotation" in doctype_name_lower or "lead" in doctype_name_lower or "opportunity" in doctype_name_lower or "crm" in module_name_lower:
            return "Sales & CRM"
        if "purchase" in doctype_name_lower or "supplier" in doctype_name_lower or "buying" in module_name_lower:
            return "Purchase & Procurement"
        if "item" in doctype_name_lower or "stock" in doctype_name_lower or "inventory" in doctype_name_lower or "delivery" in doctype_name_lower:
            return "Stock & Inventory"
        if "manufactur" in doctype_name_lower or "bom" in doctype_name_lower or "production" in doctype_name_lower:
            return "Manufacturing"
        if "account" in doctype_name_lower or "invoice" in doctype_name_lower or "payment" in doctype_name_lower or "journal" in doctype_name_lower or "ledger" in doctype_name_lower or "currency" in doctype_name_lower:
            return "Accounting & Finance"
        if "employee" in doctype_name_lower or "hr" in module_name_lower or "leave" in doctype_name_lower or "payroll" in doctype_name_lower:
            return "HR & Payroll"
        if "project" in doctype_name_lower or "task" in doctype_name_lower:
            return "Projects"
        if "support" in doctype_name_lower or "issue" in doctype_name_lower or "helpdesk" in module_name_lower:
            return "Support & Helpdesk"
        if "report" in doctype_name_lower or "dashboard" in doctype_name_lower or "analytics" in module_name_lower or "insights" in module_name_lower:
            return "Analytics & Reports"
        if "user" in doctype_name_lower or "role" in doctype_name_lower or "permission" in doctype_name_lower or "system" in module_name_lower or "setup" in doctype_name_lower:
            return "System & Configuration"
        if "website" in doctype_name_lower or "blog" in doctype_name_lower or "web page" in doctype_name_lower:
            return "Content & Website"
        if "file" in doctype_name_lower or "folder" in doctype_name_lower or "drive" in module_name_lower:
            return "File Management"
        if "communication" in doctype_name_lower or "email" in doctype_name_lower or "notification" in doctype_name_lower or "event" in doctype_name_lower:
            return "Communication & Events"
        if "wiki" in doctype_name_lower or "knowledge" in doctype_name_lower:
            return "Knowledge Management"
        if "automation" in doctype_name_lower or "workflow" in doctype_name_lower:
            return "Automation & Workflow"
        
        return "Other"

    def _original_init(self, erpnext_url: str, username: str, password: str, google_api_key: str, 
                     mongo_uri: Optional[str] = None, session_id: Optional[str] = None,
                     gmaps_api_key: Optional[str] = None):
        """Initialize Enhanced Aida AI agent with CRM capabilities."""
        self.erpnext_url = erpnext_url
        self.username = username
        self.session_id = session_id or str(uuid.uuid4())
        self.gmaps_api_key = gmaps_api_key
        
        try:
            self.erpnext_client = FrappeClient(erpnext_url, username, password)
            logger.info(f"✅ Connected to ERPNext at {erpnext_url}")
            # Check for specific methods for version compatibility
            # FrappeClient has standard post_api method
            logger.debug("Using standard FrappeClient methods for API calls")
        except Exception as e:
            logger.error(f"❌ Failed to connect to ERPNext: {str(e)}")
            raise Exception(f"ERPNext connection failed: {str(e)}. Please check URL and credentials.")
        
        # Test connection with a simple, safe call
        try:
            test_response = self._safe_erpnext_call("call", "frappe.ping", {}) # Pass empty params dict
            logger.info("✅ ERPNext connection verified via frappe.ping.")
        except Exception as e:
            try:
                test_response = self._safe_erpnext_call("get_list", 'User', limit_page_length=1) # Use _safe_erpnext_call
                logger.info("✅ ERPNext connection verified via User query.")
            except Exception as e2:
                logger.error(f"❌ ERPNext connection test failed: {str(e2)}. Proceeding with potentially limited ERPNext access.")
        
        try:
            genai.configure(api_key=google_api_key)
            self.llm = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                api_key=google_api_key,
                temperature=0.1
            )
            logger.info("✅ Google Gemini API configured.")
        except Exception as e:
            logger.error(f"❌ Google API configuration failed: {str(e)}")
            raise Exception(f"Google API configuration failed: {str(e)}. Please check your API key.")
        
        # Initialize memory manager with better error handling
        try:
            self.memory_manager = MongoMemoryManager(mongo_uri)
            logger.info(f"✅ Memory management initialized. Mongo available: {self.memory_manager.mongo_available}")
        except Exception as mongo_error:
            logger.warning(f"MongoDB connection failed: {mongo_error}")
            # Create a fallback memory manager that doesn't use MongoDB
            self.memory_manager = MongoMemoryManager(None)
            logger.info("✅ Memory management initialized with in-memory fallback (MongoDB unavailable)")
        
        # Initialize CRM agents if available
        self.lead_creation_agent = None
        self.lead_outreach_agent = None
        self.gmaps_extractor = None
        
        if CRM_AVAILABLE and self.gmaps_api_key:
            try:
                # Initialize lead creation agent
                self.lead_creation_agent = LeadCreationAgent(
                    erpnext_url=erpnext_url,
                    username=username,
                    password=password,
                    gmaps_api_key=self.gmaps_api_key,
                    gemini_api_key=google_api_key
                )
                
                # Initialize lead outreach agent
                self.lead_outreach_agent = LeadOutreachAgent(
                    erpnext_url=erpnext_url,
                    username=username,
                    password=password,
                    gemini_api_key=google_api_key
                )
                
                # Initialize Google Maps extractor
                self.gmaps_extractor = GMapsDataExtractor(self.gmaps_api_key)
                
                logger.info("✅ CRM agents initialized successfully")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize CRM agents: {e}")
        elif not self.gmaps_api_key:
            logger.info("ℹ️ Google Maps API key not provided - CRM features disabled")
        else:
            logger.info("ℹ️ CRM dependencies not available - CRM features disabled")
        
        # Discover system capabilities with error handling
        self.installed_apps: Dict[str, Any] = {}
        self.all_doctypes: Dict[str, Any] = {}

        try:
            logger.info("🔍 Discovering ERPNext system capabilities...")
            # First, discover apps (which also tries to get app-doctype mapping)
            self.installed_apps = self.discover_installed_apps()
            # Then discover all doctypes, which will also assign categories and descriptions
            self.all_doctypes = self.discover_doctypes()

            # Now, update installed_apps with the actual doctypes found during discover_doctypes
            for app_name, app_info in self.installed_apps.items():
                app_doctypes = [
                    dt_name for dt_name, dt_info in self.all_doctypes.items()
                    if dt_info.get('app') == app_name
                ]
                app_info['doctypes'] = app_doctypes
                app_info['doctype_count'] = len(app_doctypes)
                self.installed_apps[app_name] = app_info

            logger.info(f"🤖 Aida AI Agent initialized successfully!")
            logger.info(f"📱 Discovered {len(self.installed_apps)} installed apps.")
            logger.info(f"📋 Found {len(self.all_doctypes)} available doctypes.")
            
        except Exception as e:
            logger.error(f"System discovery failed, using minimal configuration: {str(e)}", exc_info=True)
            self.installed_apps = {
                'erpnext': {'name': 'erpnext', 'title': 'ERPNext', 'description': 'Main ERP', 'version': 'Unknown', 'doctype_count': 2, 'doctypes': ['Customer', 'Sales Order']},
                'frappe': {'name': 'frappe', 'title': 'Frappe Framework', 'description': 'Core Framework', 'version': 'Unknown', 'doctype_count': 0, 'doctypes': ['User', 'DocType']}
            }
            self.all_doctypes = {
                'Customer': {'name': 'Customer', 'module': 'CRM', 'custom': False, 'category': 'Sales & CRM', 'app': 'erpnext', 'description': 'Manages customer details'},
                'Sales Order': {'name': 'Sales Order', 'module': 'Selling', 'custom': False, 'category': 'Sales & CRM', 'app': 'erpnext', 'description': 'Represents a commitment to sell goods/services'},
                'User': {'name': 'User', 'module': 'Core', 'custom': False, 'category': 'System & Configuration', 'app': 'frappe', 'description': 'System user account'},
                'DocType': {'name': 'DocType', 'module': 'Core', 'custom': False, 'category': 'System & Configuration', 'app': 'frappe', 'description': 'Defines document structure'}
            }
        
        self.conversation_history = ChatMessageHistory()
        self.tools = self._create_tools()
        
        try:
            self.agent_executor = self._create_agent()
            logger.info("✅ Agent executor created successfully.")
        except Exception as e:
            logger.error(f"❌ Agent creation failed: {str(e)}", exc_info=True)
            raise Exception(f"Agent creation failed: {str(e)}")

    def _safe_erpnext_call(self, operation: str, *args, **kwargs):
        """Make ERPNext API calls with error handling and retries."""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                if operation == "get_list":
                    return self.erpnext_client.get_list(*args, **kwargs)
                elif operation == "get_doc":
                    return self.erpnext_client.get_doc(*args, **kwargs)
                elif operation == "insert":
                    return self.erpnext_client.insert(*args, **kwargs)
                elif operation == "update":
                    return self.erpnext_client.update(*args, **kwargs)
                elif operation == "delete":
                    return self.erpnext_client.delete(*args, **kwargs)
                elif operation == "call":
                    method_name = args[0]
                    params = args[1] if len(args) > 1 else {}
                    # Ensure params is a dict for proper API call
                    if not isinstance(params, dict):
                        params = {}
                    # Use post_request for method calls - this is the correct way
                    call_params = {'cmd': method_name}
                    call_params.update(params)
                    return self.erpnext_client.post_request(call_params)
                elif operation == "post_api":
                    # Use the correct post_api method from frappeclient
                    method_path = args[0]
                    params = args[1] if len(args) > 1 else kwargs
                    if not isinstance(params, dict):
                        params = {}
                    return self.erpnext_client.post_api(method_path, params)
                else:
                    raise ValueError(f"Unknown ERPNext operation: {operation}")
                    
            except Exception as e:
                logger.warning(f"ERPNext API call failed (attempt {attempt + 1}/{max_retries}) for operation '{operation}': {str(e)}")
                if attempt == max_retries - 1:
                    logger.error(f"All ERPNext API retry attempts failed for {operation}.")
                    raise e
                time.sleep(retry_delay)
                retry_delay *= 2
        
        return None

    def _fetch_records_tool(self, input_str: str) -> str:
        """Fetch records from any ERPNext doctype. Input should be JSON with 'doctype' (string, required), optional 'filters' (dict), 'fields' (list of strings), 'limit' (int), and 'order_by' (string, e.g., 'creation desc')."""
        try:
            logger.info(f"Tool 'fetch_records' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            
            if not doctype:
                return json.dumps({"success": False, "message": "Doctype is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})
            
            filters = params.get("filters", {})
            fields = params.get("fields", [])
            limit = params.get("limit", 20)
            order_by = params.get("order_by", "creation desc")
            
            try:
                records = self._safe_erpnext_call(
                    "get_list",
                    doctype,
                    fields=fields if fields else ["name"],
                    filters=filters,
                    limit_page_length=limit,
                    order_by=order_by
                )
                
                if records:
                    return json.dumps({
                        "success": True,
                        "records": records,
                        "count": len(records),
                        "doctype": doctype,
                        "message": f"Found {len(records)} records for {doctype}."
                    })
                else:
                    return json.dumps({
                        "success": True,
                        "records": [],
                        "count": 0,
                        "doctype": doctype,
                        "message": f"No {doctype} records found matching the criteria."
                    })
                    
            except Exception as api_error:
                logger.warning(f"Failed to fetch records with specified fields/filters: {api_error}. Attempting minimal fetch.")
                # Fallback to fetching only 'name' field with fewer filters if initial fails
                try:
                    minimal_records = self._safe_erpnext_call(
                        "get_list",
                        doctype,
                        fields=["name"],
                        filters={}, # Remove potentially problematic filters
                        limit_page_length=min(limit, 10) # Smaller limit for fallback
                    )
                    
                    return json.dumps({
                        "success": True,
                        "records": minimal_records,
                        "count": len(minimal_records) if minimal_records else 0,
                        "message": f"Retrieved {doctype} records with limited fields due to potential permission restrictions or complex filters: {str(api_error)}. Please refine your query if needed."
                    })
                    
                except Exception as fallback_error:
                    logger.error(f"Even minimal fetch failed for {doctype}: {fallback_error}")
                    return json.dumps({
                        "success": False,
                        "message": f"Unable to fetch {doctype} records: {str(fallback_error)}. It might be a permission issue or the doctype is not accessible.",
                        "error_type": type(fallback_error).__name__
                    })
                    
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for fetch_records. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in fetch_records_tool: {str(e)}", exc_info=True)
            return json.dumps({
                "success": False,
                "message": f"Error processing request: {str(e)}",
                "error_type": type(e).__name__
            })

    def _read_record_tool(self, input_str: str) -> str:
        """Fetch a single record by its name (ID) from any ERPNext doctype. Input should be JSON with 'doctype' (string, required) and 'name' (string, required, the ID of the record). Example: {"doctype": "Customer", "name": "CUST-00001"}"""
        try:
            logger.info(f"Tool 'read_record' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            record_name = params.get("name")

            if not doctype:
                return json.dumps({"success": False, "message": "Doctype is required."})
            if not record_name:
                return json.dumps({"success": False, "message": "Record name (ID) is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})

            try:
                record = self._safe_erpnext_call("get_doc", doctype, record_name)
                if record:
                    return json.dumps({
                        "success": True,
                        "doctype": doctype,
                        "name": record_name,
                        "record": record,
                        "message": f"Successfully retrieved record '{record_name}' from {doctype}."
                    })
                else:
                    return json.dumps({
                        "success": False,
                        "message": f"Record '{record_name}' not found in {doctype}.",
                        "error_type": "RecordNotFound"
                    })
            except Exception as api_error:
                logger.error(f"Failed to retrieve record '{record_name}' from {doctype}: {api_error}")
                return json.dumps({
                    "success": False,
                    "message": f"Error retrieving record '{record_name}' from {doctype}: {str(api_error)}. It might not exist or there are permission issues.",
                    "error_type": type(api_error).__name__
                })
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for read_record. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _read_record_tool: {str(e)}", exc_info=True)
            return json.dumps({
                "success": False,
                "message": f"Error processing request: {str(e)}",
                "error_type": type(e).__name__
            })

    def _get_doctype_fields_tool(self, input_str: str) -> str:
        """Get all available fields for a specific doctype including which fields are required, their types, and options. Input must be JSON with 'doctype' field. Example: {"doctype": "Sales Order"}"""
        try:
            logger.info(f"Tool 'get_doctype_fields' called with input: {input_str}")
            params = json.loads(input_str)
            doctype_name = params.get("doctype")
            if not doctype_name:
                return json.dumps({"success": False, "error": "Doctype name is required in JSON input {'doctype': 'DocTypeName'}"})
            if doctype_name not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype_name}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})

            logger.info(f"Fetching fields for Doctype: {doctype_name}")
            
            try:
                meta = None
                # Method 1: Try the common getdoctype method
                try:
                    meta = self._safe_erpnext_call('call', 'frappe.desk.form.load.getdoctype', {
                        'doctype': doctype_name,
                        'with_parent': True
                    })
                except Exception as call_error:
                    logger.debug(f"call method for getdoctype failed: {call_error}")
                
                # Method 2: Try direct API call as fallback for different FrappeClient versions
                if not meta or not meta.get('docs'):
                    try:
                        meta = self.erpnext_client.get_api(
                            "method/frappe.desk.form.load.getdoctype",
                            params={'doctype': doctype_name, 'with_parent': True}
                        )
                    except Exception as direct_error:
                        logger.debug(f"Direct API call for getdoctype failed: {direct_error}")
                
                # Method 3: Try getting doctype meta directly from DocType doctype (less detailed fields usually)
                if not meta or not meta.get('docs'):
                    try:
                        meta_doc = self._safe_erpnext_call('get_doc', 'DocType', doctype_name)
                        if meta_doc:
                            meta = {'docs': [meta_doc]}
                    except Exception as meta_error:
                        logger.debug(f"Direct DocType fetch failed: {meta_error}")

                if not meta or 'docs' not in meta or not meta['docs']:
                    # Fallback: try to get basic info by fetching a single record and inferring fields
                    logger.warning(f"Could not get detailed doctype metadata for {doctype_name}. Attempting to infer fields from sample record.")
                    try:
                        sample_records = self._safe_erpnext_call(
                            "get_list",
                            doctype_name,
                            fields=['*'], # Request all fields
                            limit_page_length=1
                        )
                        
                        if sample_records and isinstance(sample_records, list) and sample_records:
                            fields_info = []
                            for field_name in sample_records[0].keys():
                                # Heuristics for common field types if metadata is not available
                                field_type = "Data"
                                if field_name.endswith("_date") or field_name.endswith("_at") or "date" in field_name:
                                    field_type = "Date"
                                elif "time" in field_name:
                                    field_type = "Time"
                                elif field_name.endswith("_id") or "email" in field_name or "phone" in field_name or "website" in field_name:
                                    field_type = "Data"
                                elif "is_" in field_name or "has_" in field_name or "active" in field_name:
                                    field_type = "Check"
                                elif "amount" in field_name or "rate" in field_name or "price" in field_name or "total" in field_name or "value" in field_name:
                                    field_type = "Currency"
                                elif "qty" in field_name or "count" in field_name:
                                    field_type = "Int"
                                elif "name" in field_name and field_name != "name" and field_name != doctype_name.lower() + "_name": # e.g., customer_name
                                    field_type = "Data" # Could be link, but Data is safer inference
                                elif "status" in field_name:
                                    field_type = "Select" # Usually a select field
                                
                                fields_info.append({
                                    "fieldname": field_name,
                                    "label": field_name.replace('_', ' ').title(),
                                    "fieldtype": field_type,
                                    "reqd": field_name in ['name', doctype_name.lower().replace(" ", "_") + '_name'], # Heuristic for common required fields
                                    "options": None, # Cannot infer options
                                    "description": f"Inferred field: {field_name}. Type: {field_type}. (Metadata not available)"
                                })
                            return json.dumps({"success": True, "doctype": doctype_name, "fields": fields_info, "message": f"Found {len(fields_info)} fields (inferred from sample record). Detailed metadata could not be retrieved."})
                        else:
                            return json.dumps({"success": False, "message": f"Could not retrieve any sample records or field information for {doctype_name}."})
                    except Exception as fallback_error:
                        logger.warning(f"Fallback field inference failed: {fallback_error}. Returning basic fields.")
                        # Final fallback: return basic ERPNext field structure
                        basic_fields = [
                            {"fieldname": "name", "label": "Name", "fieldtype": "Data", "reqd": True, "options": None, "description": "Unique identifier for the document."},
                            {"fieldname": "owner", "label": "Owner", "fieldtype": "Link", "reqd": False, "options": "User", "description": "User who created the document."},
                            {"fieldname": "creation", "label": "Creation Date", "fieldtype": "Datetime", "reqd": False, "options": None, "description": "Timestamp when the document was created."},
                            {"fieldname": "modified", "label": "Modified Date", "fieldtype": "Datetime", "reqd": False, "options": None, "description": "Timestamp of the last modification."}
                        ]
                        return json.dumps({"success": True, "doctype": doctype_name, "fields": basic_fields, "message": "Showing basic fields only due to API limitations or errors in retrieving detailed metadata."})
                
                doctype_meta = meta['docs'][0]
                fields_info = []
                
                for field_meta in doctype_meta.get("fields", []):
                    # Filter out unnecessary fields like Section Break, Column Break, Read Only fields unless specified
                    if field_meta.get("fieldtype") not in ["Section Break", "Column Break", "Read Only"] and not field_meta.get("read_only"):
                        fields_info.append({
                            "fieldname": field_meta.get("fieldname"),
                            "label": field_meta.get("label"),
                            "fieldtype": field_meta.get("fieldtype"),
                            "reqd": field_meta.get("reqd", 0) == 1,
                            "options": field_meta.get("options"),
                            "description": field_meta.get("description", "")
                        })
                
                logger.info(f"Fetched {len(fields_info)} fields for {doctype_name}.")
                return json.dumps({"success": True, "doctype": doctype_name, "fields": fields_info, "message": f"Found {len(fields_info)} fields for {doctype_name}."})
                
            except Exception as call_error:
                logger.error(f"Final fallback error in _get_doctype_fields_tool: {call_error}", exc_info=True)
                return json.dumps({"success": False, "error": f"An unhandled error occurred while trying to retrieve field information for {doctype_name}: {str(call_error)}"})
                
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for get_doctype_fields. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _get_doctype_fields_tool: {str(e)}", exc_info=True)
            return json.dumps({"success": False, "error": f"An unexpected error occurred: {str(e)}"})

    def _create_record_tool(self, input_str: str) -> str:
        """Create a new record in ERPNext. Input should be JSON with 'doctype' (string, required) and 'fields' (dict, required, containing data for the new record). Example: {"doctype": "Lead", "fields": {"lead_name": "New Lead Inc", "email_id": "contact@newlead.com"}}"""
        try:
            logger.info(f"Tool 'create_record' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            fields_data = params.get("fields")

            if not doctype:
                return json.dumps({"success": False, "error": "Doctype is required."})
            if not fields_data or not isinstance(fields_data, dict):
                return json.dumps({"success": False, "error": "Fields data (as a dictionary) is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})


            doc_to_insert = {"doctype": doctype, **fields_data}
            
            logger.info(f"Creating record for Doctype: {doctype} with data: {fields_data}")
            created_doc = self._safe_erpnext_call("insert", doc_to_insert)
            
            if created_doc and created_doc.get('name'):
                logger.info(f"Successfully created record for {doctype}: {created_doc.get('name')}")
                return json.dumps({"success": True, "doctype": doctype, "record": created_doc, "message": f"Record '{created_doc.get('name')}' created successfully."})
            else:
                return json.dumps({"success": False, "message": f"Failed to create record for {doctype}. Response was: {created_doc}", "details": created_doc})
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for create_record. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _create_record_tool: {str(e)}", exc_info=True)
            error_message = str(e)
            if hasattr(e, 'response') and e.response:
                try:
                    error_data = e.response.json()
                    if "_server_messages" in error_data:
                        messages = json.loads(error_data["_server_messages"])
                        error_message = f"Validation Error: {messages}"
                        return json.dumps({"success": False, "error": "Validation Error", "messages": messages, "details": str(e)})
                    elif "exc_info" in error_data: # More general Frappe error
                         error_message = f"ERPNext Server Error: {error_data.get('exc_info', 'No details available')}"
                except:
                    pass # Cannot parse response, use generic error
            return json.dumps({"success": False, "error": error_message, "error_type": type(e).__name__})

    def _update_record_tool(self, input_str: str) -> str:
        """Update an existing record in ERPNext. Input should be JSON with 'doctype' (string, required), 'name' (string, required, ID of the record to update), and 'updates' (dict, required, fields to update). Example: {"doctype": "Customer", "name": "CUST-0001", "updates": {"credit_limit": 5000}}"""
        try:
            logger.info(f"Tool 'update_record' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            record_name = params.get("name")
            updates = params.get("updates")

            if not doctype:
                return json.dumps({"success": False, "error": "Doctype is required."})
            if not record_name:
                return json.dumps({"success": False, "error": "Record name/ID is required."})
            if not updates or not isinstance(updates, dict):
                return json.dumps({"success": False, "error": "Updates data (as a dictionary) is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})


            doc_to_update = {"doctype": doctype, "name": record_name, **updates}
            
            logger.info(f"Updating record Doctype: {doctype}, Name: {record_name} with updates: {updates}")
            updated_doc = self._safe_erpnext_call("update", doc_to_update)
            
            if updated_doc and updated_doc.get('name'):
                logger.info(f"Successfully updated record '{record_name}' in {doctype}.")
                return json.dumps({"success": True, "doctype": doctype, "name": record_name, "record": updated_doc, "message": f"Record '{record_name}' updated successfully."})
            else:
                return json.dumps({"success": False, "message": f"Failed to update record '{record_name}' in {doctype}. Response was: {updated_doc}", "details": updated_doc})

        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for update_record. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _update_record_tool: {str(e)}", exc_info=True)
            error_message = str(e)
            if hasattr(e, 'response') and e.response:
                try:
                    error_data = e.response.json()
                    if "_server_messages" in error_data:
                        messages = json.loads(error_data["_server_messages"])
                        error_message = f"Validation Error: {messages}"
                        return json.dumps({"success": False, "error": "Validation Error", "messages": messages, "details": str(e)})
                    elif "exc_info" in error_data:
                         error_message = f"ERPNext Server Error: {error_data.get('exc_info', 'No details available')}"
                except:
                    pass
            return json.dumps({"success": False, "error": error_message, "error_type": type(e).__name__})

    def _search_records_tool(self, input_str: str) -> str:
        """Search for records across ERPNext using text search. Input should be JSON with 'doctype' (string, required) and 'search_text' (string, required). Optional: 'fields_to_search' (list of strings), 'limit' (int)."""
        try:
            logger.info(f"Tool 'search_records' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            search_text = params.get("search_text")
            fields_to_search = params.get("fields_to_search")
            limit = params.get("limit", 20)

            if not doctype:
                return json.dumps({"success": False, "error": "Doctype is required."})
            if not search_text:
                return json.dumps({"success": False, "error": "Search text is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})


            fields_to_fetch = self._get_default_fields(doctype)
            if 'name' not in fields_to_fetch:  
                fields_to_fetch.append('name')

            logger.info(f"Searching records in Doctype: {doctype} for text '{search_text}'")
            
            if not fields_to_search:
                fields_to_search = self._get_searchable_fields(doctype)

            all_results = []
            seen_names = set()

            # Iterate through suggested searchable fields and collect results
            for field in fields_to_search:
                try:
                    filters = {field: ["like", f"%{search_text}%"]}
                    records = self._safe_erpnext_call(
                        "get_list",
                        doctype,
                        filters=filters,
                        fields=fields_to_fetch,
                        limit_page_length=limit,
                        order_by="modified desc"
                    )
                    if records:
                        for rec in records:
                            if rec.get('name') and rec['name'] not in seen_names:
                                all_results.append(rec)
                                seen_names.add(rec['name'])
                        logger.debug(f"Search in {doctype} for '{search_text}' on field '{field}' found {len(records)} new records.")
                except Exception as field_search_error:
                    logger.debug(f"Search on field '{field}' failed for {doctype}: {field_search_error}")
                    continue
            
            if all_results:
                # Sort results (e.g., by modification date or creation)
                all_results.sort(key=lambda x: x.get('modified', x.get('creation', '')), reverse=True)
                return json.dumps({"success": True, "doctype": doctype, "search_text": search_text, "count": len(all_results), "records": all_results, "message": f"Found {len(all_results)} records matching '{search_text}' across relevant fields."})
            else:
                return json.dumps({"success": True, "doctype": doctype, "search_text": search_text, "count": 0, "records": [], "message": f"No records found matching '{search_text}' for {doctype}."})
                
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for search_records. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _search_records_tool: {str(e)}", exc_info=True)
            return json.dumps({"success": False, "error": f"Error processing request: {str(e)}", "error_type": type(e).__name__})

    def _get_record_count_tool(self, input_str: str) -> str:
        """Get count of records matching criteria. Input should be JSON with 'doctype' (string, required) field, optional 'filters' (dict)."""
        try:
            logger.info(f"Tool 'get_record_count' called with input: {input_str}")
            params = json.loads(input_str)
            doctype = params.get("doctype")
            filters = params.get("filters", {})

            if not doctype:
                return json.dumps({"success": False, "error": "Doctype is required."})
            if doctype not in self.all_doctypes:
                return json.dumps({"success": False, "message": f"Doctype '{doctype}' not found in available doctypes. Please use 'discover_app_doctypes' or 'get_doctypes_by_category' to find valid doctypes."})


            logger.info(f"Getting count for Doctype: {doctype} with Filters: {filters}")
            
            try:
                # Use get_list to count records as FrappeClient doesn't have a direct get_count
                records = self._safe_erpnext_call(
                    "get_list",
                    doctype,
                    filters=filters,
                    fields=["name"],
                    limit_page_length=999999 # Max limit to get all records for count
                )
                
                count = len(records) if records else 0
                logger.info(f"Count for {doctype} is {count}")
                return json.dumps({
                    "success": True,  
                    "doctype": doctype,  
                    "filters": filters,  
                    "count": count,  
                    "message": f"Found {count} records for {doctype}."
                })
                
            except Exception as count_error:
                logger.warning(f"Count with specified filters failed: {count_error}. Trying without filters.")
                
                try:
                    basic_records = self._safe_erpnext_call(
                        "get_list",
                        doctype,
                        fields=["name"],
                        filters={}, # Remove filters
                        limit_page_length=999999
                    )
                    count = len(basic_records) if basic_records else 0
                    return json.dumps({
                        "success": True,  
                        "doctype": doctype,  
                        "filters": "removed_due_to_error",  
                        "count": count,  
                        "message": f"Found {count} total records for {doctype} (filters were ignored due to an error, possibly permission related)."
                    })
                    
                except Exception as basic_error:
                    logger.error(f"Basic count also failed for {doctype}: {basic_error}")
                    return json.dumps({
                        "success": False,  
                        "error": f"Unable to count records for {doctype}: {str(basic_error)}",
                        "error_type": type(basic_error).__name__
                    })
                    
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for get_record_count. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _get_record_count_tool: {str(e)}", exc_info=True)
            return json.dumps({"success": False, "error": f"Error processing request: {str(e)}", "error_type": type(e).__name__})

    def _send_email_tool(self, input_str: str) -> str:
        """Send emails with AI-generated content. Input should be JSON with 'recipient' (string, required), optional 'subject' (string), 'content' (string), 'content_request' (string for AI generation), 'email_type' (general/meeting/follow_up/introduction/product_demo/proposal/thank_you), 'sender_name', 'company_name', 'lead_name', 'product_name', 'context'. If 'content' is provided, 'content_request' is ignored."""
        try:
            logger.info(f"Tool 'send_email' called with input: {input_str}")
            params = json.loads(input_str)
            
            recipient = params.get("recipient")
            if not recipient:
                return json.dumps({"success": False, "error": "Recipient email address is required."})
            
            subject = params.get("subject")
            content = params.get("content")
            content_request = params.get("content_request")
            email_type = params.get("email_type", "general")
            sender_name = params.get("sender_name", "")
            company_name = params.get("company_name", "")
            lead_name = params.get("lead_name", "") # Can be used as recipient name if available
            product_name = params.get("product_name", "")
            context = params.get("context", "")
            
            if not content and content_request:
                logger.info(f"Generating email content for: {content_request}")
                
                content_generation_result = self._generate_email_content(
                    content_request=content_request,
                    recipient=recipient,
                    email_type=email_type,
                    sender_name=sender_name,
                    company_name=company_name,
                    lead_name=lead_name,
                    product_name=product_name,
                    context=context
                )
                
                if not content_generation_result.get("success"):
                    return json.dumps({
                        "success": False,  
                        "error": f"Failed to generate email content: {content_generation_result.get('error')}"
                    })
                
                content = content_generation_result["content"]
                if not subject: # Use generated subject if not provided
                    subject = content_generation_result.get("subject", "Email from ERPNext")
            
            if not subject:
                subject = "Email from ERPNext"
            if not content:
                return json.dumps({"success": False, "error": "Email content is required. Either provide 'content' or 'content_request' for AI generation."})
            
            email_data = {
                "recipients": recipient,
                "subject": subject,
                "content": content,
                "as_html": True,
                "communication_medium": "Email",
                "send_email": 1
            }
            
            logger.info(f"Sending email to {recipient} with subject: {subject}")
            response = self._safe_erpnext_call("post_api", "frappe.core.doctype.communication.email.make", email_data)
            
            logger.info(f"Email sent successfully to {recipient}")
            return json.dumps({
                "success": True,
                "recipient": recipient,
                "subject": subject,
                "content_preview": content[:200] + "..." if len(content) > 200 else content,
                "response": response,
                "message": f"Email sent successfully to {recipient}."
            })
            
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for send_email. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _send_email_tool: {str(e)}", exc_info=True)
            return json.dumps({"success": False, "error": f"Error sending email: {str(e)}", "error_type": type(e).__name__})
    
    def _generate_email_content(self, content_request: str, recipient: str, email_type: str = "general",
                                  sender_name: str = "", company_name: str = "", lead_name: str = "",
                                  product_name: str = "", context: str = "") -> Dict[str, Any]:
        """Generate professional email content using Gemini AI."""
        try:
            email_context = f"""
            Generate a professional email based on the following request:
            
            Request: {content_request}
            Recipient: {recipient}
            Email Type: {email_type}
            """
            
            if sender_name:
                email_context += f"\nSender Name: {sender_name}"
            if company_name:
                email_context += f"\nCompany: {company_name}"
            if lead_name:
                email_context += f"\nRecipient Name: {lead_name}"
            if product_name:
                email_context += f"\nProduct/Service: {product_name}"
            if context:
                email_context += f"\nAdditional Context: {context}"
            
            type_instructions = {
                "meeting": "Focus on scheduling a meeting, be specific about purpose and suggest time flexibility.",
                "follow_up": "Reference previous interaction, maintain professional tone, include clear next steps.",
                "introduction": "Introduce yourself/company professionally, highlight value proposition, keep it concise.",
                "product_demo": "Focus on product benefits, offer demonstration, include compelling value proposition.",
                "proposal": "Present your proposal clearly, highlight benefits, include call-to-action.",
                "thank_you": "Express genuine gratitude, summarize key points, maintain relationship.",
                "general": "Maintain professional tone, be clear and concise, include appropriate call-to-action."
            }
            
            instruction = type_instructions.get(email_type, type_instructions["general"])
            
            prompt = f"""
            {email_context}
            
            Instructions:
            - {instruction}
            - Use professional business language.
            - Keep the email concise but informative.
            - Include a clear subject line.
            - Format as HTML for better presentation.
            - Include proper greetings and sign-off.
            - Make it personalized and engaging.
            
            Please provide the response in the following JSON format:
            ```json
            {{
                "subject": "Email subject line",
                "content": "Full HTML email content with proper formatting and line breaks as HTML <br/> tags"
            }}
            ```
            """
            
            response = self.llm.invoke(prompt)
            content_text = response.content
            
            try:
                # Robustly extract JSON from potential markdown code block
                if "```json" in content_text:
                    content_text = content_text.split("```json", 1)[1].split("```", 1)[0].strip()
                elif "```" in content_text: # Fallback if not specifically json code block
                    content_text = content_text.split("```", 1)[1].strip()
                    
                email_data = json.loads(content_text)
                
                return {
                    "success": True,
                    "subject": email_data.get("subject", "Email from ERPNext"),
                    "content": email_data.get("content", content_text) # Use generated content, fallback to raw if missing
                }
                
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON from email generation, attempting to extract subject and content heuristically.")
                lines = content_text.split('\n')
                subject = "Email from ERPNext"
                content = content_text
                
                for i, line in enumerate(lines):
                    if line.lower().startswith('subject:'):
                        subject = line.split(':', 1)[1].strip()
                        content = '\n'.join(lines[i+1:]).strip()
                        break
                
                return {
                    "success": True,
                    "subject": subject,
                    "content": content
                }
                
        except Exception as e:
            logger.error(f"Error generating email content: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
    
    def _send_crm_outreach_email_tool(self, input_str: str) -> str:
        """Send automated CRM outreach emails to leads with personalization. Input should be JSON with 'lead' (string, required - lead name or email), 'outreach_type' (introduction/follow_up/product_demo/proposal), optional 'company_name', 'product_service', 'custom_message', 'sender_name'. This tool will attempt to find the lead's email in ERPNext if a name is provided."""
        try:
            logger.info(f"Tool 'send_crm_outreach_email' called with input: {input_str}")
            params = json.loads(input_str)
            
            lead_name_or_email = params.get("lead")
            outreach_type = params.get("outreach_type", "introduction")
            
            if not lead_name_or_email:
                return json.dumps({"success": False, "error": "Lead name or email is required."})
            
            company_name = params.get("company_name", "")
            product_service = params.get("product_service", "")
            custom_message = params.get("custom_message", "")
            sender_name = params.get("sender_name", "")
            
            lead_info = None
            recipient_email = lead_name_or_email
            
            if "@" not in lead_name_or_email: # Assume it's a lead name, try to find email
                try:
                    leads = self._safe_erpnext_call(
                        "get_list",
                        "Lead",
                        filters={"lead_name": ["like", f"%{lead_name_or_email}%"]},
                        fields=["name", "lead_name", "email_id", "company_name", "territory", "source"],
                        limit_page_length=1
                    )
                    
                    if leads:
                        lead_info = leads[0]
                        recipient_email = lead_info.get("email_id")
                        if not recipient_email:
                            return json.dumps({"success": False, "error": f"No email address found for lead: {lead_name_or_email}. Please provide an email directly or update the lead's record."})
                        
                        if not company_name: # Use company name from lead if not provided in params
                            company_name = lead_info.get("company_name", "")
                    else:
                        return json.dumps({"success": False, "error": f"Lead '{lead_name_or_email}' not found in ERPNext. Please provide an existing lead's name/email or create a new lead first."})
                        
                except Exception as lead_fetch_error:
                    logger.warning(f"Failed to fetch lead info for '{lead_name_or_email}': {lead_fetch_error}. Attempting to proceed with provided email (if any).")
                    if "@" not in lead_name_or_email: # If it was a name and fetch failed, and not an email, then it's an error.
                        return json.dumps({"success": False, "error": f"Failed to fetch lead information for '{lead_name_or_email}': {str(lead_fetch_error)}. Please ensure the lead exists and has an email, or provide the email directly."})
            
            outreach_templates = {
                "introduction": f"I'd like to introduce our company and explore potential collaboration opportunities with {company_name or 'your company'}.",
                "follow_up": f"Following up on our previous conversation about how we can help {company_name or 'your company'}.",
                "product_demo": f"I'd like to show you how {product_service or 'our solutions'} can benefit {company_name or 'your company'} with a quick demo.",
                "proposal": f"I have a proposal that could help {company_name or 'your company'} achieve better results."
            }
            
            base_message = outreach_templates.get(outreach_type, outreach_templates["introduction"])
            if custom_message:
                base_message = custom_message
            
            content_request = base_message
            if lead_info: # Add more context if lead info was successfully fetched
                content_request += f" The lead is from {lead_info.get('territory', 'unknown territory')} and came through {lead_info.get('source', 'unknown source')}."
            
            email_result = self._generate_email_content(
                content_request=content_request,
                recipient=recipient_email,
                email_type=outreach_type,
                sender_name=sender_name,
                company_name=company_name,
                lead_name=lead_info.get("lead_name") if lead_info else lead_name_or_email,
                product_name=product_service,
                context=f"CRM outreach email for {outreach_type} to {lead_info.get('lead_name', lead_name_or_email) if lead_info else lead_name_or_email}"
            )
            
            if not email_result.get("success"):
                return json.dumps({"success": False, "error": f"Failed to generate outreach content: {email_result.get('error')}"})
            
            email_data = {
                "recipients": recipient_email,
                "subject": email_result["subject"],
                "content": email_result["content"],
                "as_html": True,
                "communication_medium": "Email",
                "send_email": 1
            }
            
            logger.info(f"Sending CRM outreach email to {recipient_email}")
            response = self._safe_erpnext_call("post_api", "frappe.core.doctype.communication.email.make", email_data)
            
            activity_logged = False
            if lead_info:
                try:
                    note_content = f"Automated CRM outreach email sent: {email_result['subject']}. Type: {outreach_type}."
                    self._safe_erpnext_call("insert", {
                        "doctype": "Comment",
                        "comment_type": "Comment",
                        "reference_doctype": "Lead",
                        "reference_name": lead_info["name"],
                        "content": note_content
                    })
                    activity_logged = True
                except Exception as note_error:
                    logger.warning(f"Failed to log activity in lead '{lead_info.get('name')}': {note_error}")
            
            return json.dumps({
                "success": True,
                "recipient": recipient_email,
                "lead_name": lead_info.get("lead_name") if lead_info else lead_name_or_email,
                "outreach_type": outreach_type,
                "subject": email_result["subject"],
                "content_preview": email_result["content"][:200] + "..." if len(email_result["content"]) > 200 else email_result["content"],
                "activity_logged": activity_logged,
                "response": response,
                "message": f"CRM outreach email sent successfully to {recipient_email} and activity logged: {activity_logged}."
            })
            
        except json.JSONDecodeError:
            return json.dumps({"success": False, "message": "Invalid JSON input for send_crm_outreach_email. Ensure input is a valid JSON string."})
        except Exception as e:
            logger.error(f"Error in _send_crm_outreach_email_tool: {str(e)}", exc_info=True)
            return json.dumps({"success": False, "error": f"Error sending CRM outreach email: {str(e)}", "error_type": type(e).__name__})

    def _detect_how_to_question(self, user_input: str) -> bool:
        """Detect if the user is asking a how-to question."""
        # Explicit how-to question patterns
        explicit_how_to_patterns = [
            r'how\s+to\s+',
            r'how\s+do\s+i\s+',
            r'how\s+can\s+i\s+',
            r'steps\s+to\s+',
            r'guide\s+to\s+',
            r'tutorial\s+',
            r'instructions\s+to\s+',
            r'show\s+me\s+how\s+to\s+',
            r'teach\s+me\s+how\s+to\s+',
            r'explain\s+how\s+to\s+'
        ]
        
        user_input_lower = user_input.lower()
        
        # Check for explicit how-to patterns first
        for pattern in explicit_how_to_patterns:
            if re.search(pattern, user_input_lower):
                return True
        
        # Check for action words that could be how-to questions, but exclude direct creation commands
        # Only consider it a how-to question if it doesn't contain specific identifiers (names, emails, etc.)
        action_patterns = [
            r'\bsetup\s+',
            r'\bconfigure\s+',
            r'\bset\s+up\s+'
        ]
        
        # Patterns that indicate direct creation/action (not how-to)
        direct_action_indicators = [
            r'\bcreate\s+(?:a\s+)?(?:new\s+)?(?:crm\s+)?lead\s+(?:named|called|for)\s+[a-zA-Z]',  # "create a lead named John"
            r'\bcreate\s+(?:a\s+)?(?:new\s+)?lead\s+[a-zA-Z]',  # "create lead John"
            r'\badd\s+(?:a\s+)?(?:new\s+)?lead\s+(?:named|called|for)\s+[a-zA-Z]',  # "add a lead named John"
            r'\bmake\s+(?:a\s+)?(?:new\s+)?lead\s+(?:named|called|for)\s+[a-zA-Z]',  # "make a lead named John"
            r'\bcreate\s+.*\b(?:type|email|phone|company)\s*:',  # "create lead type: Individual"
            r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',  # Contains email address
            r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b'  # Contains phone number
        ]
        
        # If it contains direct action indicators, it's not a how-to question
        for indicator in direct_action_indicators:
            if re.search(indicator, user_input_lower):
                return False
        
        # Check for action patterns only if no direct action indicators
        for pattern in action_patterns:
            if re.search(pattern, user_input_lower):
                return True
        
        return False
    
    def _generate_clickable_links(self, doctype: str, action: str = "create") -> Dict[str, str]:
        """Generate clickable links for ERPNext pages based on doctype and action."""
        base_url = self.site_base_url.rstrip('/')
        
        # Map doctypes to their modules/apps for better URL generation
        doctype_info = self.all_doctypes.get(doctype, {})
        module = doctype_info.get('module', '').lower().replace(' ', '-')
        app = doctype_info.get('app', 'erpnext')
        
        # Generate URLs based on common ERPNext patterns
        links = {}
        
        if action == "create":
            links["new_record"] = f"{base_url}/app/{doctype.lower().replace(' ', '-')}/new"
            links["list_view"] = f"{base_url}/app/{doctype.lower().replace(' ', '-')}"
        elif action == "view":
            links["list_view"] = f"{base_url}/app/{doctype.lower().replace(' ', '-')}"
        elif action == "update":
            links["list_view"] = f"{base_url}/app/{doctype.lower().replace(' ', '-')}"
        
        # Add module-specific links
        if module:
            if 'selling' in module or 'sales' in module:
                links["selling_module"] = f"{base_url}/app/selling"
            elif 'buying' in module or 'purchase' in module:
                links["buying_module"] = f"{base_url}/app/buying"
            elif 'stock' in module or 'inventory' in module:
                links["stock_module"] = f"{base_url}/app/stock"
            elif 'accounts' in module or 'accounting' in module:
                links["accounts_module"] = f"{base_url}/app/accounts"
            elif 'hr' in module or 'human' in module:
                links["hr_module"] = f"{base_url}/app/hr"
            elif 'manufacturing' in module:
                links["manufacturing_module"] = f"{base_url}/app/manufacturing"
            elif 'crm' in module:
                links["crm_module"] = f"{base_url}/app/crm"
        
        return links
    
    def _generate_onboarding_response(self, user_input: str, doctype: str = None) -> str:
        """Generate dynamic onboarding response using LLM with clickable links and personalized instructions."""
        try:
            # Extract doctype from user input if not provided
            if not doctype:
                for dt_name in self.all_doctypes.keys():
                    if dt_name.lower() in user_input.lower():
                        doctype = dt_name
                        break
            
            if not doctype:
                # Try to infer from common terms
                user_lower = user_input.lower()
                if any(term in user_lower for term in ['customer', 'client']):
                    doctype = 'Customer'
                elif any(term in user_lower for term in ['sales order', 'order']):
                    doctype = 'Sales Order'
                elif any(term in user_lower for term in ['item', 'product']):
                    doctype = 'Item'
                elif any(term in user_lower for term in ['lead', 'prospect']):
                    doctype = 'Lead'
                elif any(term in user_lower for term in ['quotation', 'quote']):
                    doctype = 'Quotation'
                elif any(term in user_lower for term in ['invoice']):
                    doctype = 'Sales Invoice'
            
            # Determine action type
            action = "create"
            if any(term in user_input.lower() for term in ['view', 'see', 'list', 'show']):
                action = "view"
            elif any(term in user_input.lower() for term in ['update', 'edit', 'modify']):
                action = "update"
            
            # Generate dynamic response using LLM
            if doctype and doctype in self.all_doctypes:
                # Get doctype fields for context
                try:
                    fields_info = self._get_doctype_fields_tool(json.dumps({"doctype": doctype}))
                    fields_data = json.loads(fields_info)
                except:
                    fields_data = {"success": False}
                
                # Generate clickable links
                links = self._generate_clickable_links(doctype, action)
                
                # Create LLM prompt for personalized response
                prompt = f"""
Generate a concise, helpful onboarding response for a user asking: "{user_input}"

Context:
- User wants to {action} a {doctype} in ERPNext
- Available quick access links: {list(links.keys()) if links else 'None'}
- Doctype fields available: {len(fields_data.get('fields', [])) if fields_data.get('success') else 'Unknown'} fields

Requirements:
1. Start with a clear heading using <h2> tags
2. Provide specific, actionable steps (3-5 steps max)
3. Be concise and user-friendly
4. Focus on the specific action ({action}) for {doctype}
5. Do NOT include tips, best practices, or additional advice sections
6. Do NOT include clickable links or buttons (they will be added separately)
7. Use <h3> for sub-headings if needed
8. Keep the response under 150 words
9. Only include essential steps needed to complete the task
10. Do NOT include any code blocks, backticks, or the word 'html' anywhere
11. Use minimal spacing - single line breaks only, no excessive whitespace
12. Output clean, properly formatted content with proper tag structure

Generate a helpful, personalized response:"""
                
                try:
                    llm_response = self.llm.invoke(prompt)
                    dynamic_content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
                except Exception as e:
                    logger.warning(f"LLM generation failed, using fallback: {e}")
                    dynamic_content = f"<h2>How to {action.title()} a {doctype}</h2>\n\n<h3>Quick Steps</h3>\n1. Navigate to {doctype} module\n2. {'Click New to create' if action == 'create' else 'Find and select the record'}\n3. {'Fill required fields and save' if action == 'create' else 'Make your changes and save'}"
                
                # Add clickable links as buttons
                if links:
                    dynamic_content += "\n\n<h3>Quick Access</h3>\n"
                    for link_name, url in links.items():
                        formatted_name = link_name.replace('_', ' ').title()
                        dynamic_content += f"<button onclick=\"window.open('{url}', '_blank')\">{formatted_name}</button>\n"
                
                return dynamic_content
            
            else:
                # Generate dynamic generic response using LLM
                base_url = self.site_base_url.rstrip('/')
                
                prompt = f"""
Generate a helpful onboarding response for a user asking: "{user_input}"

Context:
- User is asking about ERPNext in general (no specific doctype identified)
- Available modules: Sales & CRM, Selling, Buying, Stock, Accounts, HR
- User needs general guidance on ERPNext navigation

Requirements:
1. Start with a clear heading using <h2> tags
2. Provide general navigation guidance
3. Be encouraging and helpful
4. Suggest asking about specific documents for detailed help
5. Keep it concise (under 150 words)
6. Do NOT include clickable links or buttons (they will be added separately)
7. Use <h3> for sub-headings if needed
8. Do NOT include any code blocks, backticks, or the word 'html' anywhere
9. Use minimal spacing - single line breaks only, no excessive whitespace
10. Output clean, properly formatted content with proper tag structure

Generate a helpful response:"""
                
                try:
                    llm_response = self.llm.invoke(prompt)
                    dynamic_content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
                except Exception as e:
                    logger.warning(f"LLM generation failed, using fallback: {e}")
                    dynamic_content = "<h2>ERPNext Navigation</h2>\n\nI'm here to help you navigate ERPNext! You can explore different modules or ask me about specific documents like Customers, Sales Orders, or Items for detailed guidance."
                
                # Add module buttons
                dynamic_content += "\n\n<h3>Main Modules</h3>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/crm', '_blank')\">Sales & CRM</button>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/selling', '_blank')\">Selling</button>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/buying', '_blank')\">Buying</button>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/stock', '_blank')\">Stock</button>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/accounts', '_blank')\">Accounts</button>\n"
                dynamic_content += f"<button onclick=\"window.open('{base_url}/app/hr', '_blank')\">HR</button>\n"
                
                return dynamic_content
                
        except Exception as e:
            logger.error(f"Error generating onboarding response: {e}", exc_info=True)
            return f"I'd be happy to help you with ERPNext! However, I encountered an error generating the detailed guide. Please try asking about a specific document type, like 'How to create a Customer' or 'How to make a Sales Order'."

    def _create_tools(self) -> List[Tool]:
        """Create LangChain tools for ERPNext operations with dynamic doctype support."""
        tools = [
            Tool(
                name="fetch_records",
                description="Fetch records from any ERPNext doctype. Input: JSON with 'doctype' (string, required), optional 'filters' (dict), 'fields' (list of strings), 'limit' (int), and 'order_by' (string, e.g., 'creation desc'). Use this for general queries like 'show me all customers' or 'list items with quantity less than 10'.",
                func=self._fetch_records_tool
            ),
            Tool(
                name="read_record",
                description="Fetch a single record by its name (ID) from any ERPNext doctype. Input: JSON with 'doctype' (string, required) and 'name' (string, required, the ID of the record). Use this when you need specific details about a known record, e.g., 'show me details of customer CUST-00001'.",
                func=self._read_record_tool
            ),
            Tool(
                name="get_doctype_fields",
                description="Get all available fields for a specific doctype including which fields are required, their types (e.g., Data, Int, Currency, Date, Select, Link), and options (for Link or Select). Input: JSON with 'doctype' field. Use this before creating or updating records to understand what information is needed, or if the user asks 'What fields does a Sales Order have?'.",
                func=self._get_doctype_fields_tool
            ),
            Tool(
                name="create_record",
                description="Create a new record in ERPNext. Input: JSON with 'doctype' (string, required) and 'fields' (dict, required, containing data for the new record). BEFORE using this, use 'get_doctype_fields' to understand required fields if you are unsure. If required fields are missing, ask the user for ALL of them. Example: {\"doctype\": \"Lead\", \"fields\": {\"lead_name\": \"New Lead Inc\", \"email_id\": \"contact@newlead.com\"}}",
                func=self._create_record_tool
            ),
            Tool(
                name="update_record",
                description="Update an existing record in ERPNext. Input: JSON with 'doctype' (string, required), 'name' (string, required, ID of the record to update), and 'updates' (dict, required, fields to update). BEFORE using this, use 'get_doctype_fields' to verify field names and types if you are unsure. Example: {\"doctype\": \"Customer\", \"name\": \"CUST-0001\", \"updates\": {\"credit_limit\": 5000}}",
                func=self._update_record_tool
            ),
            Tool(
                name="search_records",
                description="Search for records across ERPNext using text search on relevant fields. Input: JSON with 'doctype' (string, required) and 'search_text' (string, required). Optional: 'fields_to_search' (list of strings, specify if you want to search only specific fields), 'limit' (int). Use this for fuzzy searches, e.g., 'find customers named Acme' or 'search for projects related to website redesign'.",
                func=self._search_records_tool
            ),
            Tool(
                name="get_record_count",
                description="Get count of records matching criteria. Input: JSON with 'doctype' (string, required) field, optional 'filters' (dict). Use this for 'how many' questions, e.g., 'How many open tasks are there?'",
                func=self._get_record_count_tool
            ),
            Tool(
                name="discover_app_doctypes",
                description="Discover all available doctypes from installed apps. Input should be app name (string) or 'all' (string) for all apps. Use this if the user asks about specific apps or their doctypes, e.g., 'What doctypes are in the ERPNext app?' or 'Show me all doctypes.'",
                func=self._discover_app_doctypes_tool
            ),
            Tool(
                name="get_installed_apps",
                description="Get list of all installed Frappe apps and their information, including names, titles, and descriptions. No input string required, but can accept an empty string. Use this if the user asks 'What apps are installed?' or 'Tell me about the available applications.'",
                func=self._get_installed_apps_tool
            ),
            Tool(
                name="get_doctypes_by_category",
                description="Get doctypes grouped by category (e.g., HR & Payroll, Sales & CRM, Manufacturing). Input should be category name (string) or 'all' (string). Use this to help users explore doctypes by business area, e.g., 'Show me doctypes related to HR' or 'List all doctype categories.'",
                func=self._get_doctypes_by_category_tool
            ),
            Tool(
                name="send_email",
                description="Send emails with AI-generated content. Input: JSON with 'recipient' (string, required), optional 'subject' (string), 'content' (string), 'content_request' (string for AI generation), 'email_type' (general/meeting/follow_up/introduction/product_demo/proposal/thank_you), 'sender_name', 'company_name', 'lead_name', 'product_name', 'context'. If 'content' is provided, 'content_request' is ignored.",
                func=self._send_email_tool
            ),
            Tool(
                name="send_crm_outreach_email",
                description="Send automated CRM outreach emails to leads with personalization. Input: JSON with 'lead' (string, required - lead name or email), 'outreach_type' (e.g., introduction, follow_up, product_demo, proposal), optional 'company_name', 'product_service', 'custom_message', 'sender_name'. This tool will attempt to find the lead's email in ERPNext if a name is provided. Use this for specific CRM outreach actions, e.g., 'Send an introduction email to John Smith about our new CRM solution'.",
                func=self._send_crm_outreach_email_tool
            )
        ]
        
        # Add CRM tools if available
        if self.lead_creation_agent:
            tools.extend([
                Tool(
                    name="create_leads",
                    description="Create leads from Google Maps business search. Input: JSON with 'business_type' (string, required), 'location' (string, required), 'count' (int, optional, default 10). Example: {'business_type': 'restaurants', 'location': 'New York', 'count': 20}. Use this when users want to generate leads from specific business types in certain locations.",
                    func=self._create_leads_tool
                ),
                Tool(
                    name="search_businesses",
                    description="Search for businesses on Google Maps without creating leads. Input: JSON with 'business_type' (string, required), 'location' (string, required), 'count' (int, optional, default 10). Returns business information for preview before lead creation.",
                    func=self._search_businesses_tool
                )
            ])
        
        if self.lead_outreach_agent:
            tools.extend([
                Tool(
                    name="run_outreach_campaign",
                    description="Run automated outreach campaign to leads. Input: JSON with optional 'lead_count' (int), 'industry' (string), 'min_employees' (int), 'status' (string). Example: {'lead_count': 50, 'industry': 'Technology', 'status': 'Lead'}. Use this for bulk outreach campaigns.",
                    func=self._run_outreach_campaign_tool
                ),
                Tool(
                    name="get_campaign_stats",
                    description="Get statistics about outreach campaigns. Input: JSON with optional 'days' (int, default 30) to specify time period. Returns campaign performance metrics.",
                    func=self._get_campaign_stats_tool
                ),
                Tool(
                    name="get_leads_for_outreach",
                    description="Get leads suitable for outreach campaigns. Input: JSON with optional 'industry' (string), 'min_employees' (int), 'status' (string), 'limit' (int, default 50). Returns filtered leads ready for outreach.",
                    func=self._get_leads_for_outreach_tool
                )
            ])
        
        return tools
    
    def _discover_app_doctypes_tool(self, app_input: str) -> str:
        """Tool to discover doctypes for specific apps or all apps."""
        try:
            app_name_query = app_input.strip().lower()
            
            if app_name_query == 'all':
                result = {}
                for app_name, info in self.installed_apps.items():
                    result[app_name] = {
                        'title': info.get('title', app_name),
                        'doctypes': info.get('doctypes', []),
                        'count': len(info.get('doctypes', []))
                    }
                
                return json.dumps({
                    "success": True,
                    "message": f"Found {len(self.installed_apps)} installed apps with their doctypes.",
                    "apps": result
                })
                
            else:
                matching_app_key = None
                for app_key in self.installed_apps:
                    if app_name_query == app_key.lower() or app_name_query in app_key.lower():
                        matching_app_key = app_key
                        break
                
                if matching_app_key:
                    app_info = self.installed_apps[matching_app_key]
                    return json.dumps({
                        "success": True,
                        "app": matching_app_key,
                        'title': app_info.get('title', matching_app_key),
                        "description": app_info.get('description', ''),
                        "doctypes": app_info.get('doctypes', []),
                        "count": len(app_info.get('doctypes', []))
                    })
                else:
                    available_apps = list(self.installed_apps.keys())
                    return json.dumps({
                        "success": False,
                        "message": f"App '{app_input}' not found. Available apps: {', '.join(available_apps)}. Please refine your query.",
                        "available_apps": available_apps
                    })
        
        except Exception as e:
            logger.error(f"Error in _discover_app_doctypes_tool: {e}", exc_info=True)
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    def _get_installed_apps_tool(self, input_text: str = "") -> str:
        """Tool to get information about installed apps."""
        try:
            apps_info = []
            for app_name, app_data in self.installed_apps.items():
                apps_info.append({
                    'name': app_name,
                    'title': app_data.get('title', app_name),
                    'description': app_data.get('description', ''),
                    'version': app_data.get('version', 'Unknown'),
                    'doctype_count': app_data.get('doctype_count', 0),
                    'sample_doctypes': app_data.get('doctypes', [])[:5] # Show first 5 doctypes
                })
            
            return json.dumps({
                "success": True,
                "total_apps": len(apps_info),
                "apps": apps_info,
                "message": f"Successfully retrieved information for {len(apps_info)} installed apps."
            })
        
        except Exception as e:
            logger.error(f"Error in _get_installed_apps_tool: {e}", exc_info=True)
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    def _get_doctypes_by_category_tool(self, category_input: str) -> str:
        """Tool to get doctypes grouped by category."""
        try:
            category_query = category_input.strip().lower()
            
            category_summary_dict = {}
            for doctype_name, doctype_info in self.all_doctypes.items():
                category = doctype_info.get('category', 'Other') # Corrected variable name 'cat' to 'category' here
                if category not in category_summary_dict: # Corrected variable name 'cat' to 'category' here
                    category_summary_dict[category] = []
                category_summary_dict[category].append({
                    'name': doctype_name,
                    'module': doctype_info.get('module', 'Unknown'),
                    'app': doctype_info.get('app', 'unknown_app'),
                    'description': doctype_info.get('description', "")
                })
            
            if category_query == 'all':
                return json.dumps({
                    "success": True,
                    "categories": category_summary_dict,
                    "total_categories": len(category_summary_dict),
                    "message": f"Found {len(category_summary_dict)} doctype categories."
                })
            else:
                matching_category_key = None
                for cat_key in category_summary_dict:
                    if category_query == cat_key.lower() or category_query in cat_key.lower():
                        matching_category_key = cat_key
                        break
                
                if matching_category_key:
                    return json.dumps({
                        "success": True,
                        "category": matching_category_key,
                        "doctypes": category_summary_dict[matching_category_key],
                        "count": len(category_summary_dict[matching_category_key]),
                        "message": f"Found {len(category_summary_dict[matching_category_key])} doctypes in category '{matching_category_key}'."
                    })
                else:
                    available_categories = list(category_summary_dict.keys())
                    return json.dumps({
                        "success": False,
                        "message": f"Category '{category_input}' not found. Available categories: {', '.join(available_categories)}",
                        "available_categories": available_categories
                    })
        
        except Exception as e:
            logger.error(f"Error in _get_doctypes_by_category_tool: {e}", exc_info=True)
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    def _get_default_fields(self, doctype: str) -> List[str]:
        """Get safe default fields that typically don't cause permission errors."""
        base_fields = ["name", "creation", "modified", "owner"]
        
        if doctype not in self.all_doctypes:
            return base_fields
            
        doctype_info = self.all_doctypes[doctype]
        category = doctype_info.get('category', 'Other')
        
        safe_category_fields = {
            'Sales & CRM': ["customer_name", "status", "territory", "contact_person", "email_id"],
            'Purchase & Procurement': ["supplier_name", "status", "company", "item_name"],  
            'HR & Payroll': ["employee_name", "department", "status", "designation", "user_id"],
            'Stock & Inventory': ["item_code", "item_name", "item_group", "warehouse", "stock_qty"],
            'Manufacturing': ["production_order", "status", "finished_item", "qty", "item_code"],
            'Projects': ["project_name", "status", "priority", "project_type", "percent_complete"],
            'Accounting & Finance': ["posting_date", "company", "total_amount", "grand_total", "currency"],
            'Support & Helpdesk': ["subject", "status", "priority", "customer", "issue_type"],
            'Analytics & Reports': ["is_standard", "module", "report_type", "report_name"],
            'System & Configuration': ["module", "is_single", "description", "name"],
            'Content & Website': ["title", "route", "published", "page_name"],
            'File Management': ["file_name", "file_url", "is_private", "folder"],
            'Communication & Events': ["subject", "sender", "recipients", "communication_date", "status"],
            'Knowledge Management': ["title", "category", "route", "published"],
            'Automation & Workflow': ["document_type", "workflow_state", "status", "workflow_name"]
        }
        
        specific_fields = safe_category_fields.get(category, [])
        all_fields = base_fields + specific_fields
        
        # Ensure uniqueness and return
        return list(dict.fromkeys(all_fields))

    def _get_searchable_fields(self, doctype: str) -> List[str]:
        """Get a list of commonly searchable fields for a given doctype."""
        searchable_fields = ['name', 'title', 'subject', 'description', 'notes', 'content', 'email_id', 'phone_no', 'mobile_no']

        if doctype in self.all_doctypes:
            category = self.all_doctypes[doctype].get('category', 'Other')
            if category == 'Sales & CRM':
                searchable_fields.extend(['customer_name', 'contact_person', 'company_name', 'lead_name'])
            elif category == 'Purchase & Procurement':
                searchable_fields.extend(['supplier_name', 'item_name'])
            elif category == 'HR & Payroll':
                searchable_fields.extend(['employee_name', 'user_id', 'department', 'designation'])
            elif category == 'Stock & Inventory':
                searchable_fields.extend(['item_code', 'item_name', 'warehouse'])
            elif category == 'Projects':
                searchable_fields.extend(['project_name', 'task_name'])
            elif category == 'Accounting & Finance':
                searchable_fields.extend(['account_name', 'party_name'])

        # Get actual fields for the doctype to filter for valid fields
        all_doctype_fields_json = self._get_doctype_fields_tool(json.dumps({"doctype": doctype}))
        try:
            fields_response = json.loads(all_doctype_fields_json)
            if fields_response.get("success") and fields_response.get("fields"):
                available_fieldnames = {f['fieldname'] for f in fields_response['fields']}
                # Filter for Data, Text, Small Text, Long Text, Read Only (if it can be searched)
                # Link fields could also be searched if their primary field matches.
                searchable_types = ["Data", "Text", "Small Text", "Long Text", "Code", "Link", "Select"]
                filtered_fields = [f['fieldname'] for f in fields_response['fields'] if f.get('fieldtype') in searchable_types]
                
                # Combine heuristic fields with actual available fields that are searchable
                return list(dict.fromkeys([f for f in searchable_fields if f in available_fieldnames] + filtered_fields))
        except Exception as e:
            logger.warning(f"Could not get detailed doctype fields for {doctype} to refine search fields: {e}. Using heuristic list.")
            
        return list(dict.fromkeys(searchable_fields))

    def _create_agent(self) -> AgentExecutor:
        """Create the LangChain agent with enhanced prompt for dynamic app support."""
        
        app_summary = []
        for app_name, app_info in self.installed_apps.items():
            doctype_list_str = ", ".join(app_info.get('doctypes', [])[:5]) + ("..." if len(app_info.get('doctypes', [])) > 5 else "")
            app_summary.append(f"  - {app_info.get('title', app_name)} ({app_name}): {app_info.get('doctype_count', 0)} doctypes. Sample: [{doctype_list_str}]")
        
        category_summary_dict = {} 
        for doctype_name, doctype_info in self.all_doctypes.items():
            category = doctype_info.get('category', 'Other') # Corrected variable name 'cat' to 'category' here
            if category not in category_summary_dict: # Corrected variable name 'cat' to 'category' here
                category_summary_dict[category] = [] 
            category_summary_dict[category].append(doctype_name)
        
        categories_info_str = "\n".join([f"  - {cat}: {len(doctypes)} doctypes. Sample: [{', '.join(doctypes[:5]) + ('...' if len(doctypes) > 5 else '')}]" for cat, doctypes in category_summary_dict.items()])
        
        tools_description_str = "\n".join([f"{tool.name}: {tool.description}" for tool in self.tools])
        tool_names_list = [tool.name for tool in self.tools]
        
        # Add CRM capabilities info
        crm_capabilities = ""
        if self.lead_creation_agent or self.lead_outreach_agent:
            crm_capabilities = f"""

**🎯 Enhanced CRM Capabilities:**
You have advanced CRM features available:
{"✅ Lead Generation: Create leads from Google Maps business searches" if self.lead_creation_agent else "❌ Lead Generation: Not available (requires Google Maps API key)"}
{"✅ Business Search: Find businesses on Google Maps for prospecting" if self.gmaps_extractor else "❌ Business Search: Not available (requires Google Maps API key)"}
{"✅ Outreach Campaigns: Run automated email campaigns to leads" if self.lead_outreach_agent else "❌ Outreach Campaigns: Not available"}
{"✅ Campaign Analytics: Track email campaign performance" if self.lead_outreach_agent else "❌ Campaign Analytics: Not available"}

**CRM Use Cases:**
- "Create 20 restaurant leads in New York" → Uses Google Maps to find and create leads
- "Search for tech companies in San Francisco" → Finds businesses without creating leads
- "Run outreach campaign to technology leads" → Sends personalized emails to filtered leads
- "Show me campaign stats for the last 30 days" → Displays email and lead metrics"""
        
        prompt_template_str = f"""You are Aida AI, a friendly, proactive, and highly knowledgeable assistant for ERPNext and all installed Frappe applications with enhanced CRM capabilities. Your goal is to efficiently help users manage their ERP data, generate leads, run outreach campaigns, and automate business processes.

**Your Core Personality:**
- **Helpful & Proactive:** Anticipate user needs and offer solutions.
- **Clear & Concise:** Communicate directly and avoid jargon.
- **Patient & Thorough:** Ask clarifying questions if needed, ensuring all necessary information is gathered before acting, especially for creating/updating records.
- **Knowledgeable:** Demonstrate deep understanding of ERPNext and its structure by effectively using your tools.
- **CRM-Focused:** Proactively suggest lead generation and outreach opportunities when relevant.{crm_capabilities}

**🚀 Dynamic Capabilities & Data Access:**
You have direct API access to ALL installed Frappe applications on this ERPNext instance. This means you can interact with any doctype and module present.

**📊 ERPNext System Overview:**
Here's a summary of the applications and doctype categories you can interact with:

**Installed Apps:**
{{apps_info_str}}

**Doctype Categories:**
{{categories_info_str}}

📋 **Total Discovered Doctypes:** {len(self.all_doctypes)}

**💡 General Guidelines for Interaction:**
1.  **Understand User Intent:** Carefully analyze the user's query to determine the required doctype, action (read, create, update, delete, search, count, email), and necessary parameters.
2.  **Tool Selection & Input:** Choose the most appropriate tool for the task. **ALWAYS** ensure your `Action Input` is a valid JSON string when the tool description specifies it.
3.  **Contextual Awareness:**
    * **Conversational History:** Leverage `chat_history` to understand follow-up questions (e.g., referring to a previously mentioned record with "it" or "that") and maintain continuity.
    * **Current Date:** For date-related queries, consider the current date: {{current_date}}. You can interpret relative dates like "today", "yesterday", "last week", "next month".
4.  **CRUD Operations (Create/Update):**
    * **Pre-check Fields:** If you intend to `create_record` or `update_record` and you are **unsure about the required fields or their types**, your FIRST step MUST be to use the `get_doctype_fields` tool for that specific doctype. This prevents validation errors.
    * **Missing Information:** If, after inspecting the doctype fields, you find that you still lack required information from the user, you MUST ask for **ALL** of the missing required fields clearly and concisely in a single `Final Answer` response. For example: "To create a new Sales Order, I need the customer's name, item code, and quantity."
5.  **Discovery Queries:** If the user asks general questions about available apps or doctypes, use `get_installed_apps`, `discover_app_doctypes`, or `get_doctypes_by_category` to provide helpful information.
6.  **Error Handling:** If a tool call fails, parse the error message and provide a user-friendly explanation. Suggest rephrasing or provide potential solutions.

**🚨 CRITICAL RULES:**
-   **Direct Answer:** Once you have the complete answer or have successfully performed the requested action, provide the `Final Answer` and **STOP**. Do NOT continue with further thoughts or actions.
-   **No Redundant Actions:** Do NOT repeat the same tool call if it's not yielding new, relevant results or if you are stuck in a loop.
-   **JSON Format:** Ensure ALL tool inputs are correctly formatted JSON strings if the tool's description requires it. Invalid JSON will cause the tool to fail.

You have access to the following tools:
{{tools}}

Use this EXACT format:

Question: the input question you must answer
Thought: you should always think about what to do, which tool to use, and what the input should be.
Action: the action to take, should be one of [{{tool_names}}]
Action Input: the input to the action (MUST BE A VALID JSON STRING IF THE TOOL DESCRIPTION SAYS SO, otherwise a plain string).
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer or I need more information from the user.
Final Answer: the final answer to the original input question, or a question to the user for more information, listing ALL necessary information.

REMEMBER: Once you provide a Final Answer, you are DONE. Do not continue with more thoughts or actions.

Begin!

{{chat_history}}
Question: {{input}}
Thought:{{agent_scratchpad}}"""
        
        prompt = PromptTemplate(
            template=prompt_template_str,
            input_variables=["input", "agent_scratchpad", "current_date", "chat_history"],
            partial_variables={
                "tools": tools_description_str,
                "tool_names": ", ".join(tool_names_list),
                "apps_info_str": "\n".join(app_summary), 
                "categories_info_str": categories_info_str 
            }
        )
        
        agent = create_react_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=prompt
        )
        
        return AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True, # Keep verbose for debugging agent's thought process
            handle_parsing_errors=True,
            max_iterations=10, # Increased max iterations
            return_intermediate_steps=True,
            early_stopping_method="force"
        )
            
    def chat(self, user_input: str) -> str:
        """
        Main chat interface for Aida AI, using LangChain's conversational capabilities.
        
        Args:
            user_input: User's natural language query
            
        Returns:
            Aida's response
        """
        try:
            # Check if this is a how-to question for onboarding
            if self._detect_how_to_question(user_input):
                onboarding_response = self._generate_onboarding_response(user_input)
                if onboarding_response:
                    # Store the onboarding response in conversation history
                    if self.memory_manager:
                        self.memory_manager.store_conversation(
                            self.session_id, 
                            user_input,
                            onboarding_response
                        )
                    return onboarding_response
            
            current_date_str = datetime.now().strftime("%Y-%m-%d")
            
            # Add current user message to history. The RunnableWithMessageHistory will manage it.
            # self.conversation_history.add_user_message(user_input) # This is handled by RunnableWithMessageHistory

            # Prepare chat history for the agent. RunnableWithMessageHistory injects it.
            # We don't need to manually prepare `history_for_agent` if using `RunnableWithMessageHistory`.
            
            chain_with_history = RunnableWithMessageHistory(
                self.agent_executor,
                lambda session_id: self.conversation_history, # The message history is managed by this instance
                input_messages_key="input",
                history_messages_key="chat_history" # Key for history in the prompt
            )

            response_dict = chain_with_history.invoke(
                {"input": user_input, "current_date": current_date_str},
                config={"configurable": {"session_id": self.session_id}}
            )
            ai_response_str = response_dict.get("output", "I'm sorry, I couldn't generate a response.")

            query_result_for_storage = None
            doctype_for_storage = None

            # Extract last relevant observation for storage
            if response_dict.get("intermediate_steps"):
                # Iterate in reverse to get the most recent useful observation
                for step_action, step_observation_str in reversed(response_dict["intermediate_steps"]):
                    try:
                        observation_data = json.loads(step_observation_str)
                        if isinstance(observation_data, dict) and observation_data.get("success"):
                            if "records" in observation_data or "count" in observation_data or "record" in observation_data:
                                query_result_for_storage = observation_data
                                doctype_for_storage = observation_data.get("doctype", doctype_for_storage)
                                break # Found the most recent relevant result, exit loop
                        if observation_data.get("doctype"): # Even if not a full result, store doctype if known
                            doctype_for_storage = observation_data.get("doctype")
                    except json.JSONDecodeError:
                        logger.debug(f"Intermediate step observation was not JSON: {step_observation_str[:100]}...")
                    except Exception as e_obs:
                        logger.warning(f"Error processing observation for context: {e_obs}")
            
            # Store the full conversation turn (user input, AI response, and observed result)
            if self.memory_manager:
                self.memory_manager.store_conversation(
                    self.session_id, 
                    user_input,
                    ai_response_str, 
                    query_result_for_storage, 
                    doctype_for_storage
                )
            
            return ai_response_str
                
        except Exception as e:
            logger.error(f"Error in chat method: {e}", exc_info=True)
            error_message = f"I apologize, but I encountered an unexpected error: {str(e)}. Please try rephrasing your question or check the ERPNext connection."
            # Attempt to store the error response as well
            if self.memory_manager:
                self.memory_manager.store_conversation(self.session_id, user_input, error_message)
            return error_message

# Example usage and configuration
def create_aida_agent_with_credentials(erpnext_url: str, username: str, password: str, 
                                        google_api_key: str, mongo_uri: str = None, session_id: str = None):
    """
    Factory function to create Aida AI agent with provided credentials.
    This replaces the old create_aida_agent function for multi-tenant use.
    """
    
    if not all([erpnext_url, username, password, google_api_key]):
        raise ValueError("Invalid credentials provided. Ensure ERPNext URL, username, password, and Google API key are all set.")
    if not (erpnext_url.startswith('http://') or erpnext_url.startswith('https://')):
        raise ValueError("ERPNext URL must start with 'http://' or 'https://'.")

    try:
        agent = AidaERPNextAgent(
            erpnext_url=erpnext_url,
            username=username,
            password=password,
            google_api_key=google_api_key,
            mongo_uri=mongo_uri if MONGODB_AVAILABLE else None,
            session_id=session_id
        )
        return agent
    except Exception as e:
        logger.error(f"Failed to create agent instance: {e}", exc_info=True)
        raise Exception(f"Failed to create agent: {e}. Please check your ERPNext connection details and API key.")

    # CRM Tool Methods
    def _create_leads_tool(self, input_str: str) -> str:
        """Create leads from Google Maps business search."""
        if not self.lead_creation_agent:
            return json.dumps({"success": False, "message": "Lead creation not available. Google Maps API key required."})
        
        try:
            params = json.loads(input_str)
            business_type = params.get("business_type")
            location = params.get("location")
            count = params.get("count", 10)
            
            if not business_type or not location:
                return json.dumps({"success": False, "message": "business_type and location are required"})
            
            # Create leads using the lead creation agent
            result = self.lead_creation_agent.create_leads(
                business_type=business_type,
                location=location,
                count=count
            )
            
            return json.dumps({"success": True, "result": result})
            
        except Exception as e:
            logger.error(f"Lead creation failed: {e}")
            return json.dumps({"success": False, "message": f"Lead creation failed: {str(e)}"})
    
    def _search_businesses_tool(self, input_str: str) -> str:
        """Search businesses on Google Maps without creating leads."""
        if not self.gmaps_extractor:
            return json.dumps({"success": False, "message": "Google Maps search not available. API key required."})
        
        try:
            params = json.loads(input_str)
            business_type = params.get("business_type")
            location = params.get("location")
            count = params.get("count", 10)
            
            if not business_type or not location:
                return json.dumps({"success": False, "message": "business_type and location are required"})
            
            # Search businesses using Google Maps
            businesses = self.gmaps_extractor.search_businesses(
                business_type=business_type,
                location=location,
                max_results=count
            )
            
            return json.dumps({"success": True, "businesses": businesses, "count": len(businesses)})
            
        except Exception as e:
            logger.error(f"Business search failed: {e}")
            return json.dumps({"success": False, "message": f"Business search failed: {str(e)}"})
    
    def _run_outreach_campaign_tool(self, input_str: str) -> str:
        """Run automated outreach campaign."""
        if not self.lead_outreach_agent:
            return json.dumps({"success": False, "message": "Outreach campaigns not available."})
        
        try:
            params = json.loads(input_str)
            lead_count = params.get("lead_count", 50)
            industry = params.get("industry")
            min_employees = params.get("min_employees")
            status = params.get("status", "Lead")
            
            # Get leads for outreach
            leads = self.lead_outreach_agent.get_leads_for_outreach(
                lead_count=lead_count,
                industry=industry,
                min_employees=min_employees,
                status=status
            )
            
            if not leads:
                return json.dumps({"success": False, "message": "No leads found matching criteria"})
            
            # Run outreach campaign
            campaign_results = []
            for lead in leads:
                try:
                    result = self.lead_outreach_agent.send_personalized_email(
                        lead=lead,
                        outreach_type="introduction"
                    )
                    campaign_results.append({"lead": lead.get("name"), "status": "sent", "result": result})
                except Exception as e:
                    campaign_results.append({"lead": lead.get("name"), "status": "failed", "error": str(e)})
            
            return json.dumps({
                "success": True, 
                "campaign_results": campaign_results,
                "total_leads": len(leads),
                "sent_count": len([r for r in campaign_results if r["status"] == "sent"])
            })
            
        except Exception as e:
            logger.error(f"Outreach campaign failed: {e}")
            return json.dumps({"success": False, "message": f"Outreach campaign failed: {str(e)}"})
    
    def _get_campaign_stats_tool(self, input_str: str) -> str:
        """Get campaign statistics."""
        try:
            params = json.loads(input_str) if input_str.strip() else {}
            days = params.get("days", 30)
            
            # Calculate date range
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Get email statistics from ERPNext
            try:
                emails_sent = self._safe_erpnext_call(
                    "get_list",
                    "Email Queue",
                    filters={
                        "creation": ["between", [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]],
                        "status": "Sent"
                    },
                    fields=["name", "recipient", "subject", "creation"]
                )
                
                leads_contacted = self._safe_erpnext_call(
                    "get_list",
                    "Lead",
                    filters={
                        "modified": ["between", [start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")]]
                    },
                    fields=["name", "lead_name", "status", "modified"]
                )
                
                return json.dumps({
                    "success": True,
                    "period_days": days,
                    "emails_sent": len(emails_sent),
                    "leads_contacted": len(leads_contacted),
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d")
                })
                
            except Exception as e:
                return json.dumps({"success": False, "message": f"Failed to get campaign stats: {str(e)}"})
            
        except Exception as e:
            logger.error(f"Campaign stats failed: {e}")
            return json.dumps({"success": False, "message": f"Campaign stats failed: {str(e)}"})
    
    def _get_leads_for_outreach_tool(self, input_str: str) -> str:
        """Get leads suitable for outreach."""
        if not self.lead_outreach_agent:
            return json.dumps({"success": False, "message": "Lead outreach not available."})
        
        try:
            params = json.loads(input_str) if input_str.strip() else {}
            industry = params.get("industry")
            min_employees = params.get("min_employees")
            status = params.get("status", "Lead")
            limit = params.get("limit", 50)
            
            leads = self.lead_outreach_agent.get_leads_for_outreach(
                lead_count=limit,
                industry=industry,
                min_employees=min_employees,
                status=status
            )
            
            return json.dumps({
                "success": True,
                "leads": leads,
                "count": len(leads),
                "filters_applied": {
                    "industry": industry,
                    "min_employees": min_employees,
                    "status": status
                }
            })
            
        except Exception as e:
            logger.error(f"Get leads for outreach failed: {e}")
            return json.dumps({"success": False, "message": f"Get leads for outreach failed: {str(e)}"})

def create_aida_agent():
    """
    Legacy factory function - now requires environment variables.
    This is kept for backward compatibility with direct script usage.
    """
    
    ERPNEXT_URL = os.getenv("ERPNEXT_URL")
    USERNAME = os.getenv("ERPNEXT_USERNAME")
    PASSWORD = os.getenv("ERPNEXT_PASSWORD")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GMAPS_API_KEY = os.getenv("GMAPS_API_KEY")
    MONGO_URI = os.getenv("MONGO_URI")
    
    if not all([ERPNEXT_URL, USERNAME, PASSWORD, GOOGLE_API_KEY]):
        print("⚠️ Please set the following environment variables for direct script usage:")
        print("  - ERPNEXT_URL (e.g., http://localhost:8000)")
        print("  - ERPNEXT_USERNAME")
        print("  - ERPNEXT_PASSWORD")
        print("  - GOOGLE_API_KEY")
        print("Optional: GMAPS_API_KEY (for CRM lead generation)")
        print("Optional: MONGO_URI (e.g., mongodb://localhost:27017/)")
        return None
    
    try:
        agent = AidaERPNextAgent(
            erpnext_url=ERPNEXT_URL,
            username=USERNAME,
            password=PASSWORD,
            google_api_key=GOOGLE_API_KEY,
            gmaps_api_key=GMAPS_API_KEY,
            mongo_uri=MONGO_URI if MONGO_URI and MONGODB_AVAILABLE else None
        )
        return agent
    except Exception as e:
        print(f"❌ Failed to create agent: {e}")
        logger.error(f"Failed to create agent from env vars: {e}", exc_info=True)
        return None

if __name__ == "__main__":
    print("🚀 Initializing Aida AI ERPNext Assistant...")
    
    aida = create_aida_agent()
    
    if not aida:
        print("❌ Failed to initialize Aida AI. Please check your configuration and environment variables.")
        exit(1)
    
    print(f"🤖 Hi! I'm Aida AI, your ERPNext assistant (Session ID: {aida.session_id})!")
    print("Ask me anything about your ERPNext data or to help you create/update records.\n")
    print("You can type 'quit', 'exit', or 'bye' to end the conversation.")
    
    # Generate dynamic example queries using LLM
    try:
        available_features = ["ERPNext data queries", "record creation/updates", "doctype exploration"]
        if aida.lead_creation_agent:
            available_features.append("lead generation from Google Maps")
        if aida.lead_outreach_agent:
            available_features.append("automated outreach campaigns")
        
        prompt = f"""
Generate 10-12 diverse, practical example queries that users can ask an ERPNext AI assistant. 

Available features: {', '.join(available_features)}

Requirements:
1. Mix of different query types: data retrieval, record creation, updates, how-to questions
2. Use realistic business scenarios and data
3. Include both simple and complex queries
4. Make them actionable and specific
5. Cover different ERPNext modules (Sales, CRM, Stock, etc.)
6. Each query should be on a separate line
7. Don't number them
8. Keep each query under 100 characters
9. Make them sound natural and conversational

Generate the example queries:"""
        
        llm_response = aida.llm.invoke(prompt)
        generated_content = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
        
        # Parse the generated queries
        example_queries = []
        for line in generated_content.strip().split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and len(line) > 10:
                # Remove any numbering or bullet points
                line = re.sub(r'^\d+\.\s*', '', line)
                line = re.sub(r'^[-*]\s*', '', line)
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1]
                example_queries.append(line)
        
        # Fallback to some basic queries if LLM generation fails
        if len(example_queries) < 5:
            example_queries = [
                "How many customers do we have?",
                "Show me recent sales orders",
                "Create a new lead for ABC Company",
                "What fields are needed for a quotation?",
                "List all items in stock"
            ]
            
    except Exception as e:
        logger.warning(f"Failed to generate dynamic example queries: {e}")
        # Fallback to basic static queries
        example_queries = [
            "How many customers do we have?",
            "Show me recent sales orders",
            "Create a new lead for ABC Company",
            "What fields are needed for a quotation?",
            "List all items in stock",
            "How to create a sales invoice?",
            "Update customer contact information",
            "Show me pending tasks"
        ]
    
    print("\n" + "="*50)
    print("Example queries you can try:")
    for i, query in enumerate(example_queries, 1):
        print(f"{i}. {query}")
    print("="*50 + "\n")

    try:
        while True:
            user_input_str = input("\n👤 You: ").strip()
            if not user_input_str:
                continue
            if user_input_str.lower() in ['quit', 'exit', 'bye']:
                print("\n👋 Goodbye! It was great helping you with ERPNext!")
                break
            
            print("\n🤖 Aida:", end=" ", flush=True)
            response_str = aida.chat(user_input_str)
            print(response_str)
            
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye! It was great helping you with ERPNext!")
    except Exception as e_main:
        print(f"\n❌ An unexpected error occurred in the main loop: {e_main}")
        logger.error(f"Main loop error: {e_main}", exc_info=True)
        print("Please try again or type 'quit' to exit.")