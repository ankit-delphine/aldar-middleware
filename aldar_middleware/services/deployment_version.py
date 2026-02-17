"""Service for fetching deployment versions from Kubernetes."""

import subprocess
import re
import os
from typing import Dict, Optional, Tuple
from loguru import logger

from aldar_middleware.settings import settings

# Try to import Kubernetes client (for in-cluster access)
try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    KUBERNETES_CLIENT_AVAILABLE = True
except ImportError:
    KUBERNETES_CLIENT_AVAILABLE = False
    logger.warning("kubernetes package not installed. Only kubectl command will be available.")


class DeploymentVersionService:
    """Service to get deployment image versions from Kubernetes."""
    
    _k8s_client_initialized = False
    _k8s_apps_api = None
    _use_k8s_client = False

    @classmethod
    def _is_running_in_cluster(cls) -> bool:
        """
        Check if the application is running inside a Kubernetes cluster.
        
        Returns:
            True if running in cluster, False otherwise
        """
        # Check for Kubernetes service account token (standard in-cluster indicator)
        service_account_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
        return os.path.exists(service_account_path)

    @classmethod
    def _initialize_k8s_client(cls) -> bool:
        """
        Initialize Kubernetes Python client if running in cluster.
        
        Returns:
            True if client initialized successfully, False otherwise
        """
        if cls._k8s_client_initialized:
            return cls._use_k8s_client
        
        if not KUBERNETES_CLIENT_AVAILABLE:
            logger.info("Kubernetes Python client not available, using kubectl commands")
            cls._k8s_client_initialized = True
            cls._use_k8s_client = False
            return False
        
        try:
            if cls._is_running_in_cluster():
                logger.info("Running in Kubernetes cluster, using in-cluster config")
                config.load_incluster_config()
                cls._k8s_apps_api = client.AppsV1Api()
                cls._use_k8s_client = True
                logger.info("Successfully initialized Kubernetes Python client")
            else:
                logger.info("Not running in cluster, will use kubectl commands")
                cls._use_k8s_client = False
        except Exception as e:
            logger.warning(f"Failed to initialize Kubernetes client: {e}. Falling back to kubectl")
            cls._use_k8s_client = False
        
        cls._k8s_client_initialized = True
        return cls._use_k8s_client

    @classmethod
    def _get_deployment_image_k8s_client(cls, namespace: str, deployment: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Get deployment image using Kubernetes Python client (for in-cluster access).
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            
        Returns:
            Tuple of (image_string, error_message)
        """
        try:
            if not cls._k8s_apps_api:
                return None, "Kubernetes API client not initialized"
            
            logger.info(f"Fetching deployment using K8s API: {namespace}/{deployment}")
            
            # Get deployment object
            deployment_obj = cls._k8s_apps_api.read_namespaced_deployment(
                name=deployment,
                namespace=namespace
            )
            
            # Extract image from first container
            if (deployment_obj.spec and 
                deployment_obj.spec.template and 
                deployment_obj.spec.template.spec and 
                deployment_obj.spec.template.spec.containers):
                
                image = deployment_obj.spec.template.spec.containers[0].image
                logger.info(f"Retrieved image via K8s API: {image}")
                return image, None
            else:
                error_msg = f"No containers found in deployment {deployment}"
                logger.error(error_msg)
                return None, error_msg
                
        except ApiException as e:
            error_msg = f"K8s API error (code {e.status}): {e.reason}"
            logger.error(f"Error fetching {namespace}/{deployment}: {error_msg}")
            return None, error_msg
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error fetching {namespace}/{deployment}: {error_msg}")
            return None, error_msg

    @classmethod
    def _get_deployment_image(cls, namespace: str, deployment: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Get deployment image using the best available method.
        Uses Kubernetes Python client if in-cluster, otherwise falls back to kubectl.
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            
        Returns:
            Tuple of (image_string, error_message)
        """
        # Initialize K8s client if not already done
        cls._initialize_k8s_client()
        
        # Try Kubernetes Python client first if available
        if cls._use_k8s_client:
            logger.info("Using Kubernetes Python client")
            image, error = cls._get_deployment_image_k8s_client(namespace, deployment)
            if image:
                return image, None
            logger.warning(f"K8s client failed: {error}. Falling back to kubectl")
        
        # Fall back to kubectl command
        logger.info("Using kubectl command")
        return cls._get_deployment_image_kubectl(namespace, deployment)

    @classmethod    
    def get_deployments_config(cls) -> Dict:
        """Get deployment configurations from settings."""
        return {
            "frontend": {
                "namespace": settings.k8s_namespace_frontend,
                "deployment": settings.k8s_deployment_frontend,
                "key": "aiq-frontend"
            },
            "backend": {
                "namespace": settings.k8s_namespace_backend,
                "deployment": settings.k8s_deployment_backend,
                "key": "aldar-middleware-main"
            },
            "data": {
                "namespace": settings.k8s_namespace_data,
                "deployment": settings.k8s_deployment_data,
                "key": "aiq-middleware-ai"
            }
        }

    @staticmethod
    def _extract_image_version(image_string: str, deployment_key: str) -> Optional[str]:
        """
        Extract version from image string.
        
        Example: 
        - Input: "acradqaioshared01.azurecr.io/aldar-middleware-main:1.1.0-dev"
        - Output: "aldar-middleware-main:1.1.0-dev"
        """
        try:
            if not image_string:
                return None
            
            # Extract the image name and tag (everything after the last /)
            match = re.search(r'/([^/]+:[^/]+)$', image_string.strip())
            if match:
                return match.group(1)
            
            # If no match, try to extract at least the image:tag pattern
            match = re.search(r'([^/\s]+:[^/\s]+)$', image_string.strip())
            if match:
                return match.group(1)
            
            return image_string.strip()
        except Exception as e:
            logger.error(f"Error extracting version from '{image_string}': {e}")
            return None

    @staticmethod
    def _get_deployment_image_kubectl(namespace: str, deployment: str) -> tuple[Optional[str], Optional[str]]:
        """
        Get deployment image using kubectl command (for local development).
        
        Args:
            namespace: Kubernetes namespace
            deployment: Deployment name
            
        Returns:
            Tuple of (image_string, error_message)
        """
        try:
            # Build kubectl command
            cmd = [
                "kubectl",
                "-n", namespace,
                "get", "deployment", deployment,
                "-o", "jsonpath={.spec.template.spec.containers[0].image}"
            ]
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            # Execute command without shell for better security and cross-platform compatibility
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                shell=False
            )
            
            logger.info(f"Command return code: {result.returncode}")
            logger.info(f"Command stdout: {result.stdout[:200] if result.stdout else 'None'}")
            logger.info(f"Command stderr: {result.stderr[:200] if result.stderr else 'None'}")
            
            if result.returncode != 0:
                error_msg = f"kubectl failed (code {result.returncode}): {result.stderr or 'No error message'}"
                logger.error(f"Error for {namespace}/{deployment}: {error_msg}")
                return None, error_msg
            
            if not result.stdout or not result.stdout.strip():
                error_msg = f"No image found for deployment {deployment} in namespace {namespace}"
                logger.warning(error_msg)
                return None, error_msg
            
            return result.stdout.strip(), None
            
        except subprocess.TimeoutExpired:
            error_msg = f"Timeout (30s) getting image for {namespace}/{deployment}"
            logger.error(error_msg)
            return None, error_msg
        except FileNotFoundError:
            error_msg = "kubectl command not found. Is kubectl installed and in PATH?"
            logger.error(error_msg)
            return None, error_msg
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(f"Error getting image for {namespace}/{deployment}: {error_msg}")
            return None, error_msg

    @classmethod
    def get_all_deployment_versions(cls) -> Dict[str, Optional[str]]:
        """
        Get all deployment versions for frontend, backend, and data services.
        
        Returns:
            Dictionary with deployment versions:
            {
                "aiq-frontend": "aiq-frontend:1.0.0-dev",
                "aldar-middleware-main": "aldar-middleware-main:1.1.0-dev",
                "aiq-middleware-ai": "aiq-middleware-ai:1.2.0-dev"
            }
        """
        versions = {}
        errors = []
        
        deployments = cls.get_deployments_config()
        
        for service_name, config in deployments.items():
            namespace = config["namespace"]
            deployment = config["deployment"]
            key = config["key"]
            
            logger.info(f"Fetching version for {service_name} ({namespace}/{deployment})")
            
            # Get raw image string from kubectl
            image_string, error = cls._get_deployment_image(namespace, deployment)
            
            if image_string:
                # Extract clean version
                version = cls._extract_image_version(image_string, key)
                versions[key] = version
                logger.info(f"Got version for {key}: {version}")
            else:
                versions[key] = None
                error_msg = f"{service_name}: {error or 'Unknown error'}"
                errors.append(error_msg)
                logger.warning(f"Could not get version for {key}: {error}")
        
        # Add error summary if any failures
        if errors:
            versions["_errors"] = errors
        
        return versions

    @classmethod
    def get_deployment_version(cls, service: str) -> tuple[Optional[str], Optional[str]]:
        """
        Get deployment version for a specific service.
        
        Args:
            service: Service name ('frontend', 'backend', or 'data')
            
        Returns:
            Tuple of (version_string, error_message)
        """
        deployments = cls.get_deployments_config()
        
        if service not in deployments:
            error_msg = f"Unknown service: {service}. Valid services: {', '.join(deployments.keys())}"
            logger.error(error_msg)
            return None, error_msg
        
        config = deployments[service]
        namespace = config["namespace"]
        deployment = config["deployment"]
        key = config["key"]
        
        image_string, error = cls._get_deployment_image(namespace, deployment)
        
        if image_string:
            version = cls._extract_image_version(image_string, key)
            return version, None
        
        return None, error or "Failed to retrieve deployment version"
