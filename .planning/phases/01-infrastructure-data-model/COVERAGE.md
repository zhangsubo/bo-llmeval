# Phase 1 — API Coverage Declaration

No external API integration: Phase 1 builds the platform's OWN internal REST API (`/healthz`, `/readyz`, `/api/v1/suppliers`) on FastAPI + PostgreSQL; it consumes no third-party API/SDK/service — the detector fired on the word "API" in success criterion 4 ("基础 CRUD API 端点可通过 Postman/curl 调用"), which refers to the platform's own endpoints.
