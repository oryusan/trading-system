"""
Performance tracking and calculation package.

This package provides services for calculating, tracking, and aggregating 
performance metrics across trading accounts.

Components:
- Calculator: Metric calculation logic
- Aggregator: Performance data aggregation
- Storage: Performance data persistence
- Service: High-level performance tracking interface
"""

from .service import performance_service
from .calculator import PerformanceCalculator
from .aggregator import PerformanceAggregator
from .storage import PerformanceStorage

__all__ = [
    'performance_service',   # Main service instance
    'PerformanceCalculator', # Metric calculation class
    'PerformanceAggregator', # Performance data aggregation class
    'PerformanceStorage'     # Performance data storage class
]