"""
Custom exceptions for the AIQ Backend application
"""


class AIQBaseException(Exception):
    """Base exception for AIQ Backend"""
    pass


class RBACError(AIQBaseException):
    """Exception raised for RBAC-related errors"""
    pass


class PermissionDeniedError(AIQBaseException):
    """Exception raised when permission is denied"""
    pass


class ValidationError(AIQBaseException):
    """Exception raised for validation errors"""
    pass


class DatabaseError(AIQBaseException):
    """Exception raised for database-related errors"""
    pass


class ServiceError(AIQBaseException):
    """Exception raised for service-related errors"""
    pass


class AuthenticationError(AIQBaseException):
    """Exception raised for authentication errors"""
    pass


class AuthorizationError(AIQBaseException):
    """Exception raised for authorization errors"""
    pass


class ConfigurationError(AIQBaseException):
    """Exception raised for configuration errors"""
    pass


class ExternalServiceError(AIQBaseException):
    """Exception raised for external service errors"""
    pass
