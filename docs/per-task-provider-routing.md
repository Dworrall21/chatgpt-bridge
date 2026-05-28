# Per-Task Provider Routing for delegate_task

A feature enhancement for Hermes Agent's `delegate_task` tool, enabling per-task
provider and model overrides so sub-agents can be routed to different models
based on task requirements.

## Background

The current `delegation` config in `config.yaml` provides **one** provider:model
pair for all sub-agents spawned via `delegate_task`. This is limiting when
different tasks in the same batch need different capabilities — e.g., web
research through ChatGPT Bridge, vision processing through mimo-v2.5, and
general tool use through deepseek-v4-flash.

The `_build_child_agent()` function already accepts override parameters
(`override_provider`, `override_model`, `override_base_url`, etc.) but they're
all resolved from the single `delegation` config section. The schema and
tool-calling surface don't expose these to the LLM.

## Research

This design was developed by tracing the Hermes Agent codebase and consulting
**ChatGPT Bridge** for implementation advice. The ChatGPT Bridge analysis
confirmed the approach and helped identify the minimal set of changes needed.

Key files examined:
- `hermes-agent/tools/delegate_tool.py` — full tool implementation
- `hermes-agent/run_agent.py` — AIAgent class and dispatch method

Key functions traced:
- `delegate_task()` (line 1918) — entry point called by the tool dispatcher
- `_build_child_agent()` (line 870) — constructs the child AIAgent with overrides
- `_resolve_delegation_credentials()` (line 2356) — resolves provider config to
  credential bundle
- `DELEGATE_TASK_SCHEMA` (line 2660) — the JSON schema exposed to calling LLMs

## Changes Required

**One file**: `hermes-agent/tools/delegate_tool.py`

**No changes needed** in `_build_child_agent()` — it already accepts
`override_provider`, `override_base_url`, `override_api_key`,
`override_api_mode` and uses them to construct the child agent.

### Change A — Schema: Add routing fields to per-task items

In `DELEGATE_TASK_SCHEMA` (line ~2700), add these properties to each
task item in the `tasks` array:

```python
"provider": {
    "type": "string",
    "description": (
        "Optional provider override for this specific delegated task. "
        "If omitted, the default delegation provider is used."
    ),
},
"model": {
    "type": "string",
    "description": (
        "Optional model override for this specific delegated task. "
        "If omitted, the default delegation model is used."
    ),
},
"base_url": {
    "type": "string",
    "description": (
        "Optional base URL override for this specific delegated task. "
        "Use only when the provider requires a non-default endpoint."
    ),
},
"api_key": {
    "type": "string",
    "description": (
        "Optional API key override for this specific delegated task. "
        "Prefer configured credentials when available."
    ),
},
"api_mode": {
    "type": "string",
    "description": (
        "Optional API mode override for this specific delegated task. "
        "One of: chat_completions, codex_responses, anthropic_messages."
    ),
},
```

### Change B — Schema: Add top-level routing fields

Same fields at the top-level `properties` in the schema, so callers can set
a default for all sub-agents:

```python
"provider": {
    "type": "string",
    "description": "Default provider override for all delegated tasks.",
},
"model": {
    "type": "string",
    "description": "Default model override for all delegated tasks.",
},
"base_url": {"type": "string", "description": "Default base URL override."},
"api_key": {"type": "string", "description": "Default API key override."},
"api_mode": {"type": "string", "description": "Default API mode override."},
```

### Change C — Function signature

Add the routing parameters to `delegate_task()` (line ~1918):

```python
def delegate_task(
    goal: Optional[str] = None,
    context: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    max_iterations: Optional[int] = None,
    acp_command: Optional[str] = None,
    acp_args: Optional[List[str]] = None,
    role: Optional[str] = None,
    parent_agent=None,
    # ── Per-call routing overrides ─────────────────────────────────
    provider: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> str:
```

### Change D — Registration handler

Update the `registry.register()` call (line ~2787) to extract the new fields
from `args` and pass them to `delegate_task()`:

```python
registry.register(
    ...,
    handler=lambda args, **kw: delegate_task(
        goal=args.get("goal"),
        context=args.get("context"),
        toolsets=args.get("toolsets"),
        tasks=args.get("tasks"),
        max_iterations=args.get("max_iterations"),
        acp_command=args.get("acp_command"),
        acp_args=args.get("acp_args"),
        role=args.get("role"),
        provider=args.get("provider"),
        model=args.get("model"),
        base_url=args.get("base_url"),
        api_key=args.get("api_key"),
        api_mode=args.get("api_mode"),
        parent_agent=kw.get("parent_agent"),
    ),
)
```

### Change E — Single-task dict

For single-task mode (line ~2020), the task dict should carry the top-level
routing fields:

```python
task_list = [{
    "goal": goal,
    "context": context,
    "toolsets": toolsets,
    "role": top_role,
    "provider": provider,
    "model": model,
    "base_url": base_url,
    "api_key": api_key,
    "api_mode": api_mode,
}]
```

### Change F — Per-task credential resolution

In the task loop (line ~2056), resolve per-task credentials before building
each child:

```python
for i, t in enumerate(task_list):
    # Per-task beats top-level beats delegation config defaults
    task_provider = t.get("provider") or provider
    task_model = t.get("model") or model
    task_base_url = t.get("base_url") or base_url
    task_api_key = t.get("api_key") or api_key
    task_api_mode = t.get("api_mode") or api_mode

    # Resolve credentials for this task's provider
    task_creds = creds  # start with delegation config defaults
    if task_provider and task_provider != creds.get("provider"):
        # Resolve this specific provider's credentials
        task_creds = _resolve_credentials_for_provider(
            task_provider, task_model, task_base_url,
            task_api_key, task_api_mode,
        )
    elif task_base_url or task_api_key:
        # Override specific fields but keep provider
        task_creds = dict(creds)
        if task_base_url:
            task_creds["base_url"] = task_base_url
        if task_api_key:
            task_creds["api_key"] = task_api_key
        if task_api_mode:
            task_creds["api_mode"] = task_api_mode
    if task_model:
        task_creds["model"] = task_model

    child = _build_child_agent(
        ...
        model=task_creds["model"],
        override_provider=task_creds["provider"],
        override_base_url=task_creds["base_url"],
        override_api_key=task_creds["api_key"],
        override_api_mode=task_creds["api_mode"],
        ...
    )
```

This requires a new helper `_resolve_credentials_for_provider()` that works
like `_resolve_delegation_credentials()` but accepts explicit provider/model
values instead of reading from the delegation config section.

### Change G — New helper function

```python
def _resolve_credentials_for_provider(
    provider: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    api_mode: Optional[str] = None,
) -> dict:
    """Resolve a credential bundle for a specific provider:model pair.

    Looks up the provider in the runtime provider system (same path as
    _resolve_delegation_credentials) and fills in missing fields from
    the provider's registered defaults.

    Returns a dict with keys: model, provider, base_url, api_key, api_mode.
    """
    from hermes_cli.runtime_provider import (
        resolve_provider_config,
    )

    # Load the provider's configuration from config.yaml
    provider_cfg = resolve_provider_config(provider)

    effective_base_url = base_url or provider_cfg.get("base_url", "")
    effective_api_key = api_key or provider_cfg.get("api_key", "")
    effective_api_mode = api_mode or provider_cfg.get("api_mode", "")
    effective_model = model or provider_cfg.get("default_model", "")

    # Auto-detect api_mode from URL if not explicitly set
    if not effective_api_mode and effective_base_url:
        from hermes_cli.runtime_provider import _detect_api_mode_for_url
        effective_api_mode = (
            _detect_api_mode_for_url(effective_base_url)
            or "chat_completions"
        )

    return {
        "model": effective_model or None,
        "provider": provider,
        "base_url": effective_base_url or None,
        "api_key": effective_api_key or None,
        "api_mode": effective_api_mode or None,
    }
```

## Summary of Changes

| Change | Location | Lines | Complexity |
|--------|----------|-------|------------|
| A — Per-task schema fields | `DELEGATE_TASK_SCHEMA` items.properties | ~20 | Low |
| B — Top-level schema fields | `DELEGATE_TASK_SCHEMA` properties | ~20 | Low |
| C — Function signature | `delegate_task()` definition | +5 params | Low |
| D — Registration handler | `registry.register()` lambda | ~+6 lines | Low |
| E — Single-task dict | `task_list` construction | ~+6 lines | Low |
| F — Per-task resolution | Task loop at child build | ~25 lines | Medium |
| G — New helper | New function above task loop | ~35 lines | Medium |

**Total**: ~110 lines added, 0 lines removed.

## Backward Compatibility

All existing callers are unaffected:

- No `provider`/`model` in task dict → uses delegation config defaults (same
  as before)
- No routing params in `args` → `delegate_task()` receives `None` for all
  new params → delegation config defaults apply
- Schema additions are optional fields → old schemas cached by clients still
  work (they just won't see the new fields)

## Verification

1. Run the existing test suite:
   ```bash
   cd /home/david/.hermes/hermes-agent
   python3 -m pytest tests/test_delegate_task.py -v
   ```

2. Manual test — single task with provider override:
   ```python
   delegate_task(
       goal="Research quantum computing",
       provider="chatgpt-bridge",
       model="chatgpt",
       toolsets=["web"],
   )
   ```

3. Manual test — batch with mixed providers:
   ```python
   delegate_task(tasks=[
       {"goal": "Research X", "provider": "chatgpt-bridge", "model": "chatgpt"},
       {"goal": "Analyze image", "provider": "opencode-go", "model": "mimo-v2.5"},
   ])
   ```

4. Manual test — no overrides (backward compat):
   ```python
   delegate_task(tasks=[{"goal": "Do the thing"}])
   ```
