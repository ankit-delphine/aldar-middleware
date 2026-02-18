"""Main entry point for AIQ Backend application."""

import os
import sys
import logging
import uvicorn
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

# Add the current directory to Python path
current_dir = Path(__file__).parent.parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Also add the parent directory to ensure we can find the aldar_middleware module
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Configure basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_url(url_str: str) -> Tuple[str, str, int]:
    """
    Parse a URL string to extract protocol, host, and port.
    
    Supports:
    - Full URLs: http://localhost:8080, https://example.com:443
    - URLs without port: http://example.com (defaults to 80/443)
    - Just host:port: localhost:8080
    - Just host: localhost
    
    Returns:
        tuple: (protocol, host, port)
    """
    if not url_str:
        return ("http", "0.0.0.0", 8000)
    
    # If it contains ://, it's a full URL
    if "://" in url_str:
        parsed = urlparse(url_str)
        protocol = parsed.scheme or "http"
        # Extract hostname - prefer hostname, fallback to netloc
        if parsed.hostname:
            host = parsed.hostname
        elif parsed.netloc:
            # Remove port if present in netloc
            host = parsed.netloc.split(":")[0] if ":" in parsed.netloc else parsed.netloc
        else:
            host = "0.0.0.0"
        
        # Get port from URL or use defaults
        if parsed.port:
            port = parsed.port
        elif protocol == "https":
            port = 443
        elif protocol == "http":
            port = 80
        else:
            port = 8000
    # If it contains : but no ://, it's host:port
    elif ":" in url_str:
        parts = url_str.split(":")
        host = parts[0] if parts[0] else "0.0.0.0"
        try:
            port = int(parts[1]) if len(parts) > 1 else 8000
        except ValueError:
            port = 8000
        protocol = "http"
    # Otherwise, it's just a host
    else:
        host = url_str if url_str else "0.0.0.0"
        port = 8000
        protocol = "http"
    
    return (protocol, host, port)

def get_server_config() -> Tuple[str, int, str]:
    """
    Get server configuration from environment variables or settings.
    
    Checks in order:
    1. ALDAR_URL environment variable (full URL)
    2. ALDAR_HOST + PORT environment variables
    3. Settings defaults
    
    Returns:
        tuple: (host, port, protocol)
    """
    # Check for full URL first
    aiq_url = os.getenv("ALDAR_URL")
    if aiq_url:
        protocol, host, port = parse_url(aiq_url)
        return (host, port, protocol)
    
    # Fall back to separate HOST and PORT
    try:
        from aldar_middleware.settings import settings
        host = os.getenv("ALDAR_HOST", settings.host)
        # Check PORT first (without prefix), then ALDAR_PORT, then settings.port
        port = int(os.getenv("PORT") or os.getenv("ALDAR_PORT") or settings.port)
    except Exception:
        host = os.getenv("ALDAR_HOST", "0.0.0.0")
        port = int(os.getenv("PORT") or os.getenv("ALDAR_PORT") or 8000)
    
    protocol = "http"  # Default protocol for uvicorn
    return (host, port, protocol)

def create_minimal_app():
    """Create a minimal FastAPI app that can start without all dependencies."""
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        
        app = FastAPI(
            title="AIDAR Backend",
            version="0.1.0",
            docs_url="/docs",
            redoc_url="/redoc"
        )
        
        # Add CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
            allow_headers=["*"],
        )
        
        @app.get("/")
        async def root():
            return {"message": "AIDAR Backend is running", "status": "healthy"}
        
        @app.get("/api/v1/health")
        async def api_health():
            return {"status": "healthy", "message": "API is running"}
        
        return app
        
    except Exception as e:
        logger.error(f"Failed to create minimal app: {e}")
        raise

def create_full_app():
    """Create the full application with all features."""
    try:
        from aldar_middleware.application import get_app
        return get_app()
    except Exception as e:
        logger.warning(f"Failed to create full app: {e}")
        logger.info("Falling back to minimal app")
        return create_minimal_app()

def main():
    """Main function to start the application."""
    try:
        logger.info("🚀 Starting AIQ Backend...")
        
        # Try to create the full application first
        app = create_full_app()
        
        # Get settings
        try:
            from aldar_middleware.settings import settings
            log_level = settings.log_level.value.lower()
            reload = settings.debug
        except Exception as e:
            logger.warning(f"Failed to load settings: {e}")
            log_level = "info"
            reload = False
        
        # Get server configuration (host, port, protocol)
        host, port, protocol = get_server_config()
        
        # Build base URL for logging
        if (protocol == "http" and port == 80) or (protocol == "https" and port == 443):
            base_url = f"{protocol}://{host}"
        else:
            base_url = f"{protocol}://{host}:{port}"
        
        logger.info(f"📍 Server will be available at: {base_url}")
        logger.info(f"📚 API Documentation: {base_url}/docs")
        logger.info(f"🔍 Health Check: {base_url}/api/v1/health")
        
        if reload:
            # When using reload, uvicorn expects an import string
            uvicorn.run(
                "aldar_middleware:app",
                host=host,
                port=port,
                log_level=log_level,
                reload=reload,
            )
        else:
            # When not using reload, we can pass the app object directly
            uvicorn.run(
                app,
                host=host,
                port=port,
                log_level=log_level,
                reload=reload,
            )
        
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        logger.info("Attempting to start minimal application...")
        
        # Fallback to minimal app
        try:
            app = create_minimal_app()
            host, port, protocol = get_server_config()
            
            # Build base URL for logging
            if (protocol == "http" and port == 80) or (protocol == "https" and port == 443):
                base_url = f"{protocol}://{host}"
            else:
                base_url = f"{protocol}://{host}:{port}"
            
            logger.info(f"📍 Minimal server will be available at: {base_url}")
            logger.info(f"🔍 Health Check: {base_url}/api/v1/health")
            
            uvicorn.run(
                app,
                host=host,
                port=port,
                log_level="info",
                reload=False
            )
        except Exception as fallback_error:
            logger.error(f"Failed to start minimal application: {fallback_error}")
            sys.exit(1)

if __name__ == "__main__":
    main()