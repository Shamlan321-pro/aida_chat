// AIDA Web UI JavaScript
class AidaWebUI {
    constructor() {
        this.sessionId = null;
        this.isConnected = false;
        this.apiBaseUrl = '';
        this.currentTheme = localStorage.getItem('aida-theme') || 'light';
        
        this.init();
    }
    
    async init() {
        this.setupEventListeners();
        this.setupTheme();
        this.setupAutoResize();
        this.loadSavedConfig();
        
        // Check for existing session on page load
        await this.checkExistingSession();
    }
    
    setupEventListeners() {
        // Mobile menu toggle
        document.getElementById('mobileMenuBtn').addEventListener('click', () => {
            document.getElementById('sidebar').classList.toggle('open');
        });
        
        // Sidebar toggle
        document.getElementById('sidebarToggle').addEventListener('click', () => {
            document.getElementById('sidebar').classList.remove('open');
        });
        
        // Theme toggle
        document.getElementById('themeToggle').addEventListener('click', () => {
            this.toggleTheme();
        });
        
        // Connect button
        document.getElementById('connectBtn').addEventListener('click', () => {
            this.showConfigModal();
        });
        
        // Configuration modal
        document.getElementById('configBtn').addEventListener('click', () => {
            this.showConfigModal();
        });
        
        document.getElementById('configModalClose').addEventListener('click', () => {
            this.hideConfigModal();
        });
        
        document.getElementById('configCancel').addEventListener('click', () => {
            this.hideConfigModal();
        });
        
        document.getElementById('configForm').addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleConnect();
        });
        

        
        // Chat functionality
        document.getElementById('sendBtn').addEventListener('click', () => {
            this.sendMessage();
        });
        
        document.getElementById('messageInput').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        
        document.getElementById('messageInput').addEventListener('input', () => {
            this.updateSendButton();
        });
        
        // Suggestion buttons
        document.querySelectorAll('.suggestion-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('messageInput').value = btn.textContent;
                this.updateSendButton();
                this.sendMessage();
            });
        });
        
        // Session management
        document.getElementById('clearChatBtn').addEventListener('click', () => {
            this.clearChat();
        });
        
        document.getElementById('disconnectBtn').addEventListener('click', () => {
            this.disconnect();
        });
        
        // Close modals on outside click
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                e.target.classList.remove('show');
            }
        });
    }
    
    setupTheme() {
        document.documentElement.setAttribute('data-theme', this.currentTheme);
        const themeIcon = document.querySelector('#themeToggle i');
        themeIcon.className = this.currentTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    }
    
    toggleTheme() {
        this.currentTheme = this.currentTheme === 'light' ? 'dark' : 'light';
        localStorage.setItem('aida-theme', this.currentTheme);
        this.setupTheme();
    }
    
    setupAutoResize() {
        const textarea = document.getElementById('messageInput');
        textarea.addEventListener('input', () => {
            textarea.style.height = 'auto';
            textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
        });
    }
    
    loadSavedConfig() {
        const savedConfig = localStorage.getItem('aida-config');
        if (savedConfig) {
            const config = JSON.parse(savedConfig);
            document.getElementById('erpnextUrl').value = config.erpnextUrl || '';
            document.getElementById('username').value = config.username || '';
        }
    }
    
    saveConfig() {
        const config = {
            erpnextUrl: document.getElementById('erpnextUrl').value,
            username: document.getElementById('username').value
        };
        localStorage.setItem('aida-config', JSON.stringify(config));
    }
    
    showConfigModal() {
        document.getElementById('configModal').classList.add('show');
    }
    
    hideConfigModal() {
        document.getElementById('configModal').classList.remove('show');
    }
    

    
    showLoading(text = 'Loading...') {
        document.getElementById('loadingText').textContent = text;
        document.getElementById('loadingOverlay').classList.add('show');
    }
    
    hideLoading() {
        document.getElementById('loadingOverlay').classList.remove('show');
    }
    
    showToast(message, type = 'info') {
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <div style="display: flex; align-items: center; gap: 0.5rem;">
                <i class="fas ${
                    type === 'success' ? 'fa-check-circle' :
                    type === 'error' ? 'fa-exclamation-circle' :
                    type === 'warning' ? 'fa-exclamation-triangle' :
                    'fa-info-circle'
                }"></i>
                <span>${message}</span>
            </div>
        `;
        
        document.getElementById('toastContainer').appendChild(toast);
        
        setTimeout(() => {
            toast.style.animation = 'slideInRight 0.3s ease-out reverse';
            setTimeout(() => {
                toast.remove();
            }, 300);
        }, 4000);
    }
    
    updateConnectionStatus(connected) {
        this.isConnected = connected;
        const statusIndicator = document.getElementById('statusIndicator');
        const statusText = document.getElementById('statusText');
        
        if (connected) {
            statusIndicator.className = 'status-indicator online';
            statusText.textContent = 'Connected';
        } else {
            statusIndicator.className = 'status-indicator offline';
            statusText.textContent = 'Disconnected';
        }
    }
    
    updateSendButton() {
        const input = document.getElementById('messageInput');
        const sendBtn = document.getElementById('sendBtn');
        sendBtn.disabled = !input.value.trim() || !this.isConnected;
    }
    
    async handleConnect() {
        const formData = {
            erpnext_url: document.getElementById('erpnextUrl').value,
            username: document.getElementById('username').value,
            password: document.getElementById('password').value,
            site_base_url: document.getElementById('erpnextUrl').value,
            restore_session: true  // Enable session restoration
        };
        
        // Validate required fields
        if (!formData.erpnext_url || !formData.username || !formData.password) {
            this.showToast('Please fill in all required fields', 'error');
            return;
        }
        
        this.showLoading('Connecting to AIDA...');
        
        try {
            const response = await fetch(`${this.apiBaseUrl}/init_session`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            });
            
            const data = await response.json();
            
            if (response.ok) {
                this.sessionId = data.session_id;
                this.updateConnectionStatus(true);
                this.hideConfigModal();
                this.showChatInterface();
                this.saveConfig();
                
                // Store session ID for persistence
                localStorage.setItem('aida-session-id', this.sessionId);
                
                if (data.restored) {
                    this.showToast('Session restored successfully!', 'success');
                    // Load chat history for restored session
                    await this.loadChatHistory();
                } else {
                    this.showToast('Successfully connected to AIDA!', 'success');
                    // Send welcome message for new session
                    this.addMessage('assistant', 'Hello! I\'m AIDA, your ERPNext AI assistant. How can I help you today?');
                }
            } else {
                this.showToast(data.error || 'Connection failed', 'error');
            }
        } catch (error) {
            console.error('Connection error:', error);
            this.showToast('Failed to connect to API server', 'error');
        } finally {
            this.hideLoading();
        }
    }
    

    
    showChatInterface() {
        document.getElementById('welcomeScreen').style.display = 'none';
        document.getElementById('chatMessages').style.display = 'block';
        document.getElementById('chatInputContainer').style.display = 'block';
        this.updateSendButton();
    }
    
    hideChatInterface() {
        document.getElementById('welcomeScreen').style.display = 'flex';
        document.getElementById('chatMessages').style.display = 'none';
        document.getElementById('chatInputContainer').style.display = 'none';
    }
    
    async sendMessage() {
        const input = document.getElementById('messageInput');
        const message = input.value.trim();
        
        if (!message || !this.isConnected) return;
        
        // Add user message to chat
        this.addMessage('user', message);
        input.value = '';
        input.style.height = 'auto';
        this.updateSendButton();
        
        // Show typing indicator
        this.showTypingIndicator();
        
        try {
            const response = await fetch(`${this.apiBaseUrl}/chat`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    session_id: this.sessionId,
                    user_input: message
                })
            });
            
            const data = await response.json();
            
            this.hideTypingIndicator();
            
            if (response.ok) {
                this.addMessage('assistant', data.response);
            } else {
                // Handle session invalidation or server restart
                if (response.status === 404 || response.status === 400) {
                    this.handleSessionInvalidation();
                } else {
                    this.addMessage('assistant', `Error: ${data.error || 'Something went wrong'}`);
                }
            }
        } catch (error) {
            console.error('Chat error:', error);
            this.hideTypingIndicator();
            
            // Check if it's a connection error (server might be restarted)
            if (error.name === 'TypeError' || error.message.includes('fetch')) {
                this.handleSessionInvalidation();
            } else {
                this.addMessage('assistant', 'Sorry, I\'m having trouble connecting. Please try again.');
            }
        }
    }
    
    addMessage(sender, content) {
        const messagesContainer = document.getElementById('chatMessages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${sender}`;
        
        const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        
        // Create message structure
        const avatarDiv = document.createElement('div');
        avatarDiv.className = 'message-avatar';
        avatarDiv.innerHTML = `<i class="fas ${sender === 'user' ? 'fa-user' : 'fa-robot'}"></i>`;
        
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        
        const textDiv = document.createElement('div');
        textDiv.className = 'message-text';
        
        // For assistant messages, allow HTML content (for buttons)
        // For user messages, escape HTML for security
        if (sender === 'assistant') {
            textDiv.innerHTML = this.formatMessage(content);
        } else {
            textDiv.textContent = content;
        }
        
        const timeDiv = document.createElement('div');
        timeDiv.className = 'message-time';
        timeDiv.textContent = time;
        
        contentDiv.appendChild(textDiv);
        contentDiv.appendChild(timeDiv);
        messageDiv.appendChild(avatarDiv);
        messageDiv.appendChild(contentDiv);
        
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    formatMessage(content) {
        // Enhanced formatting that preserves HTML buttons while adding markdown support
        let formatted = content;
        
        // First preserve existing HTML buttons and other HTML elements
        formatted = formatted.replace(/(<button[^>]*>.*?<\/button>)/g, '$1');
        
        // Convert markdown tables to HTML tables
        formatted = this.convertMarkdownTables(formatted);
        
        // Then apply other markdown formatting
        formatted = formatted
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            // Convert markdown links to HTML (but skip if already HTML)
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
            // Convert line breaks (but not inside tables)
            .replace(/\n(?![\s]*\|)/g, '<br>');
            
        return formatted;
    }
    
    convertMarkdownTables(content) {
        // Split content by double line breaks to handle tables separately
        const sections = content.split(/\n\s*\n/);
        
        return sections.map(section => {
            // Check if this section contains a markdown table
            const lines = section.split('\n');
            const tableLines = lines.filter(line => line.trim().includes('|'));
            
            if (tableLines.length >= 2) {
                // This looks like a table
                let tableHtml = '<table class="markdown-table">';
                let isHeader = true;
                
                for (let line of lines) {
                    line = line.trim();
                    if (!line) continue;
                    
                    if (line.includes('|')) {
                        // Skip separator lines (lines with only |, -, and spaces)
                        if (/^[\s\|\-]+$/.test(line)) {
                            isHeader = false;
                            continue;
                        }
                        
                        const cells = line.split('|').map(cell => cell.trim()).filter(cell => cell);
                        const tag = isHeader ? 'th' : 'td';
                        
                        tableHtml += '<tr>';
                        cells.forEach(cell => {
                            tableHtml += `<${tag}>${cell}</${tag}>`;
                        });
                        tableHtml += '</tr>';
                        
                        if (isHeader) isHeader = false;
                    }
                }
                
                tableHtml += '</table>';
                return tableHtml;
            }
            
            return section;
        }).join('<br><br>');
    }
    
    showTypingIndicator() {
        const messagesContainer = document.getElementById('chatMessages');
        const typingDiv = document.createElement('div');
        typingDiv.className = 'message assistant';
        typingDiv.id = 'typing-indicator';
        
        typingDiv.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="typing-indicator">
                <span>AIDA is typing</span>
                <div class="typing-dots">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>
        `;
        
        messagesContainer.appendChild(typingDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
    
    hideTypingIndicator() {
        const typingIndicator = document.getElementById('typing-indicator');
        if (typingIndicator) {
            typingIndicator.remove();
        }
    }
    
    async loadChatHistory() {
        if (!this.sessionId) return;
        
        try {
            const response = await fetch(`${this.apiBaseUrl}/get_chat_history/${this.sessionId}`);
            const data = await response.json();
            
            if (response.ok && data.history && data.history.length > 0) {
                const messagesContainer = document.getElementById('chatMessages');
                messagesContainer.innerHTML = ''; // Clear existing messages
                
                // Load chat history
                data.history.forEach(message => {
                    // Add user message
                    if (message.user_message) {
                        this.addMessage('user', message.user_message);
                    }
                    // Add AI response
                    if (message.ai_response) {
                        this.addMessage('assistant', message.ai_response);
                    }
                });
            }
        } catch (error) {
            console.error('Error loading chat history:', error);
        }
    }
    
    async checkExistingSession() {
        const savedSessionId = localStorage.getItem('aida-session-id');
        if (!savedSessionId) return;
        
        try {
            const response = await fetch(`${this.apiBaseUrl}/session_status/${savedSessionId}`);
            const data = await response.json();
            
            if (response.ok && data.active) {
                this.sessionId = savedSessionId;
                this.updateConnectionStatus(true);
                this.showChatInterface();
                await this.loadChatHistory();
                this.showToast('Session restored from previous visit!', 'success');
            } else {
                // Session expired or invalid, remove from localStorage
                localStorage.removeItem('aida-session-id');
            }
        } catch (error) {
            console.error('Error checking existing session:', error);
            localStorage.removeItem('aida-session-id');
        }
    }
    
    clearChat() {
        const messagesContainer = document.getElementById('chatMessages');
        messagesContainer.innerHTML = '';
        this.showToast('Chat cleared', 'info');
    }
    
    async disconnect() {
        if (this.sessionId) {
            try {
                await fetch(`${this.apiBaseUrl}/clear_session`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        session_id: this.sessionId
                    })
                });
            } catch (error) {
                console.error('Disconnect error:', error);
            }
        }
        
        this.sessionId = null;
        this.updateConnectionStatus(false);
        this.hideChatInterface();
        this.clearChat();
        
        // Clear stored session ID
        localStorage.removeItem('aida-session-id');
        
        this.showToast('Disconnected from AIDA', 'info');
    }
    
    async handleSessionInvalidation() {
        // Clear the current session
        this.sessionId = null;
        this.isConnected = false;
        localStorage.removeItem('aida-session-id');
        
        // Update UI
        this.updateConnectionStatus(false);
        this.hideChatInterface();
        
        // Show informative message
        this.showToast('Server was restarted. Please reconnect to continue.', 'warning');
        
        // Try to preserve chat history by checking if there's any stored session data
        // The chat messages will remain visible until user reconnects
        // This way, users can see their previous conversation even after server restart
        
        // Add a message to the chat explaining what happened
        this.addMessage('assistant', '🔄 The server was restarted and your session has been cleared. Your chat history is preserved and will be restored when you reconnect. Please click "Connect" to establish a new session.');
    }
    
    // Health check to monitor connection
    async checkHealth() {
        try {
            const response = await fetch(`${this.apiBaseUrl}/health`);
            return response.ok;
        } catch (error) {
            return false;
        }
    }
    
    // Start periodic health checks
    startHealthCheck() {
        setInterval(async () => {
            if (this.isConnected) {
                const healthy = await this.checkHealth();
                if (!healthy) {
                    this.showToast('Connection to server lost', 'warning');
                }
            }
        }, 30000); // Check every 30 seconds
    }
}

// Initialize the application when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    const app = new AidaWebUI();
    app.startHealthCheck();
    
    // Add some keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd + K to focus on message input
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            document.getElementById('messageInput').focus();
        }
        
        // Escape to close modals
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal.show').forEach(modal => {
                modal.classList.remove('show');
            });
        }
    });
    
    // Add some helpful console messages
    console.log('%cAIDA Web UI Loaded Successfully! 🤖', 'color: #6366f1; font-size: 16px; font-weight: bold;');
    console.log('Keyboard shortcuts:');
    console.log('- Ctrl/Cmd + K: Focus message input');
    console.log('- Escape: Close modals');
    console.log('- Enter: Send message (Shift+Enter for new line)');
});

// Add some utility functions to window for debugging
window.aidaDebug = {
    clearStorage: () => {
        localStorage.removeItem('aida-config');
        localStorage.removeItem('aida-theme');
        console.log('AIDA storage cleared');
    },
    getConfig: () => {
        return JSON.parse(localStorage.getItem('aida-config') || '{}');
    },
    setTheme: (theme) => {
        localStorage.setItem('aida-theme', theme);
        document.documentElement.setAttribute('data-theme', theme);
        console.log(`Theme set to: ${theme}`);
    }
};

// Service Worker registration for PWA capabilities (optional)
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        // Uncomment if you want to add PWA capabilities
        // navigator.serviceWorker.register('/sw.js')
        //     .then(registration => console.log('SW registered'))
        //     .catch(registrationError => console.log('SW registration failed'));
    });
}