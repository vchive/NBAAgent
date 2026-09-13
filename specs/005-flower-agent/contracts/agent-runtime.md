# Bounded Agent Runtime Contract

## Turn input

The application supplies one validated turn with:

- opaque request/session identifiers used only for routing;
- the user's current question with ordinary wording preserved, controls/URLs/instruction injection removed at the boundary;
- Asia/Shanghai display time;
- compact confirmed `GardenContext` explicitly labelled as data, not instructions;
- at most four complete prior user/assistant turns;
- one absolute UTC deadline, max four iterations and max four tool calls.

The same logical browser session is retained until the user clicks “新对话”. The application, rather than the runtime, owns continuity. Native runtime memory is disabled.

## Capability manifest

For domain `flower`, the exact allow-list is:

- `flower_lookup(question?, plant?)`
- `care_plan(question?, plant?, location?, light?, container?, observation?)`
- `flower_search(query)`

The manifest must explicitly disable shell, filesystem, browser, generic web, arbitrary URL, MCP, memory, skills and delegation. Cross-domain calls such as `nba_query` fail before dispatch.

## Tool bridge

Every call must validate the task ID, toolset, argument allow-list, control/injection patterns, duplicate fingerprint, maximum calls, tool timeout, request deadline and maximum result bytes. The bridge returns only a field allow-list and removes URLs, provider/source/ID metadata, exact addresses and public notices before observations reach the model.

## Completion

A successful result may contain cleaned Markdown, bounded observations, tool-call audit metadata, usage and latency. Before public projection the application removes hidden thought blocks, URLs, runtime/provider/model names, prompt/tool vocabulary and raw error text. Unsafe or ungrounded output falls back to the complete local answer with a provider-neutral notice.

Runtime quota, auth, rate-limit and timeout conditions map to the corresponding public intelligence notice. Raw exception text, model name, endpoint and finish reason never cross the HTTP/SSE boundary.
