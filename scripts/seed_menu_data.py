"""Seed script for menu, launchpad apps, and agents data."""

import asyncio
import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import async_session
from aldar_middleware.models import Menu, LaunchpadApp, Agent


async def seed_menus():
    """Seed menu data."""
    async with async_session() as session:
        menus_data = [
            {
                "name": "chats",
                "display_name": "Chats",
                "icon": "chat-bubble",
                "route": "/chats",
                "order": 1
            },
            {
                "name": "agents",
                "display_name": "Agents",
                "icon": "gear",
                "route": "/agents",
                "order": 2
            },
            {
                "name": "launchpad",
                "display_name": "Launchpad",
                "icon": "rocket",
                "route": "/launchpad",
                "order": 3
            }
        ]
        
        for menu_data in menus_data:
            exists = (await session.execute(select(Menu).where(Menu.name == menu_data["name"]))).scalars().first()
            if exists:
                continue
            menu = Menu(**menu_data)
            session.add(menu)
        
        await session.commit()
        print("Menus seeded successfully!")


async def seed_launchpad_apps():
    """Seed launchpad apps data."""
    async with async_session() as session:
        apps_data = [
            {
                "app_id": "workspaces",
                "title": "Workspaces",
                "subtitle": "Workspace Management",
                "description": "Manage and organize your workspaces efficiently",
                "tags": ["Productivity", "Workspace"],
                "logo_src": "/images/workspaces_logo.png",
                "category": "trending",
                "url": "#",
                "order": 0
            },
            {
                "app_id": "data-camp",
                "title": "Data Camp",
                "subtitle": "Data Analytics Platform",
                "description": "Explore and analyze data with powerful analytics tools",
                "tags": ["Analytics", "Data"],
                "logo_src": "/images/data_camp_logo.png",
                "category": "trending",
                "url": "#",
                "order": 0
            },
            {
                "app_id": "adq-app",
                "title": "ADQ App",
                "subtitle": "Abu Dhabi Developmental",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Communication", "Project management"],
                "logo_src": "/images/adq_logo.png",
                "category": "trending",
                "url": "https://adq.ae",
                "order": 1
            },
            {
                "app_id": "jira-cloud",
                "title": "Jira Cloud",
                "subtitle": "Atlassian.com",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["IT/Admin", "Project management"],
                "logo_src": "/images/jira_logo.png",
                "category": "trending",
                "url": "https://atlassian.com",
                "order": 2
            },
            {
                "app_id": "sharepoint",
                "title": "SharePoint",
                "subtitle": "Microsoft Corporation",
                "description": "View pages and collaborate with lists",
                "tags": ["Content management", "Productivity"],
                "logo_src": "/images/sharepoint_logo.png",
                "category": "trending",
                "url": "https://sharepoint.com",
                "order": 3
            },
            {
                "app_id": "figma",
                "title": "Figma",
                "subtitle": "Figma",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Communication", "Productivity"],
                "logo_src": "/images/figma_logo.png",
                "category": "trending",
                "url": "https://figma.com",
                "order": 4
            },
            {
                "app_id": "teams",
                "title": "Teams",
                "subtitle": "Microsoft Corporation",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Communication", "Project management"],
                "logo_src": "/images/teams_logo.png",
                "category": "trending",
                "url": "https://teams.microsoft.com",
                "order": 5
            },
            {
                "app_id": "oracle",
                "title": "Oracle",
                "subtitle": "Oracle.com",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Database", "Project management"],
                "logo_src": "/images/oracle_logo.png",
                "category": "trending",
                "url": "https://oracle.com",
                "order": 6
            },
            {
                "app_id": "adobe-acrobat",
                "title": "Adobe Acrobat",
                "subtitle": "Adobe",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Communication", "Productivity"],
                "logo_src": "/images/adobe_logo.png",
                "category": "trending",
                "url": "https://adobe.com",
                "order": 7
            },
            {
                "app_id": "bbc-news",
                "title": "BBC News",
                "subtitle": "BBC",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Productivity", "Project management"],
                "logo_src": "/images/bbc_logo.png",
                "category": "trending",
                "url": "https://bbc.com",
                "order": 8
            },
            {
                "app_id": "copilot",
                "title": "Copilot",
                "subtitle": "Microsoft Corporation",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Content management", "Project management"],
                "logo_src": "/images/copilot_logo.png",
                "category": "trending",
                "url": "https://copilot.microsoft.com",
                "order": 9
            },
            {
                "app_id": "salesforce",
                "title": "Salesforce",
                "subtitle": "Salesforce",
                "description": "Empower your team to track, update, and manage all your project tasks easily...",
                "tags": ["Communication", "CRM"],
                "logo_src": "/images/salesforce_logo.png",
                "category": "trending",
                "url": "https://salesforce.com",
                "order": 10
            },
            # Finance category apps
            {
                "app_id": "quickbooks",
                "title": "QuickBooks",
                "subtitle": "Intuit",
                "description": "Manage your business finances with ease",
                "tags": ["Finance", "Accounting"],
                "logo_src": "/images/quickbooks_logo.png",
                "category": "finance",
                "url": "https://quickbooks.intuit.com",
                "order": 1
            },
            {
                "app_id": "xero",
                "title": "Xero",
                "subtitle": "Xero Limited",
                "description": "Beautiful accounting software for small businesses",
                "tags": ["Finance", "Accounting"],
                "logo_src": "/images/xero_logo.png",
                "category": "finance",
                "url": "https://xero.com",
                "order": 2
            }
        ]
        
        for app_data in apps_data:
            exists = (await session.execute(select(LaunchpadApp).where(LaunchpadApp.app_id == app_data["app_id"])) ).scalars().first()
            if exists:
                continue
            app = LaunchpadApp(**app_data)
            session.add(app)
        
        await session.commit()
        print("Launchpad apps seeded successfully!")


async def seed_agents():
    """Seed agents data."""
    async with async_session() as session:
        agents_data = [
            {
                "agent_id": "aiq-knowledge",
                "title": "AiQ Knowledge",
                "subtitle": "AI Assistant",
                "description": "AiQ Knowledge is an adaptive AI agent powered by advanced machine intelligence. It processes information in real time to provide accurate, actionable outputs.",
                "tags": ["Get", "Put", "Post", "+3 more"],
                "logo_src": "/images/aiq_knowledge_logo.png",
                "category": "all",
                "status": "active",
                "methods": ["Get", "Put", "Post", "Delete", "Patch", "Options"],
                "order": 1
            },
            {
                "agent_id": "airia",
                "title": "Airia",
                "subtitle": "Research Agent",
                "description": "Airia is an adaptive AI agent powered by advanced machine intelligence. It processes information in real time to provide accurate, actionable outputs.",
                "tags": ["Get", "Put", "Post", "+3 more"],
                "logo_src": "/images/airia_logo.png",
                "category": "all",
                "status": "active",
                "methods": ["Get", "Put", "Post", "Delete"],
                "order": 2
            },
            {
                "agent_id": "leave-agent",
                "title": "Leave Agent",
                "subtitle": "HR Assistant",
                "description": "Leave Agent is an adaptive AI agent powered by advanced machine intelligence. It processes information in real time to provide accurate, actionable outputs.",
                "tags": ["Get", "Put", "Post", "+3 more"],
                "logo_src": "/images/leave_agent_logo.png",
                "category": "all",
                "status": "active",
                "methods": ["Get", "Put", "Post"],
                "order": 3
            },
            {
                "agent_id": "deep-research",
                "title": "Deep Research",
                "subtitle": "Research Specialist",
                "description": "Deep Research is an adaptive AI agent powered by advanced machine intelligence. It processes information in real time to provide accurate, actionable outputs.",
                "tags": ["Get", "Put", "Post", "+3 more"],
                "logo_src": "/images/deep_research_logo.png",
                "category": "all",
                "status": "active",
                "methods": ["Get", "Post"],
                "order": 4
            },
            # Procurement category agents
            {
                "agent_id": "procurement-analyst",
                "title": "Procurement Analyst",
                "subtitle": "Procurement Specialist",
                "description": "Specialized agent for procurement analysis and vendor management",
                "tags": ["Analysis", "Vendor Management", "Cost Optimization"],
                "logo_src": "/images/procurement_logo.png",
                "category": "procurement",
                "status": "active",
                "methods": ["Get", "Post", "Put"],
                "order": 1
            },
            {
                "agent_id": "vendor-manager",
                "title": "Vendor Manager",
                "subtitle": "Vendor Relations",
                "description": "Manages vendor relationships and contract negotiations",
                "tags": ["Vendor Relations", "Contracts", "Negotiations"],
                "logo_src": "/images/vendor_logo.png",
                "category": "procurement",
                "status": "active",
                "methods": ["Get", "Post", "Put", "Delete"],
                "order": 2
            },
            # Risk Analysis category agents
            {
                "agent_id": "risk-analyst",
                "title": "Risk Analyst",
                "subtitle": "Risk Assessment",
                "description": "Comprehensive risk analysis and assessment capabilities",
                "tags": ["Risk Assessment", "Compliance", "Monitoring"],
                "logo_src": "/images/risk_logo.png",
                "category": "risk-analysis",
                "status": "active",
                "methods": ["Get", "Post", "Analysis"],
                "order": 1
            },
            {
                "agent_id": "compliance-monitor",
                "title": "Compliance Monitor",
                "subtitle": "Compliance Specialist",
                "description": "Monitors compliance with regulations and policies",
                "tags": ["Compliance", "Monitoring", "Reporting"],
                "logo_src": "/images/compliance_logo.png",
                "category": "risk-analysis",
                "status": "active",
                "methods": ["Get", "Post", "Monitor"],
                "order": 2
            }
        ]
        
        for agent_data in agents_data:
            # Map plain list tags to legacy_tags JSON column and avoid relationship 'tags'
            agent_payload = dict(agent_data)
            if 'tags' in agent_payload:
                agent_payload['legacy_tags'] = agent_payload.pop('tags')
            # Provide required 'name' field from title, and map subtitle to intro
            if 'name' not in agent_payload:
                agent_payload['name'] = agent_payload.get('title')
            if 'intro' not in agent_payload and 'subtitle' in agent_payload:
                agent_payload['intro'] = agent_payload['subtitle']
            exists = (await session.execute(select(Agent).where(Agent.agent_id == agent_payload["agent_id"])) ).scalars().first()
            if exists:
                continue
            agent = Agent(**agent_payload)
            session.add(agent)
        
        await session.commit()
        print("Agents seeded successfully!")


async def main():
    """Main seeding function."""
    print("Starting data seeding...")
    
    try:
        await seed_menus()
        await seed_launchpad_apps()
        await seed_agents()
        print("All data seeded successfully!")
    except Exception as e:
        print(f"Error seeding data: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
