# System Architecture Template

> **Purpose**: Document the technical architecture of the system including components, data flow, and technology choices. This provides AI with structural understanding for generating technically sound tasks.

---

## 1. Overview

### 1.1 System Summary

[One paragraph describing what the system does and its primary architectural style (monolith, microservices, serverless, etc.)]

### 1.2 Key Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| [Architecture style] | [Choice] | [Why] |
| [Primary language] | [Choice] | [Why] |
| [Database type] | [Choice] | [Why] |
| [Hosting approach] | [Choice] | [Why] |
| [Communication pattern] | [Choice] | [Why] |

---

## 2. Technology Stack

### 2.1 Frontend

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| [Framework] | [e.g., React] | [Version] | [Core UI framework] |
| [State Management] | [e.g., Redux] | [Version] | [Application state] |
| [Styling] | [e.g., Tailwind] | [Version] | [Styling approach] |
| [Build Tool] | [e.g., Vite] | [Version] | [Build/bundling] |

### 2.2 Backend

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| [Runtime] | [e.g., Node.js] | [Version] | [Execution environment] |
| [Framework] | [e.g., Express] | [Version] | [HTTP/API framework] |
| [ORM/DB Client] | [e.g., Prisma] | [Version] | [Database access] |
| [Validation] | [e.g., Zod] | [Version] | [Input validation] |

### 2.3 Data Storage

| Store | Technology | Purpose | Data Types |
|-------|------------|---------|------------|
| [Primary DB] | [e.g., PostgreSQL] | [Transactional data] | [Entity types] |
| [Cache] | [e.g., Redis] | [Session/cache] | [Cached data types] |
| [File Storage] | [e.g., S3] | [Static assets] | [File types] |
| [Search] | [e.g., Elasticsearch] | [Full-text search] | [Indexed content] |

### 2.4 Infrastructure

| Component | Technology | Purpose |
|-----------|------------|---------|
| [Hosting] | [e.g., AWS ECS] | [Application hosting] |
| [CDN] | [e.g., CloudFront] | [Static asset delivery] |
| [DNS] | [e.g., Route53] | [Domain management] |
| [Secrets] | [e.g., AWS Secrets Manager] | [Credential storage] |

---

## 3. Component Architecture

### 3.1 High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        [Clients]                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  Web App │  │Mobile App│  │ [Other]  │                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
└───────┼─────────────┼─────────────┼─────────────────────────┘
        │             │             │
        └─────────────┼─────────────┘
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                     [API Gateway]                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  [Gateway/Load Balancer]                             │   │
│  │  - Authentication                                    │   │
│  │  - Rate Limiting                                     │   │
│  │  - Request Routing                                   │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  [Service A] │ │  [Service B] │ │  [Service C] │
│              │ │              │ │              │
│  - [Func 1]  │ │  - [Func 1]  │ │  - [Func 1]  │
│  - [Func 2]  │ │  - [Func 2]  │ │  - [Func 2]  │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                     [Data Layer]                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │ [DB 1]   │  │ [Cache]  │  │ [Queue]  │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Component Descriptions

#### [Component/Service A]

- **Purpose**: [What this component does]
- **Responsibilities**:
  - [Responsibility 1]
  - [Responsibility 2]
  - [Responsibility 3]
- **Key Dependencies**: [Component B], [External Service X]
- **Data Owned**: [Entity types this component manages]

#### [Component/Service B]

- **Purpose**: [What this component does]
- **Responsibilities**:
  - [Responsibility 1]
  - [Responsibility 2]
- **Key Dependencies**: [Component C]
- **Data Owned**: [Entity types]

---

## 4. Data Flow

### 4.1 Primary User Flow: [Flow Name]

```
[User] → [Client] → [API Gateway] → [Service A] → [Database]
                                  ↓
                           [Service B] (async)
                                  ↓
                           [External API]
```

**Step-by-step:**
1. [User initiates action]
2. [Client sends request to...]
3. [API Gateway validates and routes to...]
4. [Service processes and persists...]
5. [Async event triggers...]
6. [External service called for...]

### 4.2 [Secondary Flow Name]

[Repeat pattern for other significant flows]

---

## 5. Integration Points

### 5.1 External Services

| Service | Purpose | Protocol | Auth Method |
|---------|---------|----------|-------------|
| [Service 1] | [Purpose] | REST/GraphQL/gRPC | [OAuth/API Key/etc.] |
| [Service 2] | [Purpose] | [Protocol] | [Auth] |
| [Service 3] | [Purpose] | [Protocol] | [Auth] |

### 5.2 Internal Communication

| From | To | Protocol | Pattern |
|------|-----|----------|---------|
| [Component A] | [Component B] | [HTTP/gRPC/Queue] | [Sync/Async] |
| [Component B] | [Component C] | [Protocol] | [Pattern] |

---

## 6. Security Architecture

### 6.1 Authentication

- **Method**: [JWT/Session/OAuth/etc.]
- **Token Storage**: [Where tokens are stored]
- **Refresh Strategy**: [How tokens are refreshed]

### 6.2 Authorization

- **Model**: [RBAC/ABAC/etc.]
- **Enforcement Point**: [Where authorization is checked]
- **Roles Defined**: [List of roles]

### 6.3 Data Protection

| Data Type | At Rest | In Transit | Access Control |
|-----------|---------|------------|----------------|
| [User PII] | [Encryption type] | [TLS version] | [Who can access] |
| [Credentials] | [Encryption type] | [Protocol] | [Access pattern] |
| [Business Data] | [Encryption type] | [Protocol] | [Access pattern] |

---

## 7. Scalability Considerations

### 7.1 Current Capacity

- **Expected concurrent users**: [Number]
- **Expected requests/second**: [Number]
- **Expected data volume**: [Size]

### 7.2 Scaling Strategy

| Component | Scaling Type | Trigger | Limit |
|-----------|-------------|---------|-------|
| [Web servers] | Horizontal | [CPU > X%] | [Max instances] |
| [Database] | Vertical/Read replicas | [Connections > X] | [Max size] |
| [Cache] | [Type] | [Trigger] | [Limit] |

---

## 8. Development & Deployment

### 8.1 Repository Structure

```
/
├── [frontend-dir]/          # Frontend application
├── [backend-dir]/           # Backend services
├── [shared-dir]/            # Shared code/types
├── [infrastructure-dir]/    # IaC definitions
└── [docs-dir]/              # Documentation
```

### 8.2 Environment Strategy

| Environment | Purpose | Data | Access |
|-------------|---------|------|--------|
| Local | Development | Mocked/seeded | Developers |
| Dev/Staging | Integration testing | Synthetic | Team |
| Production | Live system | Real | End users |

---

## Usage Notes for AI Task Generation

When generating architecture-related tasks:

1. **Component boundaries**: Respect service ownership - don't generate tasks that blur responsibilities
2. **Data flow**: Ensure generated tasks maintain established data flow patterns
3. **Technology choices**: Use only technologies in the stack unless migration is explicitly needed
4. **Security**: All generated tasks must respect the security architecture
5. **Scalability**: Consider scaling implications in design tasks
6. **Integration**: Reference integration points for external service tasks
