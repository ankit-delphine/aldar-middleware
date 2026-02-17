"""Deployment version routes."""

from typing import Dict, Optional
import subprocess
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from aldar_middleware.services.deployment_version import DeploymentVersionService


router = APIRouter(prefix="/deployment", tags=["Deployment Versions"])


class DeploymentVersionsResponse(BaseModel):
    """Response model for deployment versions."""
    
    frontend: Optional[str] = Field(
        None,
        alias="aiq-frontend",
        description="Frontend deployment version"
    )
    backend: Optional[str] = Field(
        None,
        alias="aldar-middleware-main",
        description="Backend deployment version"
    )
    data: Optional[str] = Field(
        None,
        alias="aiq-middleware-ai",
        description="Data/AI middleware deployment version"
    )
    
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "aiq-frontend": "aiq-frontend:1.0.0-dev",
                "aldar-middleware-main": "aldar-middleware-main:1.1.0-dev",
                "aiq-middleware-ai": "aiq-middleware-ai:1.2.0-dev"
            }
        }


class SingleDeploymentVersionResponse(BaseModel):
    """Response model for single deployment version."""
    
    service: str = Field(..., description="Service name")
    version: Optional[str] = Field(None, description="Deployment version")
    
    class Config:
        json_schema_extra = {
            "example": {
                "service": "backend",
                "version": "aldar-middleware-main:1.1.0-dev"
            }
        }


class KubectlHealthResponse(BaseModel):
    """Response model for kubectl health check."""
    
    kubectl_available: bool = Field(..., description="Whether kubectl is available")
    kubectl_version: Optional[str] = Field(None, description="kubectl version")
    cluster_info: Optional[str] = Field(None, description="Current cluster context")
    in_cluster: bool = Field(default=False, description="Whether running in Kubernetes cluster")
    method: str = Field(default="kubectl", description="Method used: 'k8s-client' or 'kubectl'")
    error: Optional[str] = Field(None, description="Error message if any")
    
    class Config:
        json_schema_extra = {
            "example": {
                "kubectl_available": True,
                "kubectl_version": "v1.28.0",
                "cluster_info": "aks-aio-adq-dev-uaen",
                "in_cluster": False,
                "method": "kubectl",
                "error": None
            }
        }


@router.get(
    "/health",
    response_model=KubectlHealthResponse,
    summary="Check kubectl connectivity",
    description=(
        "Health check endpoint to verify kubectl is available and can connect to the cluster.\n"
        "Use this to diagnose connectivity issues before attempting to fetch deployment versions."
    ),
    responses={
        200: {
            "description": "kubectl health check completed",
            "content": {
                "application/json": {
                    "example": {
                        "kubectl_available": True,
                        "kubectl_version": "v1.28.0",
                        "cluster_info": "aks-aio-adq-dev-uaen",
                        "error": None
                    }
                }
            }
        }
    }
)
async def check_kubectl_health() -> KubectlHealthResponse:
    """
    Check if kubectl is available and can connect to the cluster.
    
    Returns health status including kubectl version and cluster info.
    """
    response = KubectlHealthResponse(
        kubectl_available=False,
        kubectl_version=None,
        cluster_info=None,
        in_cluster=False,
        method="kubectl",
        error=None
    )
    
    # Check if running in Kubernetes cluster
    response.in_cluster = DeploymentVersionService._is_running_in_cluster()
    
    # Initialize and check which method will be used
    DeploymentVersionService._initialize_k8s_client()
    if DeploymentVersionService._use_k8s_client:
        response.method = "k8s-client"
        response.kubectl_available = True
        logger.info("Using Kubernetes Python client (in-cluster)")
        return response
    else:
        response.method = "kubectl"
    
    try:
        # Check kubectl version
        result = subprocess.run(
            ["kubectl", "version", "--client"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if result.returncode == 0:
            response.kubectl_available = True
            response.kubectl_version = result.stdout.strip()
            logger.info(f"kubectl is available: {response.kubectl_version}")
        else:
            response.error = f"kubectl version check failed: {result.stderr}"
            logger.error(response.error)
            return response
        
        # Check cluster connectivity
        result = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
            timeout=10,
            shell=True
        )
        
        if result.returncode == 0:
            response.cluster_info = result.stdout.strip()
            logger.info(f"Connected to cluster: {response.cluster_info}")
        else:
            response.error = f"No cluster context: {result.stderr}"
            logger.warning(response.error)
        
    except FileNotFoundError:
        response.error = "kubectl not found in PATH"
        logger.error(response.error)
    except subprocess.TimeoutExpired:
        response.error = "kubectl command timed out"
        logger.error(response.error)
    except Exception as e:
        response.error = f"Unexpected error: {str(e)}"
        logger.error(f"Error checking kubectl health: {e}")
    
    return response


@router.get(
    "/versions",
    response_model=DeploymentVersionsResponse,
    summary="Get all deployment versions",
    description=(
        "Retrieves the current deployment image versions for all three environments:\n"
        "- **Frontend** (aiq-frontend from middleware-ui namespace)\n"
        "- **Backend** (aldar-middleware-main from middleware-main namespace)\n"
        "- **Data/AI** (aiq-genai-orchestration from middleware-ai namespace)\n\n"
        "This endpoint queries Kubernetes deployments using kubectl to fetch the current "
        "container image tags being used in each environment."
    ),
    responses={
        200: {
            "description": "Successfully retrieved deployment versions",
            "content": {
                "application/json": {
                    "example": {
                        "aiq-frontend": "aiq-frontend:1.0.0-dev",
                        "aldar-middleware-main": "aldar-middleware-main:1.1.0-dev",
                        "aiq-middleware-ai": "aiq-middleware-ai:1.2.0-dev"
                    }
                }
            }
        },
        500: {
            "description": "Internal server error while fetching versions"
        }
    }
)
async def get_deployment_versions() -> Dict[str, Optional[str]]:
    """
    Get all deployment versions.
    
    Returns a dictionary containing the current image versions for:
    - aiq-frontend (Frontend)
    - aldar-middleware-main (Backend)
    - aiq-middleware-ai (Data/AI)
    
    Note: Returns None for any deployment that cannot be queried.
    """
    try:
        logger.info("Fetching all deployment versions")
        versions = DeploymentVersionService.get_all_deployment_versions()
        logger.info(f"Retrieved deployment versions: {versions}")
        return versions
    except Exception as e:
        logger.error(f"Error fetching deployment versions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch deployment versions: {str(e)}"
        )


@router.get(
    "/versions/{service}",
    response_model=SingleDeploymentVersionResponse,
    summary="Get single deployment version",
    description=(
        "Retrieves the current deployment image version for a specific service.\n\n"
        "**Available services:**\n"
        "- `frontend` - AIQ Frontend (middleware-ui namespace)\n"
        "- `backend` - AIQ Backend Main (middleware-main namespace)\n"
        "- `data` - AIQ Data/AI Middleware (middleware-ai namespace)"
    ),
    responses={
        200: {
            "description": "Successfully retrieved deployment version",
            "content": {
                "application/json": {
                    "example": {
                        "service": "backend",
                        "version": "aldar-middleware-main:1.1.0-dev"
                    }
                }
            }
        },
        400: {
            "description": "Invalid service name"
        },
        404: {
            "description": "Deployment not found"
        },
        500: {
            "description": "Internal server error while fetching version"
        }
    }
)
async def get_deployment_version(service: str) -> SingleDeploymentVersionResponse:
    """
    Get deployment version for a specific service.
    
    Args:
        service: Service name (frontend, backend, or data)
        
    Returns:
        Service name and its current version
    """
    # Validate service name
    valid_services = ["frontend", "backend", "data"]
    if service not in valid_services:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid service name. Must be one of: {', '.join(valid_services)}"
        )
    
    try:
        logger.info(f"Fetching version for service: {service}")
        version, error = DeploymentVersionService.get_deployment_version(service)
        
        if version is None:
            error_detail = f"Could not retrieve version for service '{service}'"
            if error:
                error_detail += f": {error}"
            raise HTTPException(
                status_code=404,
                detail=error_detail
            )
        
        logger.info(f"Retrieved version for {service}: {version}")
        return SingleDeploymentVersionResponse(service=service, version=version)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching version for {service}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch version for {service}: {str(e)}"
        )
