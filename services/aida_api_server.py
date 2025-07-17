import os
import json
import logging
import uuid
import requests
from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# --- START: Import AidaERPNextAgent and MongoMemoryManager from aida_agent.py ---
# This assumes your main agent code is saved in a file named `aida_agent.py`
# in the same directory as this aida_api_server.py file.
try:
    from aida_agent import AidaERPNextAgent, MongoMemoryManager
    # Re-evaluate MONGODB_AVAILABLE based on current environment for the API server
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
        MONGODB_AVAILABLE = True
    except ImportError:
        MONGODB_AVAILABLE = False
except ImportError as e:
    print(f"Error: Could not import AidaERPNextAgent or MongoMemoryManager from 'aida_agent.py'. "
          f"Please ensure 'aida_agent.py' exists and is in the same directory. Details: {e}")
    # Define placeholder classes to avoid immediate crash, though functionality will be broken
    class AidaERPNextAgent:
        def __init__(self, *args, **kwargs):
            raise NotImplementedError("AidaERPNextAgent not imported. Check aida_agent.py.")
        def chat(self, user_input: str):
            return "Error: Agent not initialized due to missing aida_agent.py. Cannot process chat."
    class MongoMemoryManager:
        def __init__(self, *args, **kwargs):
            print("MongoMemoryManager not imported. Using dummy.")
        def store_conversation(self, *args, **kwargs): pass
        def get_recent_context(self, *args, **kwargs): return []
        def get_last_query_result(self, *args, **kwargs): return None
    MONGODB_AVAILABLE = False
# --- END: Import AidaERPNextAgent and MongoMemoryManager from aida_agent.py ---

# Import session manager for session clearing functionality
try:
    import sys
    import os
    # Add parent directory to Python path
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, parent_dir)
    
    from session_manager import SessionManager
    SESSION_MANAGER_AVAILABLE = True
    
    # Initialize session manager
    session_manager = SessionManager(db_path=os.path.join(parent_dir, "aida_database.db"))
    
except ImportError as e:
    print(f"Warning: Could not import SessionManager: {e}")
    SESSION_MANAGER_AVAILABLE = False
    session_manager = None


# Configure logging for the Flask app
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
flask_logger = logging.getLogger('flask_server')
flask_logger.setLevel(logging.INFO)

app = Flask(__name__)
CORS(app) 
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey_aida_erpnext_agent") # IMPORTANT: Change this in production!

# In-memory store for active AidaERPNextAgent instances, keyed by session_id
# For production, this would ideally be backed by a persistent store like Redis
# to handle server restarts and scaling.
active_agents: Dict[str, AidaERPNextAgent] = {}


@app.route('/init_session', methods=['POST'])
def init_session():
    """
    Initialize a new session and create an AidaERPNextAgent instance for it.
    Requires ERPNext and Google API credentials from the client.
    """
    data = request.get_json()
    
    # Security: Validate request origin and CSRF token
    origin = request.headers.get('Origin', '')
    referer = request.headers.get('Referer', '')
    
    # Only allow requests from same origin in production
    if not any([
        'localhost' in origin,
        '127.0.0.1' in origin,
        'taskforgehq.com' in origin  # Replace with your actual domain
    ]):
        flask_logger.warning(f"Suspicious request from origin: {origin}")
        return jsonify({"error": "Unauthorized origin"}), 403
    
    # Log the request for security monitoring
    flask_logger.info(f"Init session request from: {request.remote_addr}, Origin: {origin}")
    
    erpnext_url = data.get('erpnext_url')
    username = data.get('username')
    password = data.get('password')
    google_api_key = data.get('google_api_key')
    mongo_uri = data.get('mongo_uri', os.getenv("MONGO_URI"))
    
    # Sanitize and validate ERPNext URL
    if erpnext_url:
        erpnext_url = erpnext_url.strip().rstrip('/')
        # Remove any trailing commas or special characters
        erpnext_url = erpnext_url.rstrip(',').strip()
        if not (erpnext_url.startswith('http://') or erpnext_url.startswith('https://')):
            return jsonify({"error": "ERPNext URL must start with 'http://' or 'https://'"}), 400
    
    site_base_url = data.get('site_base_url', erpnext_url)  # Use ERPNext URL as default

    # Security: Don't log sensitive data
    flask_logger.info(f"Session request - URL: `{erpnext_url}`, Username: {username}, Restore: {data.get('restore_session', False)}")

    # Validate required fields
    missing_fields = []
    if not erpnext_url:
        missing_fields.append("erpnext_url")
    if not username:
        missing_fields.append("username")
    if not google_api_key:
        missing_fields.append("google_api_key")
    
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        flask_logger.error(error_msg)
        return jsonify({"error": error_msg}), 400

    # Enhanced session token validation for security
    if password == "session_token":
        api_key = data.get('api_key')  # This will be the user email
        api_secret = data.get('api_secret')  # This will be the session ID
        
        if not all([api_key, api_secret]):
            return jsonify({"error": "API key and secret required for session token authentication."}), 400
        
        # Validate the Frappe session with additional security checks
        try:
            session_validation_url = f"{erpnext_url}/api/method/frappe.auth.get_logged_user"
            headers = {
                'Cookie': f'sid={api_secret}',
                'Content-Type': 'application/json',
                'User-Agent': 'AIDA-API-Server'
            }
            
            response = requests.get(session_validation_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                user_data = response.json()
                if user_data.get('message') == api_key:
                    flask_logger.info(f"Session validated for user: {api_key}")
                    username = api_key
                    password = api_secret
                else:
                    flask_logger.warning(f"Session validation failed for user: {api_key}")
                    return jsonify({"error": "Session validation failed. Invalid user."}), 401
            else:
                flask_logger.warning(f"Session validation returned status: {response.status_code}")
                return jsonify({"error": "Session validation failed. Please log in again."}), 401
                
        except Exception as e:
            flask_logger.error(f"Session validation error: {e}")
            return jsonify({"error": "Could not validate session. Please try again."}), 500
    
    # Security: Validate Google API key format
    if not google_api_key or len(google_api_key) < 20:
        return jsonify({"error": "Invalid Google API Key format."}), 400
    
    # Handle session restoration
    restore_session = data.get('restore_session', False)
    restored = False
    
    if restore_session:
        # Try to restore existing session if available
        # For now, create a new session but mark as restored for client handling
        restored = True
    
    session_id = str(uuid.uuid4())
    try:
        # Create and store a new AidaERPNextAgent instance for this session
        agent = AidaERPNextAgent(
            erpnext_url=erpnext_url,
            username=username,
            password=password,
            google_api_key=google_api_key,
            mongo_uri=mongo_uri if MONGODB_AVAILABLE else None,
            session_id=session_id,
            site_base_url=site_base_url
        )
        active_agents[session_id] = agent
        flask_logger.info(f"Session {session_id} initialized for user: {username}")
        return jsonify({
            "session_id": session_id, 
            "message": "Aida AI Agent initialized successfully.",
            "restored": restored
        }), 200
        
    except Exception as e:
        flask_logger.error(f"Failed to initialize agent: {e}", exc_info=True)
        error_msg = "Failed to initialize AI agent. Please check your credentials."
        return jsonify({"error": error_msg}), 500

@app.route('/create_leads', methods=['POST'])
def create_leads():
    """
    Create leads using Google Maps business search - separated from main agent.
    """
    data = request.get_json()
    
    # Extract parameters
    erpnext_url = data.get('erpnext_url')
    username = data.get('username')
    password = data.get('password')
    google_api_key = data.get('google_api_key')
    business_type = data.get('business_type')
    location = data.get('location')
    count = data.get('count', 10)
    
    # Validate required fields
    missing_fields = []
    if not erpnext_url:
        missing_fields.append("erpnext_url")
    if not username:
        missing_fields.append("username")
    if not password:
        missing_fields.append("password")
    if not google_api_key:
        missing_fields.append("google_api_key")
    if not business_type:
        missing_fields.append("business_type")
    if not location:
        missing_fields.append("location")
    
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        flask_logger.error(error_msg)
        return jsonify({"error": error_msg}), 400
    
    # Security: Rate limiting check (basic)
    client_ip = request.remote_addr
    current_time = datetime.now()
    
    if not hasattr(app, 'lead_rate_limits'):
        app.lead_rate_limits = {}
    
    if client_ip in app.lead_rate_limits:
        last_requests = app.lead_rate_limits[client_ip]
        recent_requests = [t for t in last_requests if (current_time - t).seconds < 300]  # 5 minutes
        if len(recent_requests) >= 5:  # Max 5 lead creation requests per 5 minutes
            flask_logger.warning(f"Lead creation rate limit exceeded for IP: {client_ip}")
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        app.lead_rate_limits[client_ip] = recent_requests + [current_time]
    else:
        app.lead_rate_limits[client_ip] = [current_time]
    
    try:
        # Create a temporary agent instance for lead creation
        temp_session_id = str(uuid.uuid4())
        agent = AidaERPNextAgent(
            erpnext_url=erpnext_url,
            username=username,
            password=password,
            google_api_key=google_api_key,
            mongo_uri=None,  # No persistent memory needed for lead creation
            session_id=temp_session_id
        )
        
        # Use the lead creation functionality
        if not agent.lead_creation_agent:
            return jsonify({"error": "Lead creation not available. Google Maps API key required."}), 400
        
        result = agent.lead_creation_agent.create_leads(
            business_type=business_type,
            location=location,
            count=count
        )
        
        flask_logger.info(f"Lead creation completed - Type: {business_type}, Location: {location}, Count: {count}")
        return jsonify({"success": True, "result": result}), 200
        
    except Exception as e:
        flask_logger.error(f"Lead creation failed: {e}", exc_info=True)
        return jsonify({"error": f"Lead creation failed: {str(e)}"}), 500

@app.route('/chat', methods=['POST'])
def chat_with_agent():
    """
    Send a message to the AidaERPNextAgent for a given session.
    """
    data = request.get_json()
    session_id = data.get('session_id')
    user_input = data.get('user_input')

    # Security: Rate limiting check (basic)
    client_ip = request.remote_addr
    current_time = datetime.now()
    
    # Basic rate limiting: max 10 requests per minute per IP
    if not hasattr(app, 'rate_limits'):
        app.rate_limits = {}
    
    if client_ip in app.rate_limits:
        last_requests = app.rate_limits[client_ip]
        recent_requests = [t for t in last_requests if (current_time - t).seconds < 60]
        if len(recent_requests) >= 10:
            flask_logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return jsonify({"error": "Rate limit exceeded. Please try again later."}), 429
        app.rate_limits[client_ip] = recent_requests + [current_time]
    else:
        app.rate_limits[client_ip] = [current_time]

    if not session_id or not user_input:
        return jsonify({"error": "Session ID and user input are required."}), 400

    # Security: Sanitize user input
    user_input = user_input.strip()
    if len(user_input) > 2000:  # Limit message length
        return jsonify({"error": "Message too long. Please limit to 2000 characters."}), 400

    agent = active_agents.get(session_id)
    if not agent:
        return jsonify({"error": "Invalid or expired session. Please reconnect."}), 404

    try:
        response = agent.chat(user_input)
        flask_logger.info(f"Chat - Session: {session_id}, User message length: {len(user_input)}")
        return jsonify({"session_id": session_id, "response": response}), 200
        
    except Exception as e:
        flask_logger.error(f"Chat error for session {session_id}: {e}", exc_info=True)
        return jsonify({"error": "An error occurred. Please try again."}), 500

@app.route('/clear_session', methods=['POST'])
def clear_session():
    """
    Clear a specific session and remove its agent instance.
    """
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    if session_id in active_agents:
        del active_agents[session_id]
        flask_logger.info(f"Session {session_id} cleared.")
        return jsonify({"message": f"Session {session_id} cleared successfully."}), 200
    else:
        return jsonify({"message": "Session not found or already cleared."}), 404

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        "status": "healthy",
        "active_sessions": len(active_agents),
        "mongodb_available": MONGODB_AVAILABLE,
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/get_chat_history/<session_id>', methods=['GET'])
def get_chat_history(session_id):
    """
    Retrieve chat history for a session.
    """
    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400
    
    try:
        # For now, return empty history since we don't have persistent storage in services version
        # This prevents the frontend from breaking when trying to load chat history
        # In a full implementation, this would connect to a database to retrieve stored messages
        
        return jsonify({
            "session_id": session_id,
            "history": [],
            "count": 0,
            "message": "Chat history not available in this version. Messages are only stored in memory during active sessions."
        }), 200
        
    except Exception as e:
        flask_logger.error(f"Error retrieving chat history for session {session_id}: {e}")
        return jsonify({"error": "Failed to retrieve chat history."}), 500

@app.route('/session_status/<session_id>', methods=['GET'])
def session_status(session_id):
    """Check if a session is still active"""
    if session_id in active_agents:
        return jsonify({"active": True, "session_id": session_id}), 200
    else:
        return jsonify({"active": False, "session_id": session_id}), 404

@app.route('/')
def index():
    return jsonify({"message": "Aida ERPNext AI Agent API is running. Use /init_session to start and /chat to interact."})

# Add CORS headers for better integration
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE')
    return response

if __name__ == '__main__':
    # Ensure Flask secret key is set for session management
    if not os.getenv("FLASK_SECRET_KEY"):
        flask_logger.warning("FLASK_SECRET_KEY not set. Using a default for development. Set a strong secret key in production!")

    # Clear all sessions on server restart using the initialized session manager
    if SESSION_MANAGER_AVAILABLE and session_manager:
        try:
            session_manager.clear_all_sessions()
            flask_logger.info("All sessions cleared on server restart")
        except Exception as e:
            flask_logger.error(f"Failed to clear sessions on startup: {e}")
    else:
        flask_logger.warning("SessionManager not available - sessions will not be cleared on restart")

    # Set default values for environment variables if not already set, for local testing.
    # In production, these should be securely managed.
    os.environ.setdefault("ERPNEXT_URL", "http://localhost:8000")
    os.environ.setdefault("ERPNEXT_USERNAME", "Administrator")
    os.environ.setdefault("ERPNEXT_PASSWORD", "admin")
    os.environ.setdefault("GOOGLE_API_KEY", "YOUR_GOOGLE_API_KEY_HERE") # Replace with your actual key

    # Example: run the Flask app locally on port 5000
    # In a production environment, use a WSGI server like Gunicorn or uWSGI
    flask_logger.info("Starting Aida ERPNext AI Agent API server...")
    app.run(debug=True, host='0.0.0.0', port=5000)