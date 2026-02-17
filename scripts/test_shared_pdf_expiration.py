#!/usr/bin/env python3
"""
Test script for shared_pdf expiration functionality.

This script helps test the SAS URL expiration without waiting for the full duration.
You can set a short expiration time (e.g., 1 minute) for quick testing.

Usage:
    # Test with 1 minute expiration (quick test)
    ALDAR_SHARED_PDF_SAS_TOKEN_EXPIRY_HOURS=0.016 python scripts/test_shared_pdf_expiration.py

    # Test with default 1 hour expiration
    python scripts/test_shared_pdf_expiration.py

    # Test with 5 minutes expiration
    ALDAR_SHARED_PDF_SAS_TOKEN_EXPIRY_HOURS=0.083 python scripts/test_shared_pdf_expiration.py
"""

import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import requests
except ImportError:
    print("❌ Error: 'requests' library not found. Install it with: pip install requests")
    sys.exit(1)


def parse_sas_token_expiry(sas_url: str) -> datetime:
    """
    Parse the expiry time from a SAS token URL.
    
    Args:
        sas_url: The full SAS URL with query parameters
        
    Returns:
        datetime object representing when the token expires
    """
    try:
        # Parse the URL to get query parameters
        parsed = urllib.parse.urlparse(sas_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        # The expiry time is in the 'se' (signed expiry) parameter
        if 'se' in params:
            expiry_str = params['se'][0]
            # Try to parse as ISO 8601 format first (Azure SAS tokens use this)
            try:
                # Remove 'Z' and parse as UTC
                expiry_str_clean = expiry_str.rstrip('Z')
                expiry_time = datetime.strptime(expiry_str_clean, '%Y-%m-%dT%H:%M:%S')
                return expiry_time
            except ValueError:
                # Fallback to Unix timestamp
                try:
                    expiry_timestamp = int(expiry_str)
                    return datetime.fromtimestamp(expiry_timestamp)
                except (ValueError, TypeError):
                    raise ValueError(f"Could not parse expiry time: {expiry_str}")
        else:
            raise ValueError("No 'se' (signed expiry) parameter found in SAS token")
    except Exception as e:
        raise ValueError(f"Failed to parse SAS token expiry: {str(e)}")


def test_shared_pdf_upload(base_url: str, token: str, test_file_path: str = None):
    """
    Test the shared_pdf upload and verify expiration time.
    
    Args:
        base_url: Base URL of the API (e.g., http://localhost:8080)
        token: Bearer token for authentication
        test_file_path: Path to a test PDF file (optional, will create a dummy file if not provided)
    """
    print("=" * 70)
    print("🧪 Testing Shared PDF Expiration")
    print("=" * 70)
    print()
    
    # Create a test PDF file if not provided
    if not test_file_path:
        test_file_path = "/tmp/test_shared_pdf.pdf"
        # Create a minimal valid PDF
        pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\ntrailer\n<< /Size 1 /Root 1 0 R >>\nstartxref\n9\n%%EOF"
        with open(test_file_path, "wb") as f:
            f.write(pdf_content)
        print(f"📄 Created test PDF file: {test_file_path}")
    else:
        if not os.path.exists(test_file_path):
            print(f"❌ Error: Test file not found: {test_file_path}")
            return
        print(f"📄 Using test PDF file: {test_file_path}")
    
    print()
    
    # Step 1: Upload the file with purpose=shared_pdf
    print("📤 Step 1: Uploading file with purpose=shared_pdf...")
    upload_url = f"{base_url}/api/v1/attachments/upload"
    
    headers = {
        "Authorization": f"Bearer {token}"
    }
    
    with open(test_file_path, "rb") as f:
        files = {"file": (os.path.basename(test_file_path), f, "application/pdf")}
        params = {
            "entity_type": "chat",
            "purpose": "shared_pdf"
        }
        
        try:
            response = requests.post(upload_url, headers=headers, files=files, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            print(f"✅ Upload successful!")
            print(f"   Attachment ID: {data.get('attachment_id')}")
            print(f"   File Name: {data.get('file_name')}")
            print(f"   Blob URL: {data.get('blob_url')[:80]}...")
            print()
            
            blob_url = data.get('blob_url')
            if not blob_url:
                print("❌ Error: No blob_url in response")
                return
            
            # Step 2: Parse and display expiration time
            print("⏰ Step 2: Analyzing SAS token expiration...")
            try:
                expiry_time = parse_sas_token_expiry(blob_url)
                now = datetime.utcnow()
                time_until_expiry = expiry_time - now
                
                print(f"✅ SAS token parsed successfully!")
                print(f"   Current Time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"   Expiry Time (UTC):  {expiry_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"   Time Until Expiry:  {time_until_expiry}")
                print(f"   Hours Until Expiry: {time_until_expiry.total_seconds() / 3600:.4f}")
                print()
                
                # Step 3: Test URL access
                print("🔍 Step 3: Testing URL access...")
                try:
                    access_response = requests.head(blob_url, timeout=10)
                    if access_response.status_code == 200:
                        print(f"✅ URL is accessible (Status: {access_response.status_code})")
                        print(f"   Content-Type: {access_response.headers.get('Content-Type', 'N/A')}")
                        print(f"   Content-Length: {access_response.headers.get('Content-Length', 'N/A')} bytes")
                    else:
                        print(f"⚠️  URL returned status: {access_response.status_code}")
                except requests.exceptions.RequestException as e:
                    print(f"❌ Error accessing URL: {str(e)}")
                
                print()
                
                # Step 4: Wait and test expiration (if expiration is short)
                if time_until_expiry.total_seconds() < 300:  # Less than 5 minutes
                    print("⏳ Step 4: Waiting for expiration (this will take a moment)...")
                    wait_seconds = int(time_until_expiry.total_seconds()) + 5
                    print(f"   Waiting {wait_seconds} seconds until after expiration...")
                    
                    for i in range(wait_seconds, 0, -10):
                        print(f"   ⏱️  {i} seconds remaining...", end='\r')
                        time.sleep(min(10, i))
                    print()
                    
                    print("🔍 Testing URL access after expiration...")
                    try:
                        expired_response = requests.head(blob_url, timeout=10)
                        if expired_response.status_code == 403:
                            print(f"✅ URL correctly expired! (Status: 403 Forbidden)")
                        elif expired_response.status_code == 404:
                            print(f"✅ URL correctly expired! (Status: 404 Not Found)")
                        else:
                            print(f"⚠️  Unexpected status: {expired_response.status_code}")
                    except requests.exceptions.RequestException as e:
                        print(f"✅ URL correctly expired! (Error: {str(e)})")
                else:
                    print("⏭️  Step 4: Skipping expiration test (expiry time is too long)")
                    print(f"   To test expiration quickly, set ALDAR_SHARED_PDF_SAS_TOKEN_EXPIRY_HOURS to a small value")
                    print(f"   Example: ALDAR_SHARED_PDF_SAS_TOKEN_EXPIRY_HOURS=0.016 (1 minute)")
                
            except ValueError as e:
                print(f"❌ Error parsing SAS token: {str(e)}")
                print(f"   This might mean the URL doesn't have a SAS token (plain URL)")
                return
            
            print()
            print("=" * 70)
            print("✅ Test completed successfully!")
            print("=" * 70)
            
        except requests.exceptions.HTTPError as e:
            print(f"❌ HTTP Error: {e}")
            if hasattr(e.response, 'text'):
                print(f"   Response: {e.response.text}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Request Error: {str(e)}")
        except Exception as e:
            print(f"❌ Unexpected Error: {str(e)}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test shared_pdf expiration functionality")
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_URL", "http://localhost:8080"),
        help="Base URL of the API (default: http://localhost:8080 or API_URL env var)"
    )
    parser.add_argument(
        "--token",
        default=os.getenv("TOKEN"),
        help="Bearer token for authentication (or set TOKEN env var)"
    )
    parser.add_argument(
        "--test-file",
        help="Path to a test PDF file (optional, will create a dummy file if not provided)"
    )
    
    args = parser.parse_args()
    
    if not args.token:
        print("❌ Error: Authentication token is required")
        print("   Set TOKEN environment variable or use --token argument")
        print()
        print("Example:")
        print("  TOKEN=your_token_here python scripts/test_shared_pdf_expiration.py")
        sys.exit(1)
    
    # Check if expiration is configured for quick testing
    expiry_hours = os.getenv("ALDAR_SHARED_PDF_SAS_TOKEN_EXPIRY_HOURS")
    if expiry_hours:
        print(f"⚙️  Using custom expiration: {expiry_hours} hours")
        print()
    
    test_shared_pdf_upload(args.base_url, args.token, args.test_file)
