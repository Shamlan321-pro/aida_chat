app_name = "aida_ui_app"
app_title = "Aida AI"
app_publisher = "Aida AI"
app_description = "AI-powered Assistant for Frappe/ERPNext"
app_email = "admin@example.com"
app_license = "MIT"
app_icon_url = "/assets/aida_ui_app/images/logo.svg"
app_icon_title = "Aida AI"
app_icon_route = "/aida"

# Apps
# ------------------
add_to_apps_screen = [
    {
        "name": "aida_ui_app",
        "logo": "/assets/aida_ui_app/images/logo.svg",
        "title": "Aida AI",
        "route": "/aida",
        "has_permission": "aida_ui_app.api.permission.has_app_permission"
    }
]

# Website Routes
website_route_rules = [
    {"from_route": "/aida/<path:app_path>", "to_route": "aida"},
    {"from_route": "/aida", "to_route": "aida"}
]

# Include JS/CSS in web and desk
app_include_js = [
    "/assets/aida_ui_app/js/aida_chat_widget.js"
]

app_include_css = [
    "/assets/aida_ui_app/css/aida_chat_widget.css"
]

web_include_js = [
    "/assets/aida_ui_app/js/aida_chat_widget.js"
]

web_include_css = [
    "/assets/aida_ui_app/css/aida_chat_widget.css"
] 