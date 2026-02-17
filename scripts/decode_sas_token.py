#!/usr/bin/env python3
"""
Quick script to decode and display SAS token expiration time from a blob URL.

Usage:
    python scripts/decode_sas_token.py "https://account.blob.core.windows.net/container/blob?sv=..."
"""

import sys
import urllib.parse
from datetime import datetime


def decode_sas_token(sas_url: str):
    """Decode and display SAS token information."""
    try:
        # Parse the URL
        parsed = urllib.parse.urlparse(sas_url)
        params = urllib.parse.parse_qs(parsed.query)
        
        print("=" * 70)
        print("🔍 SAS Token Analysis")
        print("=" * 70)
        print()
        print(f"Blob URL: {parsed.scheme}://{parsed.netloc}{parsed.path}")
        print()
        
        # Extract key parameters
        if 'se' in params:
            expiry_str = params['se'][0]
            # Try to parse as ISO 8601 format first (Azure SAS tokens use this)
            try:
                # Remove 'Z' and parse as UTC
                expiry_str_clean = expiry_str.rstrip('Z')
                expiry_time = datetime.strptime(expiry_str_clean, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                # Fallback to Unix timestamp
                try:
                    expiry_timestamp = int(expiry_str)
                    expiry_time = datetime.fromtimestamp(expiry_timestamp)
                except (ValueError, TypeError):
                    raise ValueError(f"Could not parse expiry time: {expiry_str}")
            now = datetime.utcnow()
            time_until_expiry = expiry_time - now
            
            print("⏰ Expiration Information:")
            print(f"   Current Time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"   Expiry Time (UTC):  {expiry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"   Time Until Expiry:  {time_until_expiry}")
            
            hours = time_until_expiry.total_seconds() / 3600
            minutes = (time_until_expiry.total_seconds() % 3600) / 60
            seconds = time_until_expiry.total_seconds() % 60
            
            if hours >= 1:
                print(f"   Expires In:         {int(hours)}h {int(minutes)}m {int(seconds)}s")
            elif minutes >= 1:
                print(f"   Expires In:         {int(minutes)}m {int(seconds)}s")
            else:
                print(f"   Expires In:         {int(seconds)}s")
            
            if time_until_expiry.total_seconds() < 0:
                print()
                print("   ⚠️  WARNING: This token has already expired!")
            elif time_until_expiry.total_seconds() < 60:
                print()
                print("   ⚠️  WARNING: This token expires in less than 1 minute!")
        else:
            print("❌ No expiration time found in SAS token")
            print("   This might be a plain URL without a SAS token")
        
        print()
        print("📋 Other SAS Token Parameters:")
        for key in ['sv', 'sr', 'sp', 'sig']:
            if key in params:
                value = params[key][0]
                if key == 'sig':
                    print(f"   {key}: {value[:20]}... (truncated)")
                else:
                    print(f"   {key}: {value}")
        
        print()
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/decode_sas_token.py <sas_url>")
        print()
        print("Example:")
        print('  python scripts/decode_sas_token.py "https://account.blob.core.windows.net/container/blob?sv=..."')
        sys.exit(1)
    
    decode_sas_token(sys.argv[1])
