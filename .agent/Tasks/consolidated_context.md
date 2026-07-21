# Consolidated Project Context

**Generated:** 2026-07-14T21:22:30.813724
**Project:** .

---

## 🎯 Features

### ✅ MVP Completion Candidate — 2026-07-14
- **Date:** unknown
- **Status:** completed
- **Report:** `.agent/Reports/2026-07-14-mvp-completion-candidate.md`

## 🏗️ System State

### Api Endpoints
- **File:** `.agent/System/api_endpoints.md`
- **Last Updated:** 2026-07-14T20:15:43.655193
- **Summary:** All routes are served by the single loopback FastAPI process. State-changing POST routes validate the local Host/Origin boundary. Questions and conversation content are never placed in update requests...

### Architecture
- **File:** `.agent/System/architecture.md`
- **Last Updated:** 2026-07-14T20:15:44.303144
- **Summary:** The current agent-facing architecture is maintained in [`project_architecture.md`](project_architecture.md). The authoritative approved decision record is [`docs/architecture.md`](../../docs/architect...

### Database Schema
- **File:** `.agent/System/database_schema.md`
- **Last Updated:** 2026-07-14T20:15:44.962739
- **Summary:** Local conversation header and legacy first-turn fields. `id` is the primary key; title, question, normalized question, serialized answer/model identity, corpus identity, creation/update timestamps, an...

### Project Architecture
- **File:** `.agent/System/project_architecture.md`
- **Last Updated:** 2026-07-14T20:25:23.059765
- **Summary:** Danish Immigration RAG is a local FastAPI web application for evidence-bounded answers about Danish permanent-residence language requirements and Danish examinations. Questions, retrieval, local model...

### Ux Guidelines
- **File:** `.agent/System/ux_guidelines.md`
- **Last Updated:** 2026-07-14T20:15:46.382310
- **Summary:** Keep the interface calm, conversation-first, and explicit about its information boundary. The app explains retrieved official information; it does not present itself as an authority, lawyer, or eligib...

## 📋 Active Tasks

### From context.md
**Last Updated:** 2026-07-14T20:54:01.459924

- Rerun the elevated Playwright suite and live Ollama supported-environment
- Obtain named curator/monitor/source-review evidence and rebuild the production
- Obtain independent human final-answer adjudication and final release-owner
- Run and record the required manual assistive-technology check.

---
*This consolidated context provides a snapshot of the project state.*
*Use this to understand recent work, active issues, and system architecture.*
