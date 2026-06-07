# Obsidian Agent Bridge - Implementation Roadmap

This document serves as the single source of truth for tracking the implementation progress of the Obsidian-to-Agent Automation Bridge (OAB). If agent sessions are interrupted or context is reset, refer to this checklist to determine the next task.

---

## Progress Overview
* **Overall Progress:** 19%
* **Current Phase:** Phase 2: macOS Sandbox & Execution Provider
* **Current Task:** Setup dynamic sandbox profiles (Task 2.1)

---

## Phase 1: Core Watcher & Configuration Setup
*Goal: Initialize the repository config and establish the filesystem watcher loop that detects and mutates inline task blocks.*

- [x] **Task 1.1: Environment Configuration**
  * Create `config/config.json` following the specified schema (Vault path, security, notifications).
- [x] **Task 1.2: Incremental File Scanner**
  * Implement `scripts/agent_sync.py` containing the `mtime`-based incremental scanner to scan only recently modified files.
- [x] **Task 1.3: Tag State Mutation Parser**
  * Add parsing logic to extract commands between `/second-brain-task` and `<end-task>`.
  * Implement note mutation: `/second-brain-task` -> `/second-brain-task-running` -> `/second-brain-task-completed` / `failed`.

---

## Phase 2: macOS Sandbox & Execution Provider
*Goal: Setup macOS App Sandbox (sandbox-exec) containment and build the CLI agent wrapper.*

- [ ] **Task 2.1: sandbox-exec Profile Generator**
  * Write logic in `scripts/agent_sync.py` to generate the `.sb` Scheme sandbox profile dynamically, locking read/write operations strictly to the Obsidian vault directory.
- [ ] **Task 2.2: Provider Abstraction Layer**
  * Implement the Base Class `AgentProvider` and the concrete `AgyProvider` to spawn and manage `agy` CLI processes.
- [ ] **Task 2.3: Centralized Log Replicator**
  * Redirect agent stdout/stderr and tool calls to append entries to `_System/Agent/logs.md` in a clean markdown format.

---

## Phase 3: Router/Planner Subagent
*Goal: Integrate a lightweight subagent to automate model selection and tool routing.*

- [ ] **Task 3.1: Router Prompt Engineering**
  * Draft the system instructions prompting the router subagent (`gemini-1.5-flash`) to output structured JSON metadata.
- [ ] **Task 3.2: Router Execution & Parameter Parsing**
  * Integrate the subagent step at the start of the watcher pipeline. Parse the recommended model and tool list to dynamically configure the main run.

---

## Phase 4: MCP Servers & Native AppleScript Integration
*Goal: Setup Google Calendar/Tasks APIs and write the local macOS system messaging bridge.*

- [ ] **Task 4.1: Google OAuth Credentials & Server Setup**
  * Register a Google Cloud console application and configure `@modelcontextprotocol/server-google-calendar` in `mcp_config.json`.
  * Perform the initial authentication flow to cache the token.
- [ ] **Task 4.2: Native Notifications MCP Server**
  * Implement `scripts/mcp_mac_server.py` as a lightweight JSON-RPC stdin/stdout server.
  * Implement the AppleScript bridge to send iMessage updates to the user's phone.

---

## Phase 5: Markdown Approval Gate
*Goal: Build a remote approval mechanism for dangerous shell/git actions.*

- [ ] **Task 5.1: Tool Interception & Note Insertion**
  * Classify tools in the runner and intercept requests to execute "Dangerous" tools.
  * Pause execution and insert the markdown checklist (`- [ ] Approve Execution`) into the task note.
- [ ] **Task 5.2: Watcher Checkbox Polling & Resume**
  * Update the file watcher loop to scan the note for approval states. When the checkbox changes, capture the response and resume the agent.

---

## Phase 6: Lifecycle CLI & End-to-End Testing
*Goal: Build daemon management tools and test execution.*

- [ ] **Task 6.1: Command Line Daemon Manager**
  * Create the executable script `./bin/agent-sync` (supporting `start`, `stop`, `status`, and `logs` commands) using a terminal loop.
- [ ] **Task 6.2: End-to-End Test Suite**
  * Write test notes in Obsidian covering:
    * Simple query execution.
    * Google Calendar entry insertion.
    * Multi-line code writing task.
    * Dangerous command execution to verify sandbox bounds and approval blocks.
