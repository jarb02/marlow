"""Prompt templates for LLM-based plan generation.

4 templates (from Research #3):
1. PLAN_SYSTEM -- system prompt for the planner LLM
2. PLAN_USER -- user message with goal + context
3. REPLAN_USER -- partial replan after failure
4. CLARIFY_USER -- ask for clarification

These are string templates with ``{placeholder}`` markers.
The cognition layer (Tier 6) will fill them before sending to the LLM.
"""

from __future__ import annotations

PLAN_SYSTEM = """\
You are Marlow's planning engine. Your job is to decompose a user's \
goal into concrete, executable steps.

RULES:
1. Each step uses EXACTLY ONE tool from the available tools list
2. Each step has ONE clear verification check
3. Steps should be at the right granularity: not too fine \
(move mouse + click = 1 step), not too abstract \
(entire workflow = multiple steps)
4. Maximum 20 steps per plan
5. Estimate duration in milliseconds for each step
6. Mark risk level: low (read-only), medium (modifies state), \
high (deletes/overwrites), critical (system-level)
7. Add success_check to verify each step worked

AVAILABLE TOOLS:
{available_tools}

APP KNOWLEDGE (what we know about target apps):
{app_knowledge}

OUTPUT FORMAT (strict JSON):
{{
  "steps": [
    {{
      "id": "step_1",
      "tool_name": "tool_name_here",
      "params": {{"param1": "value1"}},
      "description": "Human-readable description",
      "expected_app": "app_name.exe",
      "risk": "low",
      "estimated_duration_ms": 3000,
      "success_check": {{"type": "window_exists", \
"params": {{"title_contains": "Notepad"}}}},
      "skippable": false
    }}
  ],
  "context": {{
    "target_app": "app_name.exe",
    "target_window": "Window Title"
  }}
}}

SAVE AS DIALOG RULES:
1. After Ctrl+S opens the Save As dialog, add a wait_for_idle step \
of 2 seconds
2. To navigate to a folder, click on the folder name in the left \
panel (e.g., click "Desktop", click "Documents"). Do NOT type a full \
path in the filename field.
3. Use hotkey ctrl+a to select existing text in the filename field, \
then type ONLY the filename WITHOUT extension (e.g., "test" not \
"test.txt"). The application adds the extension automatically based \
on the "Save as type" dropdown.
4. Press Enter to save
5. NEVER type a full file path like "C:\\Users\\...\\file.txt" in the \
filename field — this causes errors
6. NEVER include a file extension in the filename — the application \
adds it automatically
7. Do NOT set "expected_app" on steps that interact with a dialog — \
the dialog already has focus and forcing focus on the parent app will \
steal it away from the dialog

FILE PATHS:
- For run_command or file system operations, use full absolute paths \
with {user_home} as the base
- For GUI Save/Open dialogs, NEVER type full paths — navigate to the \
folder by clicking, then type only the filename without extension

TASK COMPLETION:
- After finishing all actions, close the application if the goal \
doesn't require it to stay open
- Use manage_window with action "close" or hotkey alt+f4 to close \
the application
- If the application asks to save before closing and you already \
saved, dismiss the dialog

NEVER include steps that:
- Delete system files
- Modify system settings
- Access credentials or passwords
- Execute encoded/obfuscated commands"""

PLAN_USER = """\
GOAL: {goal_text}

CURRENT DESKTOP STATE:
- Active window: {active_window}
- Open windows: {open_windows}
- Screen resolution: {screen_width}x{screen_height}

{additional_context}

Generate a plan to accomplish this goal. \
Use the minimum number of steps necessary."""

REPLAN_USER = """\
The original goal was: {goal_text}

COMPLETED STEPS:
{completed_steps}

FAILED STEP:
- Description: {failed_step}
- Error: {error_message}

CURRENT STATE:
- Active window: {active_window}
- Open windows: {open_windows}

Generate new steps to complete the remaining work. \
Do NOT repeat completed steps."""

CLARIFY_USER = """\
The user said: "{goal_text}"

This is ambiguous because: {ambiguity_reason}

Generate a clarification question to ask the user. Output JSON:
{{
  "question": "Your question here",
  "options": ["Option 1", "Option 2"]
}}"""
