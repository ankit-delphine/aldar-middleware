"""
Demo endpoints for testing monitoring and metrics in Grafana.

These endpoints generate various types of metrics that can be visualized in Grafana:
- HTTP request metrics
- Business metrics (user activity, chat messages)
- Performance metrics (response times, error rates)
- System metrics (active connections, cache operations)
- AI service metrics (token usage, API costs)
"""

import logging
import time
import random
from typing import Dict, Any, List
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aldar_middleware.database.base import get_db
from aldar_middleware.monitoring.prometheus import (
    REQUEST_COUNT,
    REQUEST_DURATION,
    AGENT_CALLS_TOTAL,
    AGENT_CALL_DURATION,
    AGENT_ERRORS_TOTAL,
    OPENAI_API_CALLS,
    OPENAI_TOKENS_USED,
    OPENAI_RESPONSE_TIME,
    OPENAI_COST_ESTIMATED,
    DATABASE_OPERATIONS,
    DATABASE_QUERY_DURATION,
    record_agent_call,
    record_agent_error,
    record_openai_call,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================
# 1. HTTP Request Metrics Demo
# ============================================

@router.get("/metrics/http-requests")
async def demo_http_requests() -> Dict[str, Any]:
    """
    Generate various HTTP request metrics.
    
    This endpoint simulates different HTTP request patterns:
    - Success requests (200, 201)
    - Client errors (400, 404, 422)
    - Server errors (500, 503)
    """
    endpoints = ["/api/chat", "/api/auth", "/api/questions", "/api/agents"]
    methods = ["GET", "POST", "PUT", "DELETE"]
    statuses = [200, 201, 400, 404, 422, 500, 503]
    
    results = []
    for _ in range(20):
        endpoint = random.choice(endpoints)
        method = random.choice(methods)
        status = random.choice(statuses)
        
        REQUEST_COUNT.labels(
            method=method,
            endpoint=endpoint,
            status_code=status
        ).inc()
        
        results.append({
            "method": method,
            "endpoint": endpoint,
            "status_code": status
        })
    
    logger.info(f"Generated {len(results)} HTTP request metrics")
    
    return {
        "status": "success",
        "message": f"Generated {len(results)} HTTP request metrics",
        "requests": results
    }


@router.get("/metrics/request-latency")
async def demo_request_latency(min_duration: float = Query(0.01, ge=0.001),
                                max_duration: float = Query(5.0, le=60.0)) -> Dict[str, Any]:
    """
    Generate request latency metrics with variable response times.
    
    Simulates slow and fast requests to test performance monitoring.
    """
    endpoints = ["/api/chat", "/api/agents/execute", "/api/knowledge-base"]
    methods = ["GET", "POST"]
    
    results = []
    for _ in range(15):
        endpoint = random.choice(endpoints)
        method = random.choice(methods)
        duration = random.uniform(min_duration, max_duration)
        
        REQUEST_DURATION.labels(
            method=method,
            endpoint=endpoint
        ).observe(duration)
        
        results.append({
            "endpoint": endpoint,
            "method": method,
            "duration_seconds": round(duration, 4)
        })
    
    logger.info(f"Generated {len(results)} latency metrics (min={min_duration}s, max={max_duration}s)")
    
    return {
        "status": "success",
        "message": f"Generated {len(results)} latency metrics",
        "latencies": results
    }


# ============================================
# 2. Model Response Metrics Demo
# ============================================


# ============================================
# 3. Agent Metrics Demo
# ============================================

@router.post("/metrics/agent-calls")
async def demo_agent_calls(count: int = Query(10, ge=1, le=100)) -> Dict[str, Any]:
    """
    Simulate agent execution metrics.
    
    Tracks different types of agents and their performance.
    """
    agent_types = ["qa", "summarization", "routing", "analysis"]
    agent_names = ["gpt-4-qa", "claude-summary", "routing-engine", "bert-analyzer"]
    methods = ["execute", "query", "process"]
    statuses = ["success", "error", "timeout"]
    
    results = []
    
    for _ in range(count):
        agent_type = random.choice(agent_types)
        agent_name = random.choice(agent_names)
        method = random.choice(methods)
        status = random.choice(statuses)
        duration = random.uniform(0.1, 10.0)
        
        AGENT_CALLS_TOTAL.labels(
            agent_type=agent_type,
            agent_name=agent_name,
            method=method,
            status=status
        ).inc()
        
        AGENT_CALL_DURATION.labels(
            agent_type=agent_type,
            agent_name=agent_name,
            method=method
        ).observe(duration)
        
        if status == "error":
            error_types = ["timeout", "rate_limit", "invalid_input", "service_error"]
            error_type = random.choice(error_types)
            AGENT_ERRORS_TOTAL.labels(
                agent_type=agent_type,
                agent_name=agent_name,
                error_type=error_type
            ).inc()
        
        results.append({
            "agent_type": agent_type,
            "agent_name": agent_name,
            "method": method,
            "status": status,
            "duration_seconds": round(duration, 4)
        })
    
    logger.info(f"Generated {count} agent call metrics")
    
    return {
        "status": "success",
        "message": f"Generated {count} agent metrics",
        "agents": results
    }


# ============================================
# 4. OpenAI/AI Service Metrics Demo
# ============================================

@router.post("/metrics/openai-calls")
async def demo_openai_calls(count: int = Query(10, ge=1, le=100)) -> Dict[str, Any]:
    """
    Simulate OpenAI API call metrics.
    
    Tracks API calls, token usage, response times, and estimated costs.
    """
    models = ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-4o"]
    methods = ["completion", "chat", "embedding"]
    statuses = ["success", "error", "rate_limited"]
    
    results = []
    
    for _ in range(count):
        model = random.choice(models)
        method = random.choice(methods)
        status = random.choice(statuses)
        
        # Random token usage
        prompt_tokens = random.randint(50, 2000)
        completion_tokens = random.randint(50, 2000)
        total_tokens = prompt_tokens + completion_tokens
        duration = random.uniform(0.5, 30.0)
        
        # Record metrics
        record_openai_call(
            model=model,
            method=method,
            status=status,
            duration=duration,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens
        )
        
        results.append({
            "model": model,
            "method": method,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "duration_seconds": round(duration, 2)
        })
    
    logger.info(f"Generated {count} OpenAI call metrics")
    
    return {
        "status": "success",
        "message": f"Generated {count} OpenAI metrics",
        "calls": results
    }


@router.get("/metrics/openai-cost-report")
async def demo_openai_cost_report() -> Dict[str, Any]:
    """
    Generate a report of estimated OpenAI costs.
    
    Useful for cost tracking in production environments.
    """
    models = ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-4o"]
    
    results = {
        "total_cost_usd": 0,
        "models": {}
    }
    
    for model in models:
        # Generate random cost for each model
        cost = round(random.uniform(0.10, 50.0), 4)
        OPENAI_COST_ESTIMATED.labels(model=model).inc(cost)
        
        results["models"][model] = {
            "estimated_cost_usd": cost,
            "timestamp": datetime.utcnow().isoformat()
        }
        results["total_cost_usd"] += cost
    
    results["total_cost_usd"] = round(results["total_cost_usd"], 4)
    
    logger.info(f"Generated OpenAI cost report: ${results['total_cost_usd']:.4f}")
    
    return {
        "status": "success",
        "message": "OpenAI cost report generated",
        "cost_report": results
    }


# ============================================
# 5. Database Metrics Demo
# ============================================

@router.post("/metrics/database-operations")
async def demo_database_operations(count: int = Query(10, ge=1, le=100),
                                   db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Simulate database operation metrics.
    
    Tracks read/write operations on different tables.
    """
    operations = ["SELECT", "INSERT", "UPDATE", "DELETE"]
    tables = ["conversations", "users", "messages", "agents", "queries"]
    
    results = []
    
    for _ in range(count):
        operation = random.choice(operations)
        table = random.choice(tables)
        duration = random.uniform(0.001, 1.0)
        
        DATABASE_OPERATIONS.labels(
            operation=operation,
            table=table
        ).inc()
        
        DATABASE_QUERY_DURATION.labels(
            operation=operation,
            table=table
        ).observe(duration)
        
        results.append({
            "operation": operation,
            "table": table,
            "duration_ms": round(duration * 1000, 2)
        })
    
    logger.info(f"Generated {count} database operation metrics")
    
    return {
        "status": "success",
        "message": f"Generated {count} database operation metrics",
        "operations": results
    }


# ============================================
# 6. Combined/Stress Test
# ============================================

@router.post("/metrics/stress-test")
async def demo_stress_test(duration_seconds: int = Query(10, ge=1, le=60),
                           db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Run a stress test that generates a variety of metrics continuously.
    
    Useful for testing dashboard responsiveness and alert thresholds.
    """
    start_time = time.time()
    metrics_count = 0
    
    logger.info(f"Starting metrics stress test for {duration_seconds} seconds")
    
    while (time.time() - start_time) < duration_seconds:
        # HTTP metrics
        REQUEST_COUNT.labels(
            method=random.choice(["GET", "POST"]),
            endpoint=random.choice(["/api/chat", "/api/agents"]),
            status_code=random.choice([200, 400, 500])
        ).inc()
        metrics_count += 1
        
        # Chat metrics
        # CHAT_MESSAGES_TOTAL.labels(
        #     message_type=random.choice(["user_message", "ai_response"])
        # ).inc()
        metrics_count += 1
        
        # Database metrics
        DATABASE_OPERATIONS.labels(
            operation=random.choice(["SELECT", "INSERT"]),
            table=random.choice(["messages", "users"])
        ).inc()
        metrics_count += 1
        
        # Small delay to prevent CPU spinning
        time.sleep(0.01)
    
    elapsed_time = time.time() - start_time
    
    logger.info(f"Stress test completed: {metrics_count} metrics generated in {elapsed_time:.2f}s")
    
    return {
        "status": "success",
        "message": "Stress test completed",
        "metrics_generated": metrics_count,
        "duration_seconds": round(elapsed_time, 2),
        "metrics_per_second": round(metrics_count / elapsed_time, 2)
    }


# ============================================
# 7. Metrics Summary
# ============================================

@router.get("/metrics/summary")
async def metrics_summary() -> Dict[str, Any]:
    """
    Get a summary of available demo endpoints.
    
    This endpoint serves as documentation for the demo API.
    """
    return {
        "status": "success",
        "message": "Demo metrics endpoints summary",
        "endpoints": {
            "HTTP Metrics": {
                "demo_http_requests": "POST /demo/metrics/http-requests",
                "demo_request_latency": "GET /demo/metrics/request-latency"
            },
            "Agent Metrics": {
                "demo_agent_calls": "POST /demo/metrics/agent-calls"
            },
            "OpenAI/AI Metrics": {
                "demo_openai_calls": "POST /demo/metrics/openai-calls",
                "demo_openai_cost_report": "GET /demo/metrics/openai-cost-report"
            },
            "Database Metrics": {
                "demo_database_operations": "POST /demo/metrics/database-operations"
            },
            "Stress Test": {
                "demo_stress_test": "POST /demo/metrics/stress-test"
            },
            "Metrics Endpoint": {
                "prometheus_metrics": "GET /metrics"
            }
        },
        "grafana_setup": {
            "metrics_endpoint": "http://your-app:8000/metrics",
            "data_source_type": "Prometheus",
            "available_metrics": [
                "aiq_http_requests_total",
                "aiq_http_request_duration_seconds",
                "aiq_agent_calls_total",
                "aiq_agent_call_duration_seconds",
                "aiq_openai_api_calls_total",
                "aiq_openai_tokens_used_total",
                "aiq_openai_response_duration_seconds",
                "aiq_openai_cost_estimated_usd",
                "aiq_database_operations_total",
                "aiq_database_query_duration_seconds"
            ]
        }
    }


# ============================================
# 8. Error & Exception Simulation
# ============================================

@router.post("/metrics/simulate-errors")
async def simulate_errors(count: int = Query(5, ge=1, le=50)) -> Dict[str, Any]:
    """
    Simulate various error conditions.
    
    Useful for testing error handling and alerting.
    """
    error_types = ["timeout", "rate_limit", "invalid_input", "service_error", "auth_failed"]
    agent_types = ["qa", "summarization", "routing"]
    agent_names = ["gpt-4-qa", "claude-summary", "routing-engine"]
    
    results = []
    
    for _ in range(count):
        error_type = random.choice(error_types)
        agent_type = random.choice(agent_types)
        agent_name = random.choice(agent_names)
        
        # Record error metrics
        AGENT_ERRORS_TOTAL.labels(
            agent_type=agent_type,
            agent_name=agent_name,
            error_type=error_type
        ).inc()
        
        results.append({
            "agent_type": agent_type,
            "agent_name": agent_name,
            "error_type": error_type
        })
    
    logger.warning(f"Simulated {count} error metrics for testing")
    
    return {
        "status": "success",
        "message": f"Simulated {count} error metrics",
        "errors": results
    }