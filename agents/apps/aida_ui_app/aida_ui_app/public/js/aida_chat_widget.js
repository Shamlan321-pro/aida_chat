frappe.provide('aida_ui_app');

aida_ui_app.ChatWidget = class ChatWidget {
    constructor() {
        this.currentSession = null;
        this.isOpen = false;
        this.init();
    }

    init() {
        this.createWidgetHTML();
        this.attachEventListeners();
        this.initSession();
    }

    createWidgetHTML() {
        const widget = document.createElement('div');
        widget.className = 'aida-chat-widget';
        widget.innerHTML = `
            <button class="chat-toggle">
                <span class="chat-icon">💬</span>
                <span class="close-icon">×</span>
            </button>
            <div class="chat-window">
                <div class="chat-header">
                    <h3>Aida AI Assistant</h3>
                    <button class="minimize-btn">_</button>
                </div>
                <div class="chat-messages"></div>
                <div class="chat-input">
                    <textarea placeholder="Ask me anything..."></textarea>
                    <button class="send-btn">Send</button>
                </div>
            </div>
        `;
        document.body.appendChild(widget);

        // Store references to DOM elements
        this.widget = widget;
        this.chatWindow = widget.querySelector('.chat-window');
        this.messagesContainer = widget.querySelector('.chat-messages');
        this.input = widget.querySelector('textarea');
        this.sendButton = widget.querySelector('.send-btn');
        this.toggleButton = widget.querySelector('.chat-toggle');
    }

    attachEventListeners() {
        // Toggle chat window
        this.toggleButton.addEventListener('click', () => this.toggleChat());
        
        // Send message
        this.sendButton.addEventListener('click', () => this.sendMessage());
        
        // Send on Enter (but allow Shift+Enter for new lines)
        this.input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Minimize button
        this.widget.querySelector('.minimize-btn').addEventListener('click', () => this.toggleChat());
    }

    async initSession() {
        try {
            const response = await fetch('/api/method/aida_ui_app.api.chat.init_session', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Frappe-CSRF-Token': frappe.csrf_token
                }
            });
            
            const data = await response.json();
            if (data.message && data.message.session_id) {
                this.currentSession = data.message.session_id;
                this.addMessage('system', 'Hello! How can I assist you today?');
            }
        } catch (error) {
            console.error('Failed to initialize chat session:', error);
            this.addMessage('error', 'Failed to initialize chat. Please try again.');
        }
    }

    toggleChat() {
        this.isOpen = !this.isOpen;
        this.widget.classList.toggle('open', this.isOpen);
        if (this.isOpen) {
            this.input.focus();
        }
    }

    addMessage(type, content) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${type}`;
        messageDiv.innerHTML = `<p>${content}</p>`;
        this.messagesContainer.appendChild(messageDiv);
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    async sendMessage() {
        const message = this.input.value.trim();
        if (!message) return;

        this.addMessage('user', message);
        this.input.value = '';

        try {
            const response = await fetch('/api/method/aida_ui_app.api.chat.send_message', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Frappe-CSRF-Token': frappe.csrf_token
                },
                body: JSON.stringify({
                    session_id: this.currentSession,
                    message: message
                })
            });
            
            const data = await response.json();
            if (data.message && data.message.response) {
                this.addMessage('assistant', data.message.response);
            }
        } catch (error) {
            console.error('Failed to send message:', error);
            this.addMessage('error', 'Failed to get response. Please try again.');
        }
    }
}

// Initialize the chat widget when the document is ready
frappe.ready(() => {
    if (!window.aidaChatWidget) {
        window.aidaChatWidget = new aida_ui_app.ChatWidget();
    }
}); 