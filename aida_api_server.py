import os
import json
import logging
import uuid
import requests
from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Environment variables from .env file will not be loaded.")

# --- START: Import AidaERPNextAgent and SessionManager ---
try:
    from services.aida_agent import AidaERPNextAgent, MongoMemoryManager
    from session_manager import SessionManager
    # Re-evaluate MONGODB_AVAILABLE based on current environment for the API server
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
        MONGODB_AVAILABLE = True
    except ImportError:
        MONGODB_AVAILABLE = False
except ImportError as e:
    print(f"Error: Could not import required modules. Details: {e}")
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
    class SessionManager:
        def __init__(self, *args, **kwargs): pass
        def create_session(self, *args, **kwargs): return str(uuid.uuid4())
        def get_session(self, *args, **kwargs): return None
    MONGODB_AVAILABLE = False
# --- END: Import AidaERPNextAgent and SessionManager ---


# Configure logging for the Flask app
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
flask_logger = logging.getLogger('flask_server')
flask_logger.setLevel(logging.INFO)

app = Flask(__name__)
CORS(app, origins="*")  # Allow all origins
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey_aida_erpnext_agent") # IMPORTANT: Change this in production!

# Initialize session manager for persistent sessions with SQLite
db_path = os.getenv("AIDA_DB_PATH", "aida_database.db")
session_manager = SessionManager(db_path=db_path)

# In-memory store for active AidaERPNextAgent instances, keyed by session_id
# Sessions are now persistent, but agents are still created on-demand
active_agents: Dict[str, AidaERPNextAgent] = {}

# Clear all sessions on server restart to ensure fresh state
session_manager.clear_all_sessions()
flask_logger.info("All sessions cleared on server restart")


@app.route('/init_session', methods=['POST'])
def init_session():
    """
    Initialize or restore a persistent session with credential storage.
    Supports both new sessions and existing session restoration.
    """
    data = request.get_json()
    
    # Get request information for logging and session management
    origin = request.headers.get('Origin', '')
    user_agent = request.headers.get('User-Agent', '')
    ip_address = request.remote_addr
    
    flask_logger.info(f"Init session request from: {ip_address}, Origin: {origin}")
    
    erpnext_url = data.get('erpnext_url')
    username = data.get('username')
    password = data.get('password')
    google_api_key = os.getenv('GOOGLE_API_KEY')
    site_base_url = data.get('site_base_url', erpnext_url)
    restore_session = data.get('restore_session', True)  # Default to trying to restore

    # Security: Don't log sensitive data
    flask_logger.info(f"Session request - URL: {erpnext_url}, Username: {username}, Restore: {restore_session}")

    # Validate required fields
    missing_fields = []
    if not erpnext_url:
        missing_fields.append("erpnext_url")
    if not username:
        missing_fields.append("username")
    
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        flask_logger.error(error_msg)
        return jsonify({"error": error_msg}), 400
    
    # Validate Google API key from environment
    if not google_api_key:
        error_msg = "Google API key not configured on server. Please check environment variables."
        flask_logger.error(error_msg)
        return jsonify({"error": error_msg}), 500

    # Enhanced session token validation for security
    if password == "session_token":
        api_key = data.get('api_key')  # This will be the user email
        api_secret = data.get('api_secret')  # This will be the session ID
        
        if not all([api_key, api_secret]):
            return jsonify({"error": "API key and secret required for session token authentication."}), 400
        
        # Validate the Frappe session
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
    
    # Security: Validate Google API key format from environment
    if len(google_api_key) < 20:
        return jsonify({"error": "Invalid Google API Key format in server configuration."}), 500
    
    try:
        # Try to find existing session if restore is enabled
        existing_session_id = None
        if restore_session:
            existing_session_id = session_manager.find_existing_session(
                user_agent, ip_address, erpnext_url, username
            )
            
            if existing_session_id:
                # Verify credentials match
                if session_manager.verify_credentials(existing_session_id, password, google_api_key):
                    # Update access time and restore session
                    session_manager.update_session_access(existing_session_id)
                    
                    # Create agent if not already active
                    if existing_session_id not in active_agents:
                        session_data = session_manager.get_session(existing_session_id)
                        agent = AidaERPNextAgent(
                            erpnext_url=session_data.erpnext_url,
                            username=session_data.username,
                            password=password,  # Use actual password, not hash
                            google_api_key=google_api_key,  # Use actual key, not hash
                            mongo_uri=None,  # Using SQLite now
                            session_id=existing_session_id,
                            site_base_url=session_data.site_base_url
                        )
                        active_agents[existing_session_id] = agent
                    
                    flask_logger.info(f"Restored session {existing_session_id} for user: {username}")
                    return jsonify({
                        "session_id": existing_session_id, 
                        "message": "Session restored successfully.",
                        "restored": True
                    }), 200
                else:
                    flask_logger.info(f"Credentials changed for user {username}, creating new session")
        
        # Create new session
        session_id = session_manager.create_session(
            erpnext_url=erpnext_url,
            username=username,
            password=password,
            google_api_key=google_api_key,
            user_agent=user_agent,
            ip_address=ip_address,
            site_base_url=site_base_url
        )
        
        # Create and store agent instance
        agent = AidaERPNextAgent(
            erpnext_url=erpnext_url,
            username=username,
            password=password,
            google_api_key=google_api_key,
            mongo_uri=None,  # Using SQLite now
            session_id=session_id,
            site_base_url=site_base_url
        )
        active_agents[session_id] = agent
        
        flask_logger.info(f"New session {session_id} created for user: {username}")
        return jsonify({
            "session_id": session_id, 
            "message": "New session created successfully.",
            "restored": False
        }), 200
        
    except Exception as e:
        flask_logger.error(f"Failed to initialize session: {e}", exc_info=True)
        error_msg = "Failed to initialize session. Please check your credentials."
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
    Send a message to the AidaERPNextAgent for a given session with persistent history.
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

    # Verify session exists in session manager
    session_data = session_manager.get_session(session_id)
    if not session_data:
        return jsonify({"error": "Invalid or expired session. Please reconnect."}), 404
    
    # Update session access time
    session_manager.update_session_access(session_id)

    # Get or create agent instance
    agent = active_agents.get(session_id)
    if not agent:
        # Session exists but agent not in memory - recreate it
        # This can happen after server restart
        try:
            # We need to get actual credentials from the client again
            # For now, return an error asking to reconnect
            return jsonify({
                "error": "Session found but agent not active. Please reconnect to restore your session.",
                "reconnect_required": True
            }), 410
        except Exception as e:
            flask_logger.error(f"Failed to recreate agent for session {session_id}: {e}")
            return jsonify({"error": "Failed to restore session. Please reconnect."}), 500

    try:
        # Get chat response from agent
        response = agent.chat(user_input)
        
        # Store in session manager's chat history (in addition to agent's memory)
        # This provides redundant storage and better persistence
        session_manager.store_chat_message(
            session_id=session_id,
            user_message=user_input,
            ai_response=response
        )
        
        flask_logger.info(f"Chat - Session: {session_id}, User message length: {len(user_input)}")
        return jsonify({"session_id": session_id, "response": response}), 200
        
    except Exception as e:
        flask_logger.error(f"Chat error for session {session_id}: {e}", exc_info=True)
        error_response = "An error occurred. Please try again."
        
        # Store error in chat history too
        session_manager.store_chat_message(
            session_id=session_id,
            user_message=user_input,
            ai_response=error_response
        )
        
        return jsonify({"error": error_response}), 500

@app.route('/get_chat_history/<session_id>', methods=['GET'])
def get_chat_history(session_id):
    """
    Retrieve chat history for a session.
    """
    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400
    
    # Verify session exists
    session_data = session_manager.get_session(session_id)
    if not session_data:
        return jsonify({"error": "Invalid session."}), 404
    
    try:
        # Get chat history from session manager
        limit = request.args.get('limit', 20, type=int)
        history = session_manager.get_chat_history(session_id, limit=limit)
        
        # Format history for frontend
        formatted_history = []
        i = 0
        while i < len(history):
            msg = history[i]
            if msg["role"] == "user":
                user_message = msg["content"]
                ai_response = ""
                
                # Look for the corresponding AI response
                if i + 1 < len(history) and history[i + 1]["role"] == "assistant":
                    ai_response = history[i + 1]["content"]
                    i += 1  # Skip the AI message in next iteration
                
                formatted_history.append({
                    "timestamp": msg["timestamp"],
                    "user_message": user_message,
                    "ai_response": ai_response
                })
            i += 1
        
        return jsonify({
            "session_id": session_id,
            "history": formatted_history,
            "count": len(formatted_history)
        }), 200
        
    except Exception as e:
        flask_logger.error(f"Error retrieving chat history for session {session_id}: {e}")
        return jsonify({"error": "Failed to retrieve chat history."}), 500

@app.route('/clear_session', methods=['POST'])
def clear_session():
    """
    Clear a specific session and remove its agent instance.
    """
    data = request.get_json()
    session_id = data.get('session_id')

    if not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    # Remove from active agents
    if session_id in active_agents:
        del active_agents[session_id]
        flask_logger.info(f"Active agent for session {session_id} cleared.")
    
    # Note: We don't delete the session from session_manager to preserve history
    # The session will naturally expire based on the cleanup policy
    
    return jsonify({"message": f"Session {session_id} cleared successfully."}), 200

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring"""
    return jsonify({
        "status": "healthy",
        "active_sessions": len(active_agents),
        "mongodb_available": MONGODB_AVAILABLE,
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/session_status/<session_id>', methods=['GET'])
def session_status(session_id):
    """Check if a session is still active"""
    try:
        session = session_manager.get_session(session_id)
        if session:
            # Update last access time
            session_manager.update_session_access(session_id)
            return jsonify({"active": True, "session_id": session_id})
        else:
            return jsonify({"active": False}), 404
    except Exception as e:
        flask_logger.error(f"Error checking session status: {str(e)}")
        return jsonify({"error": "Failed to check session status"}), 500

@app.route('/')
def index():
    """Serve the main web UI"""
    return send_from_directory('web_ui', 'index.html')

@app.route('/web_ui/<path:filename>')
def serve_static(filename):
    """Serve static files from web_ui directory"""
    return send_from_directory('web_ui', filename)

@app.route('/styles.css')
def serve_styles():
    """Serve styles.css directly"""
    return send_from_directory('web_ui', 'styles.css')

@app.route('/script.js')
def serve_script():
    """Serve script.js directly"""
    return send_from_directory('web_ui', 'script.js')

@app.route('/api')
def api_info():
    """API information endpoint"""
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