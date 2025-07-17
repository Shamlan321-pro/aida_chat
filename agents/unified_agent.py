import os
import json
import logging
import os
import re
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from frappeclient import FrappeClient
import google.generativeai as genai
from langchain.agents import Tool, AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.schema import BaseOutputParser
from agents.lead_creation_agent import LeadCreationAgent
from agents.lead_outreach_agent import LeadOutreachAgent

logger = logging.getLogger(__name__)

class QueryTypeParser(BaseOutputParser):
    """Parse the query to determine intent and extract parameters"""
    
    def parse(self, text: str) -> Dict[str, Any]:
        """Parse the LLM output to extract structured data"""
        try:
            # Look for JSON in the response
            import json
            # Try to find JSON block in the response
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            
            # Fallback parsing
            result = {
                "intent": "unknown",
                "parameters": {}
            }
            
            if any(word in text.lower() for word in ['lead', 'create', 'generate', 'find']):
                result["intent"] = "lead_generation"
            elif any(word in text.lower() for word in ['outreach', 'email', 'campaign', 'contact']):
                result["intent"] = "outreach"
            elif any(word in text.lower() for word in ['stats', 'status', 'report']):
                result["intent"] = "stats"
            
            return result
        except Exception as e:
            logger.error(f"Error parsing query: {e}")
            return {"intent": "unknown", "parameters": {}}

class UnifiedAgent:
    def __init__(self, erpnext_url: str, erpnext_username: str, erpnext_password: str, 
                 company_profile: Optional[Dict[str, Any]] = None, 
                 email_templates: Optional[List[Dict[str, Any]]] = None):
        """Initialize the agent with dynamic ERPNext credentials and company configuration."""
        # Initialize Frappe client
        self.client = FrappeClient(
            erpnext_url,
            erpnext_username,
            erpnext_password
        )
        
        # Store company profile and email templates
        self.company_profile = company_profile or {
            "name": "Your Company",
            "description": "A leading technology company",
            "industry": "Technology",
            "offers": ["Software Solutions", "Consulting"],
            "value_proposition": "We help businesses grow through technology"
        }
        
        self.email_templates = email_templates or [
            {
                "name": "Meeting Request",
                "type": "meeting",
                "subject": "Quick chat about {company_name}?",
                "body": "Hi {contact_name},\n\nI noticed {company_name} is in the {industry} space. We help companies like yours {value_proposition}.\n\nWould you be open to a brief 15-minute call to discuss how we can help {company_name}?\n\nBest regards,\n{sender_name}",
                "tone": "professional"
            },
            {
                "name": "Product Demo",
                "type": "product",
                "subject": "See how {our_company} can help {company_name}",
                "body": "Hello {contact_name},\n\nI hope this email finds you well. I'm reaching out because {company_name} seems like a perfect fit for our {main_service}.\n\n{value_proposition}\n\nWould you be interested in a quick demo to see how we can help {company_name}?\n\nBest,\n{sender_name}",
                "tone": "friendly"
            }
        ]
        
        # Initialize LangChain LLM
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=os.getenv('GOOGLE_API_KEY'),
            temperature=0.7
        )
        
        # Initialize lead creation agent
        self.lead_agent = LeadCreationAgent(
            gmaps_api_key=os.getenv('GMAPS_API_KEY'),
            erpnext_url=erpnext_url,
            erpnext_username=erpnext_username,
            erpnext_password=erpnext_password,
            gemini_api_key=os.getenv('GOOGLE_API_KEY')
        )
        self.outreach_agent = LeadOutreachAgent(
            url=erpnext_url,
            username=erpnext_username,
            password=erpnext_password
        )
        
        # Initialize parser
        self.parser = QueryTypeParser()
        
        # Setup tools and agent
        self._setup_agent()
    
    def _setup_agent(self):
        """Setup LangChain agent with tools"""
        tools = [
            Tool(
                name="analyze_query",
                description="Analyze user query to determine intent and extract parameters",
                func=self._analyze_query
            ),
            Tool(
                name="create_leads",
                description="Create new leads using lead generation",
                func=self._create_leads_tool
            ),
            Tool(
                name="get_company_description",
                description="Get company description from Wiki",
                func=self._get_company_description
            ),
            Tool(
                name="get_leads_for_outreach",
                description="Get leads for outreach campaign with filters",
                func=self._get_leads_for_outreach_tool
            ),
            Tool(
                name="generate_personalized_email",
                description="Generate personalized email for a lead",
                func=self._generate_email_tool
            ),
            Tool(
                name="send_email",
                description="Send email using Frappe client",
                func=self._send_email_tool
            ),
            Tool(
                name="get_campaign_stats",
                description="Get campaign statistics",
                func=self._get_stats_tool
            )
        ]
        
        template = """
You are an intelligent CRM agent that helps with lead generation and outreach campaigns.

You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

When a user makes a request:
1. First analyze the query to understand the intent
2. Based on the intent, use the appropriate tools
3. For lead generation: use create_leads tool
4. For outreach: get company description, get leads with filters, generate emails, and send them
5. For stats: use get_campaign_stats

Always provide detailed progress updates and show email content for outreach campaigns.

Question: {input}
{agent_scratchpad}"""
        
        prompt = PromptTemplate.from_template(template)
        self.agent = create_react_agent(self.llm, tools, prompt)
        self.agent_executor = AgentExecutor(
            agent=self.agent,
            tools=tools,
            verbose=True,
            handle_parsing_errors="Check your output and make sure it conforms to the format instructions!",
            max_iterations=10,
            early_stopping_method="generate"
        )
    
    def _analyze_query(self, query: str) -> str:
        """Analyze user query to determine intent and extract parameters"""
        analysis_prompt = f"""
        Analyze this user query and extract the intent and parameters:
        
        Query: "{query}"
        
        Extract specific parameters:
        - Count: any numbers mentioned (default to 10 if none found)
        - Status: words like "open", "interested", "qualified", "lead", "replied", "opportunity", "quotation", "lost"
        - Industry: business types like "software", "marketing", "healthcare", "finance", "tech"
        - Time: words like "recent", "last X days", "new"
        
        Return as JSON (use null for missing values, not "None"):
        {{
            "intent": "lead_generation|outreach|stats",
            "parameters": {{
                "count": 10,
                "business_type": null,
                "location": null,
                "filters": {{
                    "industry": null,
                    "status": null,
                    "size": null,
                    "recent": null
                }}
            }}
        }}
        """
        
        try:
            response = self.llm.invoke(analysis_prompt)
            result = self.parser.parse(response.content)
            
            # Ensure count is always a number
            if 'parameters' in result:
                if 'count' not in result['parameters'] or result['parameters']['count'] is None:
                    result['parameters']['count'] = 10
                elif isinstance(result['parameters']['count'], str):
                    try:
                        result['parameters']['count'] = int(result['parameters']['count'])
                    except:
                        result['parameters']['count'] = 10
            
            # Clean up None values that might come as strings
            if 'parameters' in result and 'filters' in result['parameters']:
                filters = result['parameters']['filters']
                for key, value in filters.items():
                    if value in ['None', 'none', 'null', '']:
                        filters[key] = None
            
            return json.dumps(result)
        except Exception as e:
            logger.error(f"Error analyzing query: {e}")
            return json.dumps({
                "intent": "lead_generation",
                "parameters": {
                    "count": 10,
                    "business_type": None,
                    "location": None,
                    "filters": {
                        "industry": None,
                        "status": None,
                        "size": None,
                        "recent": None
                    }
                }
            })

    def process_query(self, query: str) -> Dict[str, Any]:
        """Process a single query using the agent executor."""
        try:
            response = self.agent_executor.invoke({"input": query})
            return response
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            return {"error": str(e)}

    def _create_leads_tool(self, query: str) -> str:
        """Tool to create leads."""
        try:
            # Handle both JSON and plain text input
            try:
                params = json.loads(query)
            except (json.JSONDecodeError, TypeError):
                # If not JSON, treat as plain text and extract parameters
                params = self._extract_params_from_text(query)
            
            result = self.lead_agent.create_leads(
                business_type=params.get('business_type', 'company'),
                location=params.get('location', 'USA'),
                count=params.get('count', 5),
                user_input=query # Pass the original query for more context
            )
            return json.dumps(result)
        except Exception as e:
            return f"Error creating leads: {e}"
    
    def _extract_params_from_text(self, text: str) -> Dict:
        """Extract parameters from plain text query"""
        params = {
            'business_type': 'company',
            'location': 'USA',
            'count': 5
        }
        
        # Extract count
        count_match = re.search(r'(\d+)', text)
        if count_match:
            params['count'] = int(count_match.group(1))
        
        # Extract business type
        text_lower = text.lower()
        if 'startup' in text_lower:
            params['business_type'] = 'startup'
        elif 'software' in text_lower:
            params['business_type'] = 'software company'
        elif 'marketing' in text_lower:
            params['business_type'] = 'marketing agency'
        elif 'healthcare' in text_lower:
            params['business_type'] = 'healthcare company'
        elif 'tech' in text_lower:
            params['business_type'] = 'tech company'
        
        # Extract location
        location_match = re.search(r'in\s+([^,\n]+)', text, re.IGNORECASE)
        if location_match:
            params['location'] = location_match.group(1).strip()
        
        return params

    def _get_company_description(self, query: str) -> str:
        """Tool to get company description from the stored profile."""
        return json.dumps({
            "name": self.company_profile["name"],
            "description": self.company_profile["description"],
            "industry": self.company_profile["industry"],
            "offers": self.company_profile["offers"],
            "value_proposition": self.company_profile["value_proposition"],
            "website": self.company_profile.get("website", "")
        })

    def _get_leads_for_outreach_tool(self, query: str) -> List[Dict]:
        """Tool to get leads for an outreach campaign."""
        try:
            params = json.loads(query)
            return self.outreach_agent.get_leads_for_outreach(filters=params.get('filters'))
        except Exception as e:
            logger.error(f"Error getting leads for outreach: {e}")
            return []

    def _generate_email_tool(self, query: str) -> str:
        """Tool to generate a personalized email using stored templates."""
        try:
            params = json.loads(query)
            lead = params.get('lead')
            template_type = params.get('template_type', 'meeting')
            
            # Find the appropriate template
            selected_template = None
            for template in self.email_templates:
                if template['type'] == template_type:
                    selected_template = template
                    break
            
            if not selected_template:
                selected_template = self.email_templates[0]  # Use first template as fallback
            
            # Generate personalized email using the template and company profile
            email_content = self._personalize_email_template(lead, selected_template)
            return json.dumps(email_content)
        except Exception as e:
            return f"Error generating email: {e}"
    
    def _personalize_email_template(self, lead: Dict[str, Any], template: Dict[str, Any]) -> Dict[str, Any]:
        """Personalize an email template with lead and company information."""
        try:
            # Extract lead information
            lead_name = lead.get('lead_name', lead.get('company_name', 'there'))
            contact_name = lead.get('contact_display', lead.get('lead_name', 'there'))
            lead_industry = lead.get('industry', 'your industry')
            
            # Prepare template variables
            template_vars = {
                'company_name': lead_name,
                'contact_name': contact_name.split()[0] if contact_name else 'there',  # First name only
                'industry': lead_industry,
                'our_company': self.company_profile['name'],
                'value_proposition': self.company_profile['value_proposition'],
                'main_service': ', '.join(self.company_profile['offers'][:2]),  # First 2 services
                'sender_name': 'Best regards'  # Can be customized
            }
            
            # Format subject and body
            subject = template['subject'].format(**template_vars)
            body = template['body'].format(**template_vars)
            
            return {
                'subject': subject,
                'body': body,
                'template_type': template['type'],
                'tone': template['tone']
            }
        except Exception as e:
            logger.error(f"Error personalizing email template: {e}")
            return {
                'subject': f"Partnership opportunity with {self.company_profile['name']}",
                'body': f"Hi there,\n\nI'd love to discuss how {self.company_profile['name']} can help your business.\n\nBest regards",
                'template_type': 'generic',
                'tone': 'professional'
            }

    def _send_email_tool(self, query: str) -> str:
        """Tool to send an email."""
        try:
            params = json.loads(query)
            email_data = params.get('email')
            recipient = params.get('recipient')
            
            self.client.sendmail(
                recipients=[recipient],
                subject=email_data.get('subject'),
                message=email_data.get('body'),
                now=True
            )
            return "Email sent successfully."
        except Exception as e:
            return f"Error sending email: {e}"

    def _get_stats_tool(self, query: str) -> str:
        """Tool to get campaign statistics."""
        # This would query ERPNext for stats
        return json.dumps({
            'leads_generated': 100,
            'emails_sent': 50,
            'open_rate': 0.2
        })

    def run_outreach_campaign(self, query: str) -> Dict:
        """Run a complete outreach campaign"""
        try:
            print(f"\n📧 Starting outreach campaign based on: {query}")
            
            # Step 1: Analyze query
            analysis = json.loads(self._analyze_query(query))
            filters = analysis.get('parameters', {}).get('filters', {})
            count = analysis.get('parameters', {}).get('count', 10)
            
            # Clean filters - remove None values
            clean_filters = {k: v for k, v in filters.items() if v is not None}
            
            print(f"📊 Campaign parameters: {clean_filters}, Count: {count}")
            
            # Step 2: Use stored company profile instead of hardcoded description
            company_profile = self.company_profile
            print(f"🏢 Using company profile: {company_profile['name']}")
            print(f"   Industry: {company_profile['industry']}")
            print(f"   Services: {', '.join(company_profile['offers'])}")
            
            # Step 3: Get filtered leads with detailed debugging
            filter_params = {**clean_filters, 'count': count}
            leads = self._get_filtered_leads(filter_params)
            
            if not leads:
                print("\n❌ No qualifying leads found.")
                return {"success": False, "message": "No qualifying leads found with the specified criteria"}
            
            print(f"\n✅ Found {len(leads)} qualifying leads for outreach")
            
            # Show lead details for verification
            for i, lead in enumerate(leads[:3], 1):  # Show first 3
                email_status = "✅" if lead.get('email_id') else "❌"
                print(f"   {i}. {lead.get('lead_name', 'Unknown')} ({lead.get('status', 'No status')}) {email_status} {lead.get('email_id', 'No email')}")
            
            if len(leads) > 3:
                print(f"   ... and {len(leads) - 3} more leads")
            
            # Step 4: Generate and send emails
            sent_count = 0
            failed_count = 0
            skipped_count = 0
            
            for i, lead in enumerate(leads, 1):
                try:
                    print(f"\n📧 Processing lead {i}/{len(leads)}: {lead.get('lead_name', 'Unknown')}")
                    print(f"   Company: {lead.get('company_name', 'N/A')}")
                    print(f"   Email: {lead.get('email_id', 'N/A')}")
                    print(f"   Status: {lead.get('status', 'N/A')}")
                    
                    # Skip leads without email
                    if not lead.get('email_id'):
                        skipped_count += 1
                        print(f"⏭️  Skipped - No email address")
                        continue
                    
                    # Generate email using stored company profile
                    email_content = self._generate_personalized_email(lead, "")  # Empty string since we use stored profile
                    
                    print(f"✍️  Generated email:")
                    print(f"   Subject: {email_content['subject']}")
                    print(f"   Body preview: {email_content['body'][:100]}...")
                    
                    # Send email
                    email_data = {
                        'to_email': lead['email_id'],
                        'subject': email_content['subject'],
                        'body': email_content['body']
                    }
                    
                    send_result = self._send_email_tool(json.dumps(email_data))
                    
                    if "successfully" in send_result:
                        sent_count += 1
                        print(f"✅ Email sent successfully")
                    else:
                        failed_count += 1
                        print(f"❌ Email failed: {send_result}")
                    
                    # Small delay between emails
                    import time
                    time.sleep(1)
                    
                except Exception as e:
                    failed_count += 1
                    print(f"❌ Error processing lead: {str(e)}")
            
            # Final statistics
            final_statuses = [lead.get('status', 'Unknown') for lead in leads]
            status_counts = {}
            for status in final_statuses:
                status_counts[status] = status_counts.get(status, 0) + 1
            
            return {
                "success": True,
                "campaign_stats": {
                    "total_leads": len(leads),
                    "emails_sent": sent_count,
                    "emails_failed": failed_count,
                    "emails_skipped": skipped_count,
                    "filters_applied": clean_filters,
                    "lead_statuses": status_counts
                }
            }
            
        except Exception as e:
            logger.error(f"Campaign error: {e}")
            return {"success": False, "error": str(e)}

    def _get_filtered_leads(self, filters: Dict) -> List[Dict]:
        """Get leads with dynamic filtering"""
        try:
            # First, let's debug what leads and statuses exist
            print("\n🔍 DEBUG: Checking Lead data structure...")
            
            # Get all leads without filters first to see what's available
            all_leads_debug = self.client.get_list('Lead', 
                fields=['name', 'lead_name', 'company_name', 'email_id', 'industry', 'creation', 'status'],
                limit_page_length=50
            )
            
            print(f"📊 Total leads in system: {len(all_leads_debug)}")
            
            # Show all available statuses
            statuses_in_system = set()
            leads_with_email = 0
            
            for lead in all_leads_debug:
                if lead.get('status'):
                    statuses_in_system.add(lead.get('status'))
                if lead.get('email_id'):
                    leads_with_email += 1
            
            print(f"📋 Available statuses in system: {sorted(list(statuses_in_system))}")
            print(f"📧 Leads with email addresses: {leads_with_email}")
            
            # Show sample leads by status
            for status in sorted(list(statuses_in_system)):
                status_leads = [l for l in all_leads_debug if l.get('status') == status]
                email_count = len([l for l in status_leads if l.get('email_id')])
                print(f"   - {status}: {len(status_leads)} total, {email_count} with email")
            
            # Now apply the actual filtering
            target_status = filters.get('status')
            if target_status:
                print(f"\n🎯 Looking for leads with status: '{target_status}'")
                
                # Try exact match first
                exact_match_leads = [l for l in all_leads_debug if l.get('status', '').lower() == target_status.lower()]
                print(f"   Exact match (case-insensitive): {len(exact_match_leads)}")
                
                # Use exact match leads
                if exact_match_leads:
                    filtered_leads = exact_match_leads
                else:
                    print(f"❌ No leads found with status '{target_status}' in any case")
                    filtered_leads = []
            else:
                # No status filter, use all leads
                filtered_leads = all_leads_debug
            
            print(f"📊 After status filtering: {len(filtered_leads)} leads")
            
            # Check email situation before filtering
            leads_with_email = [l for l in filtered_leads if l.get('email_id')]
            leads_without_email = [l for l in filtered_leads if not l.get('email_id')]
            
            print(f"📧 Email analysis:")
            print(f"   - Leads with email: {len(leads_with_email)}")
            print(f"   - Leads without email: {len(leads_without_email)}")
            
            # If no leads have email, offer to show leads without email or suggest adding emails
            if len(leads_with_email) == 0 and len(leads_without_email) > 0:
                print(f"\n⚠️  WARNING: Found {len(leads_without_email)} leads with status '{target_status}' but none have email addresses!")
                print("📋 Leads without email addresses:")
                for i, lead in enumerate(leads_without_email[:5], 1):  # Show first 5
                    print(f"   {i}. {lead.get('lead_name', 'Unknown')} - {lead.get('company_name', 'No company')}")
                
                if len(leads_without_email) > 5:
                    print(f"   ... and {len(leads_without_email) - 5} more")
                
                print("\n💡 Suggestions:")
                print("   1. Add email addresses to these leads in ERPNext")
                print("   2. Use leads with 'Interested' status (which have emails)")
                print("   3. Run campaign anyway without emails (for testing)")
                
                # Ask user what to do
                choice = input("\nWould you like to:\n1. Skip leads without emails (current behavior)\n2. Include leads without emails (emails won't be sent)\n3. Cancel campaign\nEnter choice (1/2/3): ").strip()
                
                if choice == "2":
                    print("📧 Including leads without email addresses (emails will be skipped)")
                    email_filtered_leads = filtered_leads  # Include all leads
                elif choice == "3":
                    print("❌ Campaign cancelled")
                    return []
                else:
                    print("📧 Using only leads with email addresses")
                    email_filtered_leads = leads_with_email
            else:
                # Normal filtering - only leads with emails
                email_filtered_leads = leads_with_email
            
            print(f"📧 After email filter: {len(email_filtered_leads)} leads")
            
            # Apply industry filter if specified
            if filters.get('industry'):
                industry = filters['industry']
                if industry is not None:  # Add null check
                    before_count = len(email_filtered_leads)
                    email_filtered_leads = [l for l in email_filtered_leads 
                                         if l.get('industry') and industry.lower() in l.get('industry', '').lower()]
                    print(f"🏭 After industry filter '{industry}': {len(email_filtered_leads)} leads (was {before_count})")
            
            # Apply recent filter if specified
            if filters.get('recent'):
                try:
                    days = int(filters['recent'])
                    cutoff_date = datetime.now() - timedelta(days=days)
                    before_count = len(email_filtered_leads)
                    
                    recent_leads = []
                    for lead in email_filtered_leads:
                        try:
                            if lead.get('creation'):  # Add null check
                                creation_date = datetime.strptime(lead['creation'][:19], '%Y-%m-%d %H:%M:%S')
                                if creation_date >= cutoff_date:
                                    recent_leads.append(lead)
                        except:
                            # If date parsing fails, include the lead
                            recent_leads.append(lead)
                    
                    email_filtered_leads = recent_leads
                    print(f"📅 After recent filter (last {days} days): {len(email_filtered_leads)} leads (was {before_count})")
                except Exception as e:
                    print(f"⚠️ Error applying recent filter: {e}")
            
            # Add research data to each lead
            final_leads = []
            for lead in email_filtered_leads:
                try:
                    lead_data = self._get_lead_research_data(lead['name'])
                    lead.update(lead_data)
                except Exception as e:
                    print(f"⚠️ Error getting research data for lead {lead.get('name', 'Unknown')}: {e}")
                    lead.update({'research_data': 'No research data available', 'notes_count': 0})
                final_leads.append(lead)
            
            # Apply count limit
            count = filters.get('count')
            if count and count > 0:
                final_leads = final_leads[:count]
                print(f"🔢 After count limit ({count}): {len(final_leads)} leads")
            
            return final_leads
            
        except Exception as e:
            logger.error(f"Error filtering leads: {e}")
            print(f"❌ Error in lead filtering: {e}")
            return []

    def _passes_additional_filters(self, lead: Dict, filters: Dict) -> bool:
        """Check if lead passes additional filters that can't be done at DB level"""
        
        # Recent leads filter (last X days)
        if 'recent' in filters:
            try:
                days = int(filters['recent'])
                creation_date = datetime.strptime(lead['creation'][:10], '%Y-%m-%d')
                cutoff_date = datetime.now() - timedelta(days=days)
                if creation_date < cutoff_date:
                    return False
            except Exception as e:
                logger.error(f"Error parsing recent filter: {e}")
                pass
        
        # Size filter (based on company description keywords)
        if 'size' in filters:
            # This would need more sophisticated logic based on research data
            pass
        
        return True

    def _passes_filters(self, lead: Dict, filters: Dict) -> bool:
        """Check if lead passes the filters (legacy method - kept for compatibility)"""
        # Status filter
        if 'status' in filters:
            lead_status = lead.get('status', '').lower()
            filter_status = filters['status'].lower()
            if filter_status not in lead_status:
                return False
        
        # Industry filter
        if 'industry' in filters:
            lead_industry = lead.get('industry', '').lower()
            if filters['industry'].lower() not in lead_industry:
                return False
        
        # Recent leads filter (last X days)
        if 'recent' in filters:
            try:
                days = int(filters['recent'])
                creation_date = datetime.strptime(lead['creation'][:10], '%Y-%m-%d')
                cutoff_date = datetime.now() - timedelta(days=days)
                if creation_date < cutoff_date:
                    return False
            except:
                pass
        
        return True
    
    def _get_lead_research_data(self, lead_name: str) -> Dict:
        """Get research data/notes for a lead"""
        try:
            # Get comments/notes for the lead
            comments = self.client.get_list('Comment',
                filters={'reference_doctype': 'Lead', 'reference_name': lead_name},
                fields=['content', 'creation']
            )
            
            research_notes = []
            for comment in comments:
                if comment.get('content'):
                    research_notes.append(comment['content'])
            
            return {
                'research_data': ' '.join(research_notes) if research_notes else 'No research data available',
                'notes_count': len(research_notes)
            }
        except Exception as e:
            logger.error(f"Error getting lead research: {e}")
            return {'research_data': 'No research data available', 'notes_count': 0}
    
    def _generate_email_tool(self, lead_and_company_json: str) -> str:
        """Generate personalized email for a lead"""
        try:
            data = json.loads(lead_and_company_json)
            lead = data['lead']
            company_desc = data['company_description']
            
            email_content = self._generate_personalized_email(lead, company_desc)
            return json.dumps(email_content)
        except Exception as e:
            return f"Error generating email: {str(e)}"
    
    def _generate_personalized_email(self, lead: Dict, company_description: str) -> Dict:
        """Generate personalized email using Gemini"""
        # Use the stored company profile instead of the passed description
        company_profile = self.company_profile
        
        prompt = f"""
        Generate a personalized outreach email based on the following information:

        MY COMPANY:
        Name: {company_profile['name']}
        Industry: {company_profile['industry']}
        Description: {company_profile['description']}
        Services/Offers: {', '.join(company_profile['offers'])}
        Value Proposition: {company_profile['value_proposition']}
        Website: {company_profile.get('website', 'N/A')}

        LEAD INFORMATION:
        - Name: {lead.get('lead_name', 'N/A')}
        - Company: {lead.get('company_name', 'N/A')}
        - Email: {lead.get('email_id', 'N/A')}
        - Industry: {lead.get('industry', 'N/A')}
        - Research Notes: {lead.get('research_data', 'N/A')}

        GUIDELINES:
        - Keep under 150 words
        - Professional yet personable tone
        - Reference their company/industry specifically
        - Use our actual company name: {company_profile['name']}
        - Highlight our specific services: {', '.join(company_profile['offers'])}
        - Emphasize our value proposition: {company_profile['value_proposition']}
        - Clear value proposition
        - End with soft call-to-action for 15-minute call
        - Use research data to personalize

        Format as:
        SUBJECT: [subject line]
        BODY: [email body]
        """
        
        try:
            response = self.llm.invoke(prompt)
            email_text = response.content
            
            # Parse subject and body
            subject = ""
            body = ""
            
            lines = email_text.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('SUBJECT:'):
                    subject = line.replace('SUBJECT:', '').strip()
                elif line.startswith('BODY:'):
                    body = '\n'.join(lines[i:]).replace('BODY:', '').strip()
                    break
            
            return {
                'subject': subject or f"Partnership opportunity with {company_profile['name']}",
                'body': body or f"I'd love to discuss how {company_profile['name']} can help your business grow. Would you be available for a brief call?"
            }
            
        except Exception as e:
            logger.error(f"Error generating email: {e}")
            return {
                'subject': f"Partnership opportunity with {company_profile['name']}",
                'body': f"I'd love to discuss how {company_profile['name']} can help your business grow. Would you be available for a brief call?"
            }
    
    def _send_email_tool(self, email_data_json: str) -> str:
        """Send email using Frappe client"""
        try:
            email_data = json.loads(email_data_json)
            
            # Send using the exact same method as send_mail.py
            frappe_email_data = {
                "recipients": email_data['to_email'],
                "subject": email_data['subject'],
                "content": self._text_to_html(email_data['body']),
                "as_html": True,
                "communication_medium": "Email",
                "send_email": 1
            }
            
            response = self.client.post_api("frappe.core.doctype.communication.email.make", frappe_email_data)
            
            logger.info(f"Email sent to {email_data['to_email']}")
            return f"Email sent successfully to {email_data['to_email']}"
            
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return f"Failed to send email: {str(e)}"
    
    def _text_to_html(self, text: str) -> str:
        """Convert plain text to HTML"""
        html_text = text.replace('\n\n', '</p><p>').replace('\n', '<br>')
        return f"<p>{html_text}</p>"
    
    def _get_stats_tool(self, filters: str = "{}") -> str:
        """Get campaign statistics"""
        try:
            all_leads = self.client.get_list('Lead', 
                fields=['name', 'status', 'industry', 'creation'],
                limit_page_length=1000
            )
            
            stats = {
                'total_leads': len(all_leads),
                'leads_with_email': 0,
                'by_status': {},
                'by_industry': {},
                'recent_leads': 0
            }
            
            # Count recent leads (last 7 days)
            cutoff_date = datetime.now() - timedelta(days=7)
            
            for lead in all_leads:
                # Count by status
                status = lead.get('status', 'Unknown')
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                
                # Count by industry
                industry = lead.get('industry', 'Unknown')
                stats['by_industry'][industry] = stats['by_industry'].get(industry, 0) + 1
                
                # Count recent leads
                try:
                    creation_date = datetime.strptime(lead['creation'][:10], '%Y-%m-%d')
                    if creation_date >= cutoff_date:
                        stats['recent_leads'] += 1
                except:
                    pass
            
            return json.dumps(stats, indent=2)
            
        except Exception as e:
            return f"Error getting stats: {str(e)}"
    
    def process_query(self, query: str) -> Dict:
        """Process user query using the LangChain agent with fallback"""
        try:
            print(f"\n🤖 Processing query: {query}")
            
            # Try direct processing first for lead generation
            if any(word in query.lower() for word in ['create', 'generate', 'find']) and 'lead' in query.lower():
                return self._direct_lead_creation(query)
            
            # Use LangChain agent for complex queries
            result = self.agent_executor.invoke({"input": query})
            return {
                "success": True,
                "result": result.get("output", "Task completed"),
                "query": query
            }
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            # Fallback to direct processing
            if 'lead' in query.lower():
                return self._direct_lead_creation(query)
            return {
                "success": False,
                "error": str(e),
                "query": query
            }
    
    def _direct_lead_creation(self, query: str) -> Dict:
        """Direct lead creation bypassing LangChain agent"""
        try:
            print("\n🔄 Using direct lead creation method...")
            
            # Parse query for parameters
            count = 5
            business_type = "software companies"
            location = "San Francisco"
            
            # Extract count
            count_match = re.search(r'(\d+)', query)
            if count_match:
                count = int(count_match.group(1))
            
            # Extract business type
            if 'software' in query.lower():
                business_type = "software companies"
            elif 'marketing' in query.lower():
                business_type = "marketing agencies"
            elif 'healthcare' in query.lower():
                business_type = "healthcare companies"
            
            # Extract location
            location_match = re.search(r'in\s+([^,\n]+)', query, re.IGNORECASE)
            if location_match:
                location = location_match.group(1).strip()
            
            print(f"📊 Parameters: {count} {business_type} in {location}")
            
            # Create leads using lead agent
            result = self.lead_agent.create_leads(
                business_type=business_type,
                location=location,
                count=count,
                generate_personalization=True
            )
            
            return {
                "success": True,
                "result": f"Successfully created {count} leads for {business_type} in {location}",
                "details": result,
                "query": query
            }
            
        except Exception as e:
            logger.error(f"Direct lead creation failed: {e}")
            return {
                "success": False,
                "error": f"Direct lead creation failed: {str(e)}",
                "query": query
            }
