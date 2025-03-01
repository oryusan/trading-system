"""
API-specific reference models with enhanced service integration.

Features:
- API-specific model definitions
- Enhanced validation
- Service integration
- Proper error handling
"""

from typing import Dict, List, Optional, Any, Literal, ClassVar, Set
from datetime import datetime
from decimal import Decimal
import ipaddress

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.errors.base import ValidationError
from app.core.logging.logger import get_logger
from app.core.references import (
    UserContext,
    validate_model_relationship,
    validate_service_access
)

logger = get_logger(__name__)

# --- Request Context Models ---

class RequestMetadata(BaseModel):
    """Enhanced request metadata with validation."""
    request_id: str = Field(..., description="Unique request identifier")
    path: str = Field(..., description="Request path")
    method: str = Field(..., description="HTTP method")
    client_ip: str = Field(..., description="Client IP address")
    user_agent: Optional[str] = Field(None, description="User agent string")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Request timestamp")

    def to_log_context(self) -> Dict[str, Any]:
        """Convert to logging context."""
        return {
            "request_id": self.request_id,
            "path": self.path,
            "method": self.method,
            "client_ip": self.client_ip,
            "timestamp": self.timestamp.isoformat()
        }

class DeviceInfo(BaseModel):
    """Extended device information with validation."""
    ip_address: str = Field(..., description="Client IP address")
    user_agent: str = Field(..., description="User agent string")
    device_type: Optional[str] = Field(None, description="Device type if detectable")
    device_id: Optional[str] = Field(None, description="Unique device identifier")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional device metadata")

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        """Validate IP address using the ipaddress module."""
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValidationError("Invalid IP address format", context={"ip_address": v})
        return v

# --- Request Parameters ---

class PaginationParams(BaseModel):
    """Enhanced pagination parameters with validation."""
    page: int = Field(1, description="Page number", gt=0)
    page_size: int = Field(100, description="Items per page", gt=0, le=1000)
    sort_by: Optional[str] = Field(None, description="Sort field")
    sort_order: Literal["asc", "desc"] = Field("asc", description="Sort direction (asc/desc)")

    def to_mongo(self) -> Dict[str, Any]:
        """Convert to MongoDB query parameters."""
        query = {
            "skip": (self.page - 1) * self.page_size,
            "limit": self.page_size,
        }
        if self.sort_by:
            query["sort"] = [(self.sort_by, 1 if self.sort_order == "asc" else -1)]
        return query

class DateRangeParams(BaseModel):
    """Enhanced date range parameters with validation."""
    start_date: datetime
    end_date: datetime
    max_days: int = Field(365, description="Maximum allowed days in range", gt=0)
    
    @property
    def total_days(self) -> int:
        """Calculate total days in range."""
        return (self.end_date - self.start_date).days

    @model_validator(mode="after")
    def validate_dates(self) -> "DateRangeParams":
        """Ensure that the end date comes after the start date and the range does not exceed max_days."""
        if self.end_date < self.start_date:
            raise ValidationError(
                "End date must be after start date",
                context={
                    "start_date": self.start_date.isoformat(),
                    "end_date": self.end_date.isoformat()
                }
            )
        if self.total_days > self.max_days:
            raise ValidationError(
                "Date range exceeds maximum allowed days",
                context={
                    "total_days": self.total_days,
                    "max_days": self.max_days,
                    "start_date": self.start_date.isoformat(),
                    "end_date": self.end_date.isoformat()
                }
            )
        return self

# --- Authentication Models ---

class LoginRequest(BaseModel):
    """Enhanced login request with validation."""
    username: str = Field(
        ...,
        description="Username for authentication",
        min_length=3,
        max_length=32,
        pattern=r'^[a-zA-Z0-9]+$'
    )
    password: str = Field(..., description="User password", min_length=8)
    device_info: Optional[DeviceInfo] = Field(None, description="Client device information")

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        """Normalize username to lowercase."""
        return v.lower()

class TokenResponse(BaseModel):
    """Enhanced authentication token response."""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration in seconds", gt=0)
    user_context: UserContext = Field(..., description="User context information")

# --- API Response Models ---

class APIErrorResponse(BaseModel):
    """Enhanced API error response."""
    success: bool = Field(False, description="Operation success status")
    error: str = Field(..., description="Error message")
    error_code: str = Field(..., description="Error code for client handling")
    context: Optional[Dict[str, Any]] = Field(None, description="Error context details")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Error timestamp")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to response dictionary with ISO-formatted timestamp."""
        return {
            "success": self.success,
            "error": {
                "message": self.error,
                "code": self.error_code,
                "context": self.context,
                "timestamp": self.timestamp.isoformat()
            }
        }

class APISuccessResponse(BaseModel):
    """Enhanced API success response."""
    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")
    data: Optional[Dict[str, Any]] = Field(None, description="Response data")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional response metadata")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to response dictionary with ISO-formatted timestamp in metadata."""
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "metadata": {**self.metadata, "timestamp": self.timestamp.isoformat()}
        }

# --- Service Response Models ---
class ServiceResponse(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None
    
class ServiceMetrics(BaseModel):
    """Service performance metrics."""
    request_count: int = Field(0, description="Total requests processed")
    error_count: int = Field(0, description="Total errors encountered")
    average_response_time: float = Field(0.0, description="Average response time in seconds")
    last_error: Optional[str] = Field(None, description="Last error message")

# --- Performance Models ---

class PerformanceParams(BaseModel):
    """Performance calculation parameters."""
    start_balance: Decimal = Field(..., description="Starting balance", gt=0)
    include_fees: bool = Field(True, description="Include trading fees in calculations")
    metrics: List[str] = Field(default_factory=list, description="Specific metrics to calculate")

    VALID_METRICS: ClassVar[Set[str]] = {"pnl", "roi", "drawdown", "sharpe", "sortino", "win_rate", "profit_factor"}

    @field_validator("metrics")
    @classmethod
    def validate_metrics(cls, v: List[str]) -> List[str]:
        """Validate requested performance metrics."""
        invalid = set(v) - cls.VALID_METRICS
        if invalid:
            raise ValidationError(
                "Invalid performance metrics",
                context={
                    "invalid_metrics": list(invalid),
                    "valid_metrics": list(cls.VALID_METRICS)
                }
            )
        return v
