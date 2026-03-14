"""ReactiveGoalLoop — ReAct pattern for multi-step goal execution.

Implements the Observe-Think-Act cycle:
1. LLM generates a step-by-step plan
2. For each step: build context prompt -> LLM decides action -> execute via pipeline
3. Observations are masked (only last + summaries) to control token usage
4. Key facts persist across steps for data-dependent chains
5. All state persisted to SQLite for crash recovery

/ Loop reactivo para ejecucion de goals multi-paso.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Optional

from marlow.kernel.observation_router import ObservationRouter, Observation
from marlow.kernel.error_recovery import ErrorRecoveryPolicy, RecoveryLevel

logger = logging.getLogger("marlow.kernel.reactive_loop")


class ReactiveGoalLoop:
    """ReAct loop for multi-step goal execution.

    Parameters
    ----------
    execution_pipeline : ExecutionPipeline
        Executes tools through security gate, event bus, etc.
    context_builder : ContextBuilder
        Builds dynamic desktop context for LLM prompts.
    react_repo : ReactSessionRepo
        SQLite persistence for session state.
    llm_generate : callable
        async (prompt: str) -> str. LLM text generation function.
    """

    def __init__(
        self,
        execution_pipeline,
        context_builder,
        react_repo,
        llm_generate,
    ):
        self.pipeline = execution_pipeline
        self.context = context_builder
        self.repo = react_repo
        self._llm_generate = llm_generate
        self.observation_router = ObservationRouter(
            execution_pipeline=execution_pipeline,
        )
        self.error_recovery = ErrorRecoveryPolicy(llm_generate=llm_generate)

    def set_desktop_observer(self, observer):
        """Inject desktop observer for UI action verification."""
        self.observation_router.observer = observer

    async def execute(self, goal_text: str, channel: str = "console") -> dict:
        """Execute a multi-step goal. Returns dict with response and status."""
        session = None
        try:
            # Reset error recovery counters
            self.error_recovery.reset()

            # 1. Create session
            session = self.repo.create_session(goal_text, channel)
            logger.info(
                "ReactiveGoalLoop started: %s — %s",
                session["id"], goal_text[:80],
            )

            # 2. Generate plan
            plan = await self._generate_plan(goal_text)
            if not plan:
                self.repo.complete_session(session["id"], "failed")
                return {
                    "response": "No pude crear un plan para esta tarea.",
                    "status": "failed",
                }

            session["plan"] = plan
            session["max_iterations"] = min(int(len(plan) * 1.5) + 1, 12)
            self.repo.update_session(
                session["id"],
                plan=plan,
                max_iterations=session["max_iterations"],
            )

            # 3. Execution loop
            while (
                session["current_step"] < len(session["plan"])
                and session["iteration_count"] < session["max_iterations"]
                and session["status"] == "active"
            ):
                session["iteration_count"] += 1

                # 3a. Build prompt
                prompt = self._build_step_prompt(session)

                # 3b. LLM decides next action
                llm_response = await self._llm_generate(prompt)
                action = self._parse_action(llm_response)

                # If parse failed (not intentional "done"), retry once
                if action is None and llm_response:
                    lower = llm_response.lower()
                    is_done = any(w in lower for w in ('"done"', "'done'", "complete", "finished"))
                    if not is_done:
                        logger.warning("  Parse failed, retrying with short prompt...")
                        action = await self._retry_parse(prompt)

                # LLM says done (or retry also failed)
                if action is None:
                    session["status"] = "completed"
                    break

                # 3c. Execute via pipeline
                tool_name = action.get("tool", "")
                params = action.get("parameters", {})
                logger.info(
                    "  Step %d: %s — %s",
                    session["current_step"] + 1,
                    tool_name,
                    action.get("thought", "direct"),
                )

                try:
                    result = await self.pipeline.execute(tool_name, params)
                    if hasattr(result, "to_dict"):
                        result = result.to_dict()
                except Exception as e:
                    result = {"error": str(e)}

                # 3d. Observe result via ObservationRouter
                obs_result = result if isinstance(result, dict) else {"result": str(result)}
                observation = await self.observation_router.observe(
                    tool_name=action.get("tool", ""),
                    action_params=action.get("parameters", {}),
                    result=obs_result,
                )
                success = observation.success
                logger.info(
                    "  Observation: %s | success=%s | confidence=%.1f | %s",
                    observation.type, observation.success,
                    observation.confidence, observation.summary[:80],
                )

                # 3e. Update session
                if success:
                    if action.get("key_fact"):
                        session["key_facts"].append(action["key_fact"])

                    step_desc = (
                        session["plan"][session["current_step"]]
                        if session["current_step"] < len(session["plan"])
                        else "extra step"
                    )
                    session["completed_steps"].append({
                        "step": session["current_step"],
                        "description": step_desc,
                        "result_summary": observation.summary or self._summarize_step(action, result),
                        "timestamp": datetime.now().isoformat(),
                    })
                    session["current_step"] += 1

                    # Keep last 3 observations
                    obs_text = observation.summary + "\n" + self._format_observation(observation.content)
                    session["observations"].append(obs_text)
                    if len(session["observations"]) > 3:
                        session["observations"] = session["observations"][-3:]

                else:
                    # Error recovery via policy
                    error_info = {
                        "step": session["current_step"],
                        "tool": action.get("tool"),
                        "error": observation.summary[:200],
                        "confidence": observation.confidence,
                        "iteration": session["iteration_count"],
                        "timestamp": datetime.now().isoformat(),
                    }
                    session["errors"].append(error_info)

                    # Classify and handle error
                    decision = self.error_recovery.classify(
                        observation.content if hasattr(observation, "content") else result,
                        session,
                        observation,
                    )
                    recovered = await self.error_recovery.execute_recovery(
                        decision, session, action, observation,
                    )
                    error_info["recovery"] = decision.level.value

                    if not recovered:
                        session["status"] = "failed"
                        break

                # 3f. Persist state
                self.repo.update_session(
                    session["id"],
                    current_step=session["current_step"],
                    iteration_count=session["iteration_count"],
                    plan=session["plan"],
                    key_facts=session["key_facts"],
                    completed_steps=session["completed_steps"],
                    errors=session["errors"],
                    observations=session["observations"],
                    status=session["status"],
                )

            # 4. Determine final status
            if session["status"] == "active":
                if session["current_step"] >= len(session["plan"]):
                    session["status"] = "completed"
                else:
                    session["status"] = "aborted"
                    logger.warning(
                        "  Goal aborted: max iterations (%d) reached",
                        session["max_iterations"],
                    )

            # 5. Generate summary
            summary = await self._generate_summary(session)
            self.repo.complete_session(session["id"], session["status"])

            logger.info(
                "ReactiveGoalLoop finished: %s — %s",
                session["id"], session["status"],
            )
            return {"response": summary, "status": session["status"]}

        except Exception as e:
            logger.error("ReactiveGoalLoop error: %s", e, exc_info=True)
            if session:
                self.repo.complete_session(session["id"], "failed")
            return {
                "response": f"Error ejecutando la tarea: {e}",
                "status": "failed",
            }

    # ── Plan generation ──

    async def _generate_plan(self, goal_text: str) -> list:
        """LLM generates a step-by-step plan."""
        try:
            ctx = self.context.build() if self.context else ""
        except Exception:
            ctx = ""

        prompt = (
            "You are Marlow, an AI desktop assistant. Generate a step-by-step plan.\n\n"
            f"Task: {goal_text}\n\n"
            f"Current state:\n{ctx}\n\n"
            "Available tool categories: filesystem (search_files, list_directory, "
            "read_file, write_file, edit_file, git_status, send_file_telegram), "
            "window management, screenshots, accessibility, clipboard, OCR, "
            "system commands, memory.\n\n"
            "Respond ONLY with a JSON array of step descriptions. "
            "Be specific about which tools to use. Keep it to 2-8 steps.\n"
            'Example: ["Search for the file using search_files", '
            '"Read the file using read_file", '
            '"Send it via send_file_telegram"]'
        )

        response = await self._llm_generate(prompt)
        return self._parse_json_array(response)

    # ── Step prompt builder ──

    def _build_step_prompt(self, session: dict) -> str:
        """Build context-selective prompt for current step."""
        sections = []

        sections.append(
            "You are Marlow, an AI desktop assistant executing a multi-step task.\n\n"
            f"[Goal]\n{session['goal_text']}"
        )

        # Plan with current step marked
        plan_lines = []
        for i, step in enumerate(session["plan"]):
            if i < session["current_step"]:
                plan_lines.append(f"  {i+1}. DONE: {step}")
            elif i == session["current_step"]:
                plan_lines.append(f"  {i+1}. CURRENT: {step}")
            else:
                plan_lines.append(f"  {i+1}. TODO: {step}")
        sections.append("[Plan]\n" + "\n".join(plan_lines))

        # Key facts
        if session.get("key_facts"):
            facts = "\n".join(f"  - {f}" for f in session["key_facts"])
            sections.append(f"[Key Facts]\n{facts}")

        # Completed steps (1-line summaries)
        if session.get("completed_steps"):
            summaries = "\n".join(
                f"  - Step {s['step']+1}: {s['result_summary']}"
                for s in session["completed_steps"]
            )
            sections.append(f"[Completed]\n{summaries}")

        # Last observation (truncated for prompt size)
        if session.get("observations"):
            last_obs = session["observations"][-1]
            if len(last_obs) > 600:
                last_obs = last_obs[:600] + "\n... (observation truncated)"
            sections.append(f"[Last Observation]\n{last_obs}")

        # Errors on current step
        step_errors = [
            e for e in session.get("errors", [])
            if e["step"] == session["current_step"]
        ]
        if step_errors:
            err_text = "\n".join(f"  - {e['error']}" for e in step_errors)
            sections.append(
                f"[Errors on this step]\n{err_text}\nTry a different approach."
            )

        # Desktop context
        try:
            if self.context:
                desktop_ctx = self.context.build()
                if desktop_ctx:
                    sections.append(f"[Desktop State]\n{desktop_ctx}")
        except Exception:
            pass

        # Instruction
        sections.append(
            "[Instruction]\n"
            "Execute the CURRENT step of the plan. Respond in JSON:\n"
            "{\n"
            '  "thought": "optional reasoning",\n'
            '  "tool": "tool_name",\n'
            '  "parameters": {...},\n'
            '  "expected_outcome": "what you expect",\n'
            '  "key_fact": "optional data to remember"\n'
            "}\n"
            'If ALL steps are complete, respond: {"done": true, "summary": "brief result"}'
        )

        return "\n\n".join(sections)

    # ── LLM response parsing ──

    def _parse_action_inner(self, llm_response: str) -> Optional[dict]:
        """Parse LLM response into action dict or None if done. No logging."""
        try:
            text = llm_response.strip()

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)

            if data.get("done"):
                return None

            if "tool" not in data:
                return None

            return data

        except (json.JSONDecodeError, IndexError, KeyError):
            return None

    def _parse_action(self, llm_response: str) -> Optional[dict]:
        """Parse LLM response with logging."""
        result = self._parse_action_inner(llm_response)
        if result is None and llm_response:
            # Check if it was intentional "done" vs parse failure
            lower = llm_response.lower()
            if any(w in lower for w in ('"done"', "'done'", "complete", "finished")):
                return None  # Intentional done
            logger.warning(
                "Failed to parse LLM action — response: %s",
                llm_response[:200],
            )
        return result

    async def _retry_parse(self, original_prompt: str) -> Optional[dict]:
        """Retry when LLM response was truncated/malformed."""
        retry_prompt = (
            "Your previous response was truncated or malformed JSON. "
            "Respond with ONLY a short JSON object, no explanation, no markdown:\n"
            '{"tool": "tool_name", "parameters": {...}}\n'
            'Or if done: {"done": true, "summary": "brief result"}'
        )
        try:
            response = await self._llm_generate(retry_prompt)
            return self._parse_action_inner(response)
        except Exception:
            return None

    def _parse_json_array(self, text: str) -> list:
        """Parse a JSON array from LLM response."""
        try:
            text = text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            result = json.loads(text)
            if isinstance(result, list):
                return result
            return []
        except Exception:
            logger.warning("Failed to parse plan JSON: %s", text[:200])
            return []

    # ── Result processing ──

    def _check_success(self, action: dict, result) -> bool:
        """Basic success check."""
        if isinstance(result, dict):
            if "error" in result and result.get("success") is not True:
                return False
            return True
        return result is not None

    def _summarize_step(self, action: dict, result) -> str:
        """Create 1-line summary of a completed step."""
        tool = action.get("tool", "unknown")
        if isinstance(result, dict):
            if "results" in result:
                return f"{tool}: found {len(result['results'])} results"
            if "content" in result:
                preview = str(result["content"])[:80]
                return f"{tool}: {preview}..."
            if "entries" in result:
                return f"{tool}: {result.get('total_entries', '?')} entries"
            if "action" in result:
                return f"{tool}: {result['action']}"
            if "branch" in result:
                return f"{tool}: branch={result['branch']}"
            return f"{tool}: completed"
        return f"{tool}: done"

    def _format_observation(self, result) -> str:
        """Format a tool result as an observation string (max 500 chars)."""
        if isinstance(result, dict):
            text = json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > 500:
                text = text[:500] + "... (truncated)"
            return text
        return str(result)[:500]

    # ── Summary generation ──

    async def _generate_summary(self, session: dict) -> str:
        """Generate a user-friendly summary."""
        if session["status"] == "completed" and session.get("completed_steps"):
            steps_text = "\n".join(
                f"- {s['result_summary']}"
                for s in session["completed_steps"]
            )
            facts = json.dumps(
                session.get("key_facts", []), ensure_ascii=False,
            )
            prompt = (
                "Summarize what was accomplished. Be concise and natural in Spanish.\n\n"
                f"Original request: {session['goal_text']}\n"
                f"Steps completed:\n{steps_text}\n"
                f"Key facts: {facts}\n\n"
                "Write a brief, friendly summary in Spanish. 2-3 sentences max."
            )
            try:
                return await self._llm_generate(prompt)
            except Exception:
                return "Tarea completada."

        elif session["status"] == "failed":
            errors = session.get("errors", [])
            if errors:
                err_text = "\n".join(f"- {e['error']}" for e in errors[:3])
                return f"No pude completar la tarea. Errores:\n{err_text}"
            return "No pude completar la tarea."

        elif session["status"] == "aborted":
            done = len(session.get("completed_steps", []))
            total = len(session.get("plan", []))
            return (
                f"Tarea parcialmente completada ({done}/{total} pasos). "
                "Se alcanzo el limite de intentos."
            )

        return "Tarea procesada."
