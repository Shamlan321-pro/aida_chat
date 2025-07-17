# AIDA Platform Database Setup Guide

## Overview

The AIDA Platform now uses **SQLite** as its database system for storing user sessions and chat history. This provides a lightweight, file-based database solution that requires no additional server setup or configuration.

## Database Features

### Multi-User Support
- **User Identification**: Users are uniquely identified by the combination of:
  - ERPNext URL
  - Username
  - Browser fingerprint (generated from User-Agent and IP address)

### Multi-Instance Support
- **ERPNext Instance Isolation**: The same username can exist across multiple ERPNext instances without conflicts
- **Session Management**: Each ERPNext URL + Username combination maintains separate sessions and chat history

### Data Storage
- **User Sessions**: Stores session credentials, timestamps, and browser fingerprints
- **Chat History**: Stores complete conversation history with timestamps, user messages, AI responses, and metadata
- **Automatic Cleanup**: Expired sessions and old chat history are automatically cleaned up

## Installation Requirements

### No Additional Database Software Required!

Unlike the previous MongoDB setup, SQLite is:
- **Built into Python**: No separate database server installation needed
- **File-based**: Database is stored as a single file (`aida_database.db`)
- **Zero Configuration**: Works out of the box with no setup required

### Python Dependencies

The required Python packages are already included in your existing setup:
```
sqlite3  # Built into Python standard library
dataclasses  # Built into Python 3.7+
hashlib  # Built into Python standard library
uuid  # Built into Python standard library
datetime  # Built into Python standard library
```

## Configuration

### Environment Variables

You can optionally set the database file location:

```bash
# Optional: Set custom database file path
set AIDA_DB_PATH=C:\path\to\your\aida_database.db

# If not set, defaults to: aida_database.db (in the application directory)
```

### Database File Location

By default, the database file will be created in the same directory as your AIDA application:
```
c:\Users\Admin\Desktop\aida_platform\aida_database.db
```

## Database Schema

### User Sessions Table
```sql
CREATE TABLE user_sessions (
    session_id TEXT PRIMARY KEY,
    user_identifier TEXT NOT NULL,
    erpnext_url TEXT NOT NULL,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    google_api_key_hash TEXT NOT NULL,
    site_base_url TEXT,
    browser_fingerprint TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    last_accessed TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT 1
);
```

### Chat Messages Table
```sql
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    user_message TEXT NOT NULL,
    ai_response TEXT NOT NULL,
    query_result TEXT,
    doctype TEXT,
    FOREIGN KEY (session_id) REFERENCES user_sessions (session_id)
);
```

## Usage Instructions

### 1. Start the AIDA Server

```bash
cd c:\Users\Admin\Desktop\aida_platform
python aida_api_server.py
```

The database will be automatically created on first run.

### 2. Access the Web Interface

Open your browser and navigate to:
```
http://localhost:5000
```

### 3. Configure Connection

1. Click "Connect to ERPNext"
2. Fill in your details:
   - **ERPNext URL**: Your ERPNext instance URL
   - **Username**: Your ERPNext username
   - **Password**: Your ERPNext password
   - **Google API Key**: Your Google AI API key
   - **API Server URL**: `http://localhost:5000` (default)

### 4. Session Management

- **Automatic Session Restoration**: When you return to the platform, your session and chat history will be automatically restored
- **Multi-Instance Support**: You can connect to different ERPNext instances with the same or different usernames
- **Persistent Chat History**: All conversations are saved and restored across sessions

## Database Management

### Backup

To backup your database, simply copy the database file:
```bash
copy aida_database.db aida_database_backup.db
```

### Restore

To restore from backup:
```bash
copy aida_database_backup.db aida_database.db
```

### View Database Contents

You can use any SQLite browser tool to view the database contents:
- **DB Browser for SQLite** (Free GUI tool)
- **SQLite command line**
- **Python script**

Example Python script to view sessions:
```python
import sqlite3

conn = sqlite3.connect('aida_database.db')
cursor = conn.cursor()

# View all sessions
cursor.execute("SELECT session_id, erpnext_url, username, created_at FROM user_sessions")
for row in cursor.fetchall():
    print(f"Session: {row[0]}, URL: {row[1]}, User: {row[2]}, Created: {row[3]}")

conn.close()
```

### Database Maintenance

- **Automatic Cleanup**: Expired sessions (older than 30 days) are automatically cleaned up
- **Manual Cleanup**: You can manually clean up old data by deleting the database file (will create a fresh database on next startup)

## Security Features

### Data Protection
- **Password Hashing**: Passwords are hashed using SHA-256 before storage
- **API Key Hashing**: Google API keys are hashed before storage
- **Session Isolation**: Each user session is isolated and secure

### Browser Fingerprinting
- **Unique Identification**: Browser fingerprints help identify returning users
- **Session Restoration**: Enables automatic session restoration for the same browser/device

## Troubleshooting

### Database File Permissions

If you encounter permission errors:
```bash
# Ensure the application directory is writable
icacls "c:\Users\Admin\Desktop\aida_platform" /grant Users:F
```

### Database Corruption

If the database becomes corrupted:
1. Stop the AIDA server
2. Delete the `aida_database.db` file
3. Restart the server (a new database will be created)

### Migration from MongoDB

If you were previously using MongoDB:
1. The new system will create a fresh database
2. Previous MongoDB data will not be automatically migrated
3. Users will need to create new sessions

## Performance

### Expected Performance
- **Small to Medium Usage**: Excellent performance for typical usage patterns
- **Large Datasets**: SQLite can handle millions of records efficiently
- **Concurrent Users**: Supports multiple concurrent users with proper locking

### Optimization Tips
- **Regular Cleanup**: The system automatically cleans up old sessions
- **Database Size**: Monitor database file size and backup regularly
- **Disk Space**: Ensure adequate disk space for database growth

## Support

For issues or questions:
1. Check the application logs for error messages
2. Verify database file permissions
3. Ensure adequate disk space
4. Review this guide for configuration options

The SQLite-based system provides a robust, maintenance-free database solution for the AIDA Platform with full multi-user and multi-instance support.