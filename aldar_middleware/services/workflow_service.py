"""Workflow orchestration and execution service."""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from aldar_middleware.settings.context import get_correlation_id, track_agent_call
from aldar_middleware.models.routing import (
    Workflow,
    WorkflowExecution,
    WorkflowStep,
)
from aldar_middleware.models.mcp import AgentMethodExecution
from aldar_middleware.services.agent_executor import AgentExecutor


class WorkflowService:
    """Workflow orchestration and execution service."""

    def __init__(self, db: AsyncSession):
        """Initialize workflow service.

        Args:
            db: Async database session
        """
        self.db = db
        self.correlation_id = get_correlation_id()
        self.executor = AgentExecutor(db)

    async def create_workflow(
        self,
        user_id: UUID,
        name: str,
        definition: Dict,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_template: bool = False,
    ) -> Workflow:
        """Create new workflow.

        Args:
            user_id: User ID
            name: Workflow name
            definition: Workflow DSL definition
            description: Workflow description
            tags: Categorization tags
            is_template: Whether this is a reusable template

        Returns:
            Created Workflow

        Raises:
            ValueError: If workflow definition is invalid
        """
        logger.info(
            "Creating workflow | user_id={user_id} name={name}",
            user_id=user_id,
            name=name,
            extra={"correlation_id": self.correlation_id},
        )

        # Validate workflow definition
        self._validate_workflow_definition(definition)

        workflow = Workflow(
            user_id=user_id,
            name=name,
            definition=definition,
            description=description,
            tags=tags or [],
            is_template=is_template,
        )
        self.db.add(workflow)
        await self.db.flush()

        logger.info(
            "Workflow created | workflow_id={workflow_id}",
            workflow_id=workflow.id,
            extra={"correlation_id": self.correlation_id},
        )

        return workflow

    async def get_workflow(self, workflow_id: UUID) -> Optional[Workflow]:
        """Get workflow definition.

        Args:
            workflow_id: Workflow ID

        Returns:
            Workflow or None if not found
        """
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        result = await self.db.execute(stmt)
        return result.scalar()

    async def list_workflows(
        self,
        user_id: UUID,
        is_template: Optional[bool] = None,
        limit: int = 100,
    ) -> List[Workflow]:
        """List workflows for user.

        Args:
            user_id: User ID
            is_template: Filter by template flag
            limit: Result limit

        Returns:
            List of Workflow objects
        """
        stmt = select(Workflow).where(Workflow.user_id == user_id)

        if is_template is not None:
            stmt = stmt.where(Workflow.is_template == is_template)

        stmt = stmt.order_by(desc(Workflow.created_at)).limit(limit)

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def update_workflow(
        self,
        workflow_id: UUID,
        updates: Dict,
    ) -> Workflow:
        """Update workflow definition.

        Args:
            workflow_id: Workflow ID
            updates: Fields to update

        Returns:
            Updated Workflow
        """
        logger.info(
            "Updating workflow | workflow_id={workflow_id}",
            workflow_id=workflow_id,
            extra={"correlation_id": self.correlation_id},
        )

        workflow = await self.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Validate if definition is being updated
        if "definition" in updates:
            self._validate_workflow_definition(updates["definition"])

        for key, value in updates.items():
            if hasattr(workflow, key):
                setattr(workflow, key, value)

        workflow.updated_at = datetime.utcnow()
        await self.db.flush()

        return workflow

    async def delete_workflow(self, workflow_id: UUID) -> bool:
        """Delete workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            True if deleted, False if not found
        """
        logger.info(
            "Deleting workflow | workflow_id={workflow_id}",
            workflow_id=workflow_id,
            extra={"correlation_id": self.correlation_id},
        )

        workflow = await self.get_workflow(workflow_id)
        if not workflow:
            return False

        await self.db.delete(workflow)
        return True

    async def execute_workflow(
        self,
        workflow_id: UUID,
        user_id: UUID,
        inputs: Optional[Dict] = None,
    ) -> WorkflowExecution:
        """Execute workflow.

        Args:
            workflow_id: Workflow ID
            user_id: User ID
            inputs: Workflow input data

        Returns:
            WorkflowExecution with execution results

        Raises:
            ValueError: If workflow not found or invalid
        """
        logger.info(
            "Executing workflow | workflow_id={workflow_id} user_id={user_id}",
            workflow_id=workflow_id,
            user_id=user_id,
            extra={"correlation_id": self.correlation_id},
        )

        workflow = await self.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Create execution record
        execution = WorkflowExecution(
            workflow_id=workflow_id,
            user_id=user_id,
            correlation_id=self.correlation_id,
            status="running",
            inputs=inputs or {},
        )
        self.db.add(execution)
        await self.db.flush()

        try:
            # Execute workflow steps
            start_time = datetime.utcnow()
            execution.execution_plan = self._build_execution_plan(workflow.definition)
            
            context = {
                "inputs": inputs or {},
                "steps": {},
                "execution_id": execution.id,
                "correlation_id": self.correlation_id,
            }

            outputs = await self._execute_workflow_steps(
                execution=execution,
                steps=workflow.definition.get("steps", []),
                context=context,
            )

            # Update execution
            execution.status = "success"
            execution.outputs = outputs
            execution.total_duration_ms = int(
                (datetime.utcnow() - start_time).total_seconds() * 1000
            )
            execution.completed_at = datetime.utcnow()

            logger.info(
                "Workflow execution completed | execution_id={execution_id} status={status}",
                execution_id=execution.id,
                status="success",
                extra={"correlation_id": self.correlation_id},
            )

        except Exception as e:
            logger.error(
                "Workflow execution failed | execution_id={execution_id} error={error}",
                execution_id=execution.id,
                error=str(e),
                extra={"correlation_id": self.correlation_id},
            )

            execution.status = "error"
            execution.outputs = {"error": str(e)}
            execution.completed_at = datetime.utcnow()

        await self.db.flush()
        return execution

    async def cancel_workflow_execution(
        self,
        execution_id: UUID,
    ) -> WorkflowExecution:
        """Cancel running workflow execution.

        Args:
            execution_id: Execution ID

        Returns:
            Updated WorkflowExecution

        Raises:
            ValueError: If execution not found
        """
        logger.info(
            "Cancelling workflow execution | execution_id={execution_id}",
            execution_id=execution_id,
            extra={"correlation_id": self.correlation_id},
        )

        stmt = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
        result = await self.db.execute(stmt)
        execution = result.scalar()

        if not execution:
            raise ValueError(f"Execution {execution_id} not found")

        execution.status = "cancelled"
        execution.completed_at = datetime.utcnow()
        await self.db.flush()

        return execution

    async def get_execution_status(self, execution_id: UUID) -> Dict:
        """Get current execution status.

        Args:
            execution_id: Execution ID

        Returns:
            Execution status details
        """
        stmt = select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
        result = await self.db.execute(stmt)
        execution = result.scalar()

        if not execution:
            return {}

        # Get step statuses
        stmt = select(WorkflowStep).where(WorkflowStep.execution_id == execution_id)
        result = await self.db.execute(stmt)
        steps = result.scalars().all()

        return {
            "execution_id": execution.id,
            "status": execution.status,
            "progress": self._calculate_progress(steps),
            "steps": [
                {
                    "step_id": step.step_id,
                    "status": step.status,
                    "duration_ms": step.duration_ms,
                }
                for step in steps
            ],
            "total_duration_ms": execution.total_duration_ms,
            "created_at": execution.created_at.isoformat() if execution.created_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
        }

    async def get_execution_history(
        self,
        workflow_id: UUID,
        limit: int = 100,
    ) -> List[WorkflowExecution]:
        """Get execution history for workflow.

        Args:
            workflow_id: Workflow ID
            limit: Result limit

        Returns:
            List of WorkflowExecution objects
        """
        stmt = (
            select(WorkflowExecution)
            .where(WorkflowExecution.workflow_id == workflow_id)
            .order_by(desc(WorkflowExecution.created_at))
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    # Private helper methods

    def _validate_workflow_definition(self, definition: Dict) -> None:
        """Validate workflow DSL definition.

        Args:
            definition: Workflow definition

        Raises:
            ValueError: If definition is invalid
        """
        required_fields = ["name", "steps"]
        for field in required_fields:
            if field not in definition:
                raise ValueError(f"Missing required field: {field}")

        steps = definition.get("steps", [])
        if not steps:
            raise ValueError("Workflow must have at least one step")

        # Validate step IDs are unique
        step_ids = set()
        for step in steps:
            if "id" not in step:
                raise ValueError("Step missing required 'id' field")
            if step["id"] in step_ids:
                raise ValueError(f"Duplicate step ID: {step['id']}")
            step_ids.add(step["id"])

        # Validate dependencies
        self._validate_dependencies(steps)

    def _validate_dependencies(self, steps: List[Dict]) -> None:
        """Validate step dependencies are valid.

        Args:
            steps: List of step definitions

        Raises:
            ValueError: If dependencies are invalid
        """
        step_ids = {step["id"] for step in steps}

        for step in steps:
            depends_on = step.get("depends_on", [])
            for dep in depends_on:
                if dep not in step_ids:
                    raise ValueError(f"Step {step['id']} depends on non-existent step {dep}")

    def _build_execution_plan(self, definition: Dict) -> Dict:
        """Build execution plan from workflow definition.

        Args:
            definition: Workflow definition

        Returns:
            Execution plan with resolved dependencies
        """
        steps = definition.get("steps", [])
        plan = {
            "steps": {},
            "order": self._topological_sort([s["id"] for s in steps], steps),
        }

        for step in steps:
            plan["steps"][step["id"]] = step

        return plan

    def _topological_sort(self, step_ids: List[str], steps: List[Dict]) -> List[str]:
        """Sort steps by dependencies (topological sort).

        Args:
            step_ids: List of step IDs
            steps: List of step definitions

        Returns:
            Ordered list of step IDs
        """
        # Build dependency graph
        graph = {sid: set() for sid in step_ids}
        in_degree = {sid: 0 for sid in step_ids}

        step_map = {step["id"]: step for step in steps}

        for step in steps:
            for dep in step.get("depends_on", []):
                graph[dep].add(step["id"])
                in_degree[step["id"]] += 1

        # Kahn's algorithm
        queue = [sid for sid in step_ids if in_degree[sid] == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    async def _execute_workflow_steps(
        self,
        execution: WorkflowExecution,
        steps: List[Dict],
        context: Dict,
    ) -> Dict:
        """Execute workflow steps in order.

        Args:
            execution: WorkflowExecution object
            steps: List of step definitions
            context: Execution context

        Returns:
            Final outputs
        """
        step_map = {step["id"]: step for step in steps}
        execution_plan = self._build_execution_plan(execution.workflow.definition)
        ordered_steps = execution_plan["order"]

        for step_id in ordered_steps:
            if step_id not in step_map:
                continue

            step_def = step_map[step_id]
            step_type = step_def.get("type", "agent_call")

            # Create step execution record
            step_exec = WorkflowStep(
                execution_id=execution.id,
                step_id=step_id,
                step_name=step_def.get("name", step_id),
                step_type=step_type,
                status="running",
                started_at=datetime.utcnow(),
            )
            self.db.add(step_exec)
            await self.db.flush()

            try:
                if step_type == "agent_call":
                    result = await self._execute_agent_call(
                        step_exec, step_def, context
                    )
                elif step_type == "condition":
                    result = await self._execute_condition(
                        step_exec, step_def, context
                    )
                elif step_type == "parallel":
                    result = await self._execute_parallel(
                        execution, step_exec, step_def, step_map, context
                    )
                elif step_type == "switch":
                    result = await self._execute_switch(
                        step_exec, step_def, context
                    )
                else:
                    raise ValueError(f"Unknown step type: {step_type}")

                # Store result in context
                context["steps"][step_id] = {
                    "output": result,
                    "status": "success",
                }

                step_exec.outputs = result
                step_exec.status = "success"

            except Exception as e:
                logger.error(
                    "Step execution failed | step_id={step_id} error={error}",
                    step_id=step_id,
                    error=str(e),
                )

                context["steps"][step_id] = {
                    "output": None,
                    "error": str(e),
                    "status": "error",
                }

                step_exec.status = "error"
                step_exec.error_reason = str(e)

                # Handle error strategy
                error_handling = execution.workflow.definition.get("error_handling", {})
                on_failure = error_handling.get("on_step_failure", "stop")

                if on_failure == "stop":
                    raise
                # else: continue on error

            finally:
                step_exec.completed_at = datetime.utcnow()
                step_exec.duration_ms = int(
                    (step_exec.completed_at - step_exec.started_at).total_seconds() * 1000
                )
                await self.db.flush()

        # Extract outputs
        output_spec = execution.workflow.definition.get("output", {})
        if output_spec.get("include"):
            outputs = {}
            for include_path in output_spec["include"]:
                outputs[include_path] = self._resolve_path(include_path, context)
            return outputs
        else:
            return context.get("steps", {})

    async def _execute_agent_call(
        self,
        step_exec: WorkflowStep,
        step_def: Dict,
        context: Dict,
    ) -> Dict:
        """Execute agent method call step.

        Args:
            step_exec: WorkflowStep object
            step_def: Step definition
            context: Execution context

        Returns:
            Step output
        """
        agent_id = step_def.get("agent_id")
        method_id = step_def.get("method_id")

        if not agent_id or not method_id:
            raise ValueError("agent_call step requires agent_id and method_id")

        # Resolve parameters from context
        params = self._resolve_parameters(step_def.get("params", {}), context)

        # Execute agent method
        result = await self.executor.execute_method(
            agent_id=agent_id,
            method_id=method_id,
            parameters=params,
            correlation_id=context["correlation_id"],
        )

        step_exec.agent_id = agent_id
        step_exec.method_id = method_id
        step_exec.inputs = params

        return result

    async def _execute_condition(
        self,
        step_exec: WorkflowStep,
        step_def: Dict,
        context: Dict,
    ) -> Dict:
        """Execute condition step.

        Args:
            step_exec: WorkflowStep object
            step_def: Step definition
            context: Execution context

        Returns:
            Condition result
        """
        condition = step_def.get("if_condition", {})
        result = self._evaluate_condition(condition, context)

        return {"condition_result": result}

    async def _execute_parallel(
        self,
        execution: WorkflowExecution,
        step_exec: WorkflowStep,
        step_def: Dict,
        step_map: Dict,
        context: Dict,
    ) -> Dict:
        """Execute parallel steps.

        Args:
            execution: WorkflowExecution object
            step_exec: WorkflowStep object
            step_def: Step definition
            step_map: Map of all steps
            context: Execution context

        Returns:
            Aggregated results from parallel steps
        """
        parallel_step_ids = step_def.get("parallel_steps", [])
        tasks = []

        for parallel_step_id in parallel_step_ids:
            if parallel_step_id not in step_map:
                continue

            parallel_step_def = step_map[parallel_step_id]

            # Create execution record for parallel step
            parallel_step_exec = WorkflowStep(
                execution_id=execution.id,
                step_id=parallel_step_id,
                step_name=parallel_step_def.get("name", parallel_step_id),
                step_type=parallel_step_def.get("type", "agent_call"),
                status="running",
                started_at=datetime.utcnow(),
            )
            self.db.add(parallel_step_exec)
            await self.db.flush()

            if parallel_step_def.get("type") == "agent_call":
                task = self._execute_agent_call(
                    parallel_step_exec, parallel_step_def, context
                )
            else:
                task = asyncio.sleep(0)  # Skip non-agent steps

            tasks.append((parallel_step_id, task, parallel_step_exec))

        # Execute all tasks concurrently
        results = {}
        if tasks:
            task_list = [task for _, task, _ in tasks]
            task_results = await asyncio.gather(*task_list, return_exceptions=True)

            for (parallel_step_id, _, step_exec), result in zip(tasks, task_results):
                if isinstance(result, Exception):
                    context["steps"][parallel_step_id] = {
                        "output": None,
                        "error": str(result),
                        "status": "error",
                    }
                    step_exec.status = "error"
                    step_exec.error_reason = str(result)
                else:
                    context["steps"][parallel_step_id] = {
                        "output": result,
                        "status": "success",
                    }
                    step_exec.outputs = result
                    step_exec.status = "success"

                step_exec.completed_at = datetime.utcnow()
                step_exec.duration_ms = int(
                    (step_exec.completed_at - step_exec.started_at).total_seconds() * 1000
                )
                results[parallel_step_id] = context["steps"][parallel_step_id]["output"]

                await self.db.flush()

        return {"parallel_results": results}

    async def _execute_switch(
        self,
        step_exec: WorkflowStep,
        step_def: Dict,
        context: Dict,
    ) -> Dict:
        """Execute switch/case logic.

        Args:
            step_exec: WorkflowStep object
            step_def: Step definition
            context: Execution context

        Returns:
            Switch result
        """
        cases = step_def.get("cases", [])

        for case in cases:
            condition = case.get("condition", {})
            if self._evaluate_condition(condition, context):
                return {"matched_case": case.get("case_id")}

        return {"matched_case": None}

    def _evaluate_condition(self, condition: Dict, context: Dict) -> bool:
        """Evaluate conditional expression.

        Args:
            condition: Condition definition
            context: Execution context

        Returns:
            Condition result
        """
        operator = condition.get("operator")
        left = self._resolve_value(condition.get("left"), context)
        right = self._resolve_value(condition.get("right"), context)

        if operator == "equals":
            return left == right
        elif operator == "not_equals":
            return left != right
        elif operator == "greater_than":
            return left > right
        elif operator == "less_than":
            return left < right
        elif operator == "in":
            return left in right if isinstance(right, (list, tuple)) else False
        else:
            return False

    def _resolve_parameters(self, params: Dict, context: Dict) -> Dict:
        """Resolve parameter values from context.

        Args:
            params: Parameter definitions
            context: Execution context

        Returns:
            Resolved parameters
        """
        resolved = {}
        for key, value in params.items():
            resolved[key] = self._resolve_value(value, context)
        return resolved

    def _resolve_value(self, value: Any, context: Dict) -> Any:
        """Resolve a single value from context.

        Args:
            value: Value to resolve (may contain ${...} references)
            context: Execution context

        Returns:
            Resolved value
        """
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            path = value[2:-1]
            return self._resolve_path(path, context)
        elif isinstance(value, dict):
            return {k: self._resolve_value(v, context) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_value(v, context) for v in value]
        else:
            return value

    def _resolve_path(self, path: str, context: Dict) -> Any:
        """Resolve a dot-separated path in context.

        Args:
            path: Path like "step_1.output.field"
            context: Execution context

        Returns:
            Resolved value
        """
        parts = path.split(".")
        value = context

        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None

        return value

    def _calculate_progress(self, steps: List[WorkflowStep]) -> float:
        """Calculate workflow progress percentage.

        Args:
            steps: List of workflow steps

        Returns:
            Progress percentage (0-100)
        """
        if not steps:
            return 0

        completed = sum(1 for s in steps if s.status in ("success", "error", "skipped"))
        return (completed / len(steps)) * 100