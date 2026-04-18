# API Specification Template

> **Purpose**: Document all REST API endpoints with their routes, methods, request/response shapes, authentication requirements, and status codes. This provides AI with the contract definitions needed to generate consistent backend controllers, frontend services, and integration tests.

---

## 1. Overview

### 1.1 API Summary

[One paragraph describing the API surface — how many modules expose endpoints, the authentication model, and the general response format.]

### 1.2 Key API Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| [Base path] | [e.g., `/api/`] | [Why] |
| [Versioning strategy] | [e.g., URL prefix `/api/v1/`] | [Why] |
| [Auth mechanism] | [e.g., JWT Bearer tokens] | [Why] |
| [Response envelope] | [e.g., `{ data, meta }`] | [Why] |
| [Error format] | [e.g., RFC 7807 Problem Details] | [Why] |
| [Pagination style] | [e.g., Offset with `page` + `pageSize`] | [Why] |

---

## 2. Common Conventions

### 2.1 Response Envelope

**Success Response:**
```json
{
  "data": { },
  "meta": {
    "totalCount": 0,
    "page": 1,
    "pageSize": 20
  }
}
```

<!-- TODO: Adjust envelope format to match your project conventions -->

**Single-item responses** omit the `meta` field unless pagination or extra metadata is relevant.

### 2.2 Error Response (RFC 7807)

```json
{
  "type": "https://tools.ietf.org/html/rfc7807",
  "title": "[Error Title]",
  "status": 400,
  "detail": "[Human-readable explanation]",
  "errors": {
    "[field]": ["[Validation message 1]", "[Validation message 2]"]
  }
}
```

### 2.3 Authentication

- **Mechanism**: [e.g., JWT Bearer token in `Authorization` header]
- **Token format**: `Authorization: Bearer <token>`
- **Unauthenticated endpoints**: [List any public endpoints]

### 2.4 Pagination

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| [page] | [int] | [1] | [Page number (1-based)] |
| [pageSize] | [int] | [20] | [Items per page (max 100)] |
| [sortBy] | [string] | [created_at] | [Field to sort by] |
| [sortDir] | [string] | [desc] | [Sort direction: asc or desc] |

---

## 3. Endpoints by Module

### 3.1 [Module Name]

#### [Resource Name]

##### [METHOD] [/api/path/{param}]

> *[One-sentence description of what this endpoint does]*

| Attribute | Value |
|-----------|-------|
| **Auth** | [Required / Public] |
| **Roles** | [Any / Admin / Owner] |

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| [param] | [UUID] | [Description] |

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| [filter] | [string] | [No] | [null] | [Description] |

**Request Body:**

```json
{
  "[field_1]": "[type — description]",
  "[field_2]": "[type — description]"
}
```

**Response (200 OK):**

```json
{
  "data": {
    "[field_1]": "[type]",
    "[field_2]": "[type]"
  }
}
```

**Status Codes:**

| Code | Condition |
|------|-----------|
| [200] | [Success] |
| [400] | [Validation error — see error response] |
| [401] | [Missing or invalid auth token] |
| [404] | [Resource not found] |

---

<!-- TODO: Repeat the endpoint block for each endpoint in this module -->

---

### 3.2 [Module Name]

<!-- TODO: Repeat the module section for each module that exposes API endpoints -->

#### [Resource Name]

##### [METHOD] [/api/path]

> *[Description]*

| Attribute | Value |
|-----------|-------|
| **Auth** | [Required / Public] |
| **Roles** | [Any / Admin] |

**Request Body:**

```json
{
  "[field]": "[type — description]"
}
```

**Response (201 Created):**

```json
{
  "data": {
    "[field]": "[type]"
  }
}
```

**Status Codes:**

| Code | Condition |
|------|-----------|
| [201] | [Resource created] |
| [400] | [Validation error] |
| [401] | [Unauthorized] |
| [409] | [Conflict — resource already exists] |

---

## 4. Shared DTOs

> *DTOs referenced by multiple endpoints or modules.*

### 4.1 [DTOName]

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| [field_1] | [string] | [No] | [Description] |
| [field_2] | [int] | [Yes] | [Description] |

<!-- TODO: Define shared DTOs used across modules — e.g., PaginationMeta, UserSummaryDto -->

---

## 5. Endpoint Summary

> *Quick reference table of all endpoints.*

| Method | Path | Module | Auth | Description |
|--------|------|--------|------|-------------|
| [GET] | [/api/resources] | [Module A] | [Required] | [List resources] |
| [POST] | [/api/resources] | [Module A] | [Required] | [Create resource] |
| [GET] | [/api/resources/{id}] | [Module A] | [Required] | [Get resource by ID] |
| [PUT] | [/api/resources/{id}] | [Module A] | [Required] | [Update resource] |
| [DELETE] | [/api/resources/{id}] | [Module A] | [Required] | [Delete resource] |

<!-- TODO: Add all endpoints here for a complete at-a-glance reference -->

---

## Usage Notes for AI Task Generation

When generating tasks from this document:

1. **Controller structure**: Each module section maps to a controller — generate controller tasks per module, not per endpoint
2. **DTO generation**: Request and response JSON shapes map directly to DTO classes — generate DTOs in the owning module
3. **Status codes**: Every endpoint must handle all listed status codes — include error-path tasks
4. **Auth requirements**: Respect the auth/roles column — generate middleware or attribute decorations accordingly
5. **Response envelope**: All responses must use the shared envelope format — never return raw entities
6. **Pagination**: Endpoints returning lists must support pagination parameters and return meta with totals
7. **Frontend alignment**: Frontend service tasks should mirror the endpoint signatures defined here — same paths, same request/response shapes
