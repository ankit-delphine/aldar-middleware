"""Test script for AGNO API integration."""

import asyncio
import json
from aldar_middleware.orchestration.agno import agno_service


async def test_agno_integration():
    """Test basic AGNO API integration."""
    print("Testing AGNO API Integration...")
    
    try:
        # Test health endpoint
        print("\n1. Testing health endpoint...")
        health_response = await agno_service.get_health()
        print(f"Health response: {json.dumps(health_response, indent=2)}")
        
        # Test config endpoint
        print("\n2. Testing config endpoint...")
        config_response = await agno_service.get_config()
        print(f"Config response: {json.dumps(config_response, indent=2)}")
        
        # Test models endpoint
        print("\n3. Testing models endpoint...")
        models_response = await agno_service.get_models()
        print(f"Models response: {json.dumps(models_response, indent=2)}")
        
        # Test agents endpoint
        print("\n4. Testing agents endpoint...")
        agents_response = await agno_service.get_agents()
        print(f"Agents response: {json.dumps(agents_response, indent=2)}")
        
        # Test teams endpoint
        print("\n5. Testing teams endpoint...")
        teams_response = await agno_service.get_teams()
        print(f"Teams response: {json.dumps(teams_response, indent=2)}")
        
        # Test workflows endpoint
        print("\n6. Testing workflows endpoint...")
        workflows_response = await agno_service.get_workflows()
        print(f"Workflows response: {json.dumps(workflows_response, indent=2)}")
        
        print("\n✅ All AGNO API tests completed successfully!")
        
    except Exception as e:
        print(f"\n❌ AGNO API test failed: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(test_agno_integration())
