import sqlite3
import json
import uuid
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
import os

logger = logging.getLogger(__name__)

@dataclass
class UserSession:
    """Data class for user session information."""
    session_id: str
    user_identifier: str  # Combination of username + erpnext_url
    erpnext_url: str
    username: str
    password_hash: str
    google_api_key_hash: str
    site_base_url: str
    created_at: datetime
    last_accessed: datetime
    browser_fingerprint: str
    is_active: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        data['created_at'] = self.created_at.isoformat()
        data['last_accessed'] = self.last_accessed.isoformat()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserSession':
        """Create UserSession from dictionary."""
        data['created_at'] = datetime.fromisoformat(data['created_at'])
        data['last_accessed'] = datetime.fromisoformat(data['last_accessed'])
        return cls(**data)

@dataclass
class ChatMessage:
    """Data class for chat messages."""
    message_id: str
    session_id: str
    user_identifier: str
    timestamp: datetime
    message_type: str  # 'user' or 'assistant'
    content: str
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['metadata'] = json.dumps(self.metadata) if self.metadata else None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChatMessage':
        """Create ChatMessage from dictionary."""
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        data['metadata'] = json.loads(data['metadata']) if data['metadata'] else None
        return cls(**data)

class DatabaseManager:
    """SQLite-based database manager for multi-user, multi-instance chat history."""
    
    def __init__(self, db_path: str = "aida_database.db"):
        self.db_path = db_path
        self.init_database()
        logger.info(f"Database manager initialized with database: {db_path}")
    
    def init_database(self):
        """Initialize the SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_identifier TEXT NOT NULL,
                    erpnext_url TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    google_api_key_hash TEXT NOT NULL,
                    site_base_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    browser_fingerprint TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            
            # Create chat_messages table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_identifier TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                )
            """)
            
            # Create indexes for better performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_identifier ON sessions (user_identifier)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_last_accessed ON sessions (last_accessed)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_browser_fingerprint ON sessions (browser_fingerprint)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON chat_messages (session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_identifier ON chat_messages (user_identifier)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON chat_messages (timestamp)")
            
            conn.commit()
            logger.info("Database tables and indexes created successfully")
    
    def _generate_user_identifier(self, username: str, erpnext_url: str) -> str:
        """Generate unique identifier for user+instance combination."""
        # Normalize URL to avoid duplicates
        normalized_url = erpnext_url.rstrip('/').lower()
        identifier_string = f"{username.lower()}@{normalized_url}"
        return hashlib.sha256(identifier_string.encode()).hexdigest()[:16]
    
    def _hash_credential(self, credential: str) -> str:
        """Hash credentials for secure storage."""
        return hashlib.sha256(credential.encode()).hexdigest()
    
    def _generate_browser_fingerprint(self, user_agent: str, ip_address: str) -> str:
        """Generate a simple browser fingerprint."""
        fingerprint_data = f"{user_agent}:{ip_address}"
        return hashlib.md5(fingerprint_data.encode()).hexdigest()
    
    def create_session(self, erpnext_url: str, username: str, password: str,
                      google_api_key: str, user_agent: str, ip_address: str,
                      site_base_url: str = None) -> str:
        """Create a new user session."""
        session_id = str(uuid.uuid4())
        user_identifier = self._generate_user_identifier(username, erpnext_url)
        browser_fingerprint = self._generate_browser_fingerprint(user_agent, ip_address)
        
        session = UserSession(
            session_id=session_id,
            user_identifier=user_identifier,
            erpnext_url=erpnext_url,
            username=username,
            password_hash=self._hash_credential(password),
            google_api_key_hash=self._hash_credential(google_api_key),
            site_base_url=site_base_url or erpnext_url,
            created_at=datetime.now(),
            last_accessed=datetime.now(),
            browser_fingerprint=browser_fingerprint
        )
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (
                    session_id, user_identifier, erpnext_url, username,
                    password_hash, google_api_key_hash, site_base_url,
                    created_at, last_accessed, browser_fingerprint, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id, session.user_identifier, session.erpnext_url,
                session.username, session.password_hash, session.google_api_key_hash,
                session.site_base_url, session.created_at.isoformat(),
                session.last_accessed.isoformat(), session.browser_fingerprint, True
            ))
            conn.commit()
        
        logger.info(f"Created new session {session_id} for user {username} on {erpnext_url}")
        return session_id
    
    def get_session(self, session_id: str) -> Optional[UserSession]:
        """Retrieve session data by session ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sessions WHERE session_id = ? AND is_active = 1
            """, (session_id,))
            
            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                session_data = dict(zip(columns, row))
                return UserSession.from_dict(session_data)
        
        return None
    
    def find_existing_session(self, user_agent: str, ip_address: str,
                             erpnext_url: str, username: str) -> Optional[str]:
        """Find existing active session for user+instance combination."""
        user_identifier = self._generate_user_identifier(username, erpnext_url)
        browser_fingerprint = self._generate_browser_fingerprint(user_agent, ip_address)
        cutoff_date = datetime.now() - timedelta(days=30)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id FROM sessions 
                WHERE user_identifier = ? 
                AND browser_fingerprint = ?
                AND last_accessed > ?
                AND is_active = 1
                ORDER BY last_accessed DESC
                LIMIT 1
            """, (user_identifier, browser_fingerprint, cutoff_date.isoformat()))
            
            row = cursor.fetchone()
            return row[0] if row else None
    
    def update_session_access(self, session_id: str):
        """Update last accessed time for session."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions SET last_accessed = ? WHERE session_id = ?
            """, (datetime.now().isoformat(), session_id))
            conn.commit()
    
    def verify_credentials(self, session_id: str, password: str, google_api_key: str) -> bool:
        """Verify stored credentials match provided ones."""
        session = self.get_session(session_id)
        if not session:
            return False
        
        password_hash = self._hash_credential(password)
        api_key_hash = self._hash_credential(google_api_key)
        
        return (session.password_hash == password_hash and 
                session.google_api_key_hash == api_key_hash)
    
    def store_chat_message(self, session_id: str, message_type: str, content: str,
                          metadata: Dict[str, Any] = None) -> str:
        """Store a chat message."""
        # Get session to get user_identifier
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        message_id = str(uuid.uuid4())
        message = ChatMessage(
            message_id=message_id,
            session_id=session_id,
            user_identifier=session.user_identifier,
            timestamp=datetime.now(),
            message_type=message_type,
            content=content,
            metadata=metadata
        )
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO chat_messages (
                    message_id, session_id, user_identifier, timestamp,
                    message_type, content, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                message.message_id, message.session_id, message.user_identifier,
                message.timestamp.isoformat(), message.message_type,
                message.content, json.dumps(metadata) if metadata else None
            ))
            conn.commit()
        
        logger.debug(f"Stored {message_type} message for session {session_id}")
        return message_id
    
    def get_chat_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get chat history for a session."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT message_type, content, timestamp, metadata
                FROM chat_messages 
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            """, (session_id, limit))
            
            messages = []
            for row in cursor.fetchall():
                message_type, content, timestamp, metadata = row
                messages.append({
                    'role': 'user' if message_type == 'user' else 'assistant',
                    'content': content,
                    'timestamp': timestamp,
                    'metadata': json.loads(metadata) if metadata else None
                })
            
            return messages
    
    def get_user_chat_history(self, username: str, erpnext_url: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all chat history for a user across all sessions on a specific ERPNext instance."""
        user_identifier = self._generate_user_identifier(username, erpnext_url)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id, message_type, content, timestamp, metadata
                FROM chat_messages 
                WHERE user_identifier = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user_identifier, limit))
            
            messages = []
            for row in cursor.fetchall():
                session_id, message_type, content, timestamp, metadata = row
                messages.append({
                    'session_id': session_id,
                    'role': 'user' if message_type == 'user' else 'assistant',
                    'content': content,
                    'timestamp': timestamp,
                    'metadata': json.loads(metadata) if metadata else None
                })
            
            return messages
    
    def deactivate_session(self, session_id: str):
        """Deactivate a session (soft delete)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions SET is_active = 0 WHERE session_id = ?
            """, (session_id,))
            conn.commit()
        
        logger.info(f"Deactivated session {session_id}")
    
    def cleanup_expired_sessions(self, days: int = 30):
        """Clean up expired sessions and their messages."""
        cutoff_date = datetime.now() - timedelta(days=days)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get expired session IDs
            cursor.execute("""
                SELECT session_id FROM sessions 
                WHERE last_accessed < ? OR is_active = 0
            """, (cutoff_date.isoformat(),))
            
            expired_sessions = [row[0] for row in cursor.fetchall()]
            
            if expired_sessions:
                # Delete messages for expired sessions
                placeholders = ','.join('?' * len(expired_sessions))
                cursor.execute(f"""
                    DELETE FROM chat_messages 
                    WHERE session_id IN ({placeholders})
                """, expired_sessions)
                
                # Delete expired sessions
                cursor.execute(f"""
                    DELETE FROM sessions 
                    WHERE session_id IN ({placeholders})
                """, expired_sessions)
                
                conn.commit()
                logger.info(f"Cleaned up {len(expired_sessions)} expired sessions")
    
    def clear_all_sessions(self):
        """Clear all sessions and chat messages. Used on server restart."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Delete all chat messages first (due to foreign key constraint)
            cursor.execute("DELETE FROM chat_messages")
            
            # Delete all sessions
            cursor.execute("DELETE FROM sessions")
            
            conn.commit()
            logger.info("All sessions and chat messages cleared")
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Count active sessions
            cursor.execute("SELECT COUNT(*) FROM sessions WHERE is_active = 1")
            active_sessions = cursor.fetchone()[0]
            
            # Count total messages
            cursor.execute("SELECT COUNT(*) FROM chat_messages")
            total_messages = cursor.fetchone()[0]
            
            # Count unique users
            cursor.execute("SELECT COUNT(DISTINCT user_identifier) FROM sessions")
            unique_users = cursor.fetchone()[0]
            
            # Get database file size
            db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            
            return {
                'active_sessions': active_sessions,
                'total_messages': total_messages,
                'unique_users': unique_users,
                'database_size_bytes': db_size,
                'database_size_mb': round(db_size / (1024 * 1024), 2)
            }