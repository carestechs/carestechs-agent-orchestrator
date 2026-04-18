# Data Model Template

> **Purpose**: Document the domain entities, their fields, relationships, and module ownership. This provides AI with the structural understanding needed to generate data-layer tasks, validate feature scope, and ensure cross-module boundaries are respected.

---

## 1. Overview

### 1.1 Model Summary

[One paragraph describing the domain at a high level — what the system models, how many modules own data, and any key modeling decisions (soft deletes, UUIDs, multi-tenancy, etc.)]

### 1.2 Key Modeling Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| [Primary key strategy] | [e.g., UUIDs] | [Why] |
| [Soft vs hard deletes] | [e.g., Soft deletes with `deleted_at`] | [Why] |
| [Multi-tenancy approach] | [e.g., Shared DB with tenant column] | [Why] |
| [Timestamp handling] | [e.g., TIMESTAMPTZ, always UTC] | [Why] |
| [Cross-module references] | [e.g., Foreign IDs only, no navigation properties] | [Why] |

---

## 2. Module Ownership

> *Every entity belongs to exactly one module. Cross-module references use IDs only — no shared tables, no cross-module foreign keys at the ORM level.*

| Module | Entities Owned | DbContext |
|--------|---------------|-----------|
| [Module A] | [Entity1, Entity2] | [ModuleADbContext] |
| [Module B] | [Entity3, Entity4] | [ModuleBDbContext] |
| [Module C] | [Entity5, Entity6, Entity7] | [ModuleCDbContext] |

---

## 3. Entity Definitions

### 3.1 [Entity Name]

> *Module: [Owning Module] — [One-sentence description of what this entity represents]*

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| [id] | [UUID] | [PK] | [Primary key] |
| [field_1] | [string(100)] | [Required, Unique] | [What this field represents] |
| [field_2] | [string(500)] | [Optional] | [What this field represents] |
| [field_3] | [enum: ValueType] | [Required, Default: X] | [What this field represents] |
| [foreign_id] | [UUID] | [Required, FK → OtherEntity] | [Cross-module or same-module reference] |
| [created_at] | [timestamptz] | [Required, Auto] | [Record creation timestamp] |
| [updated_at] | [timestamptz] | [Required, Auto] | [Last modification timestamp] |
| [deleted_at] | [timestamptz] | [Optional] | [Soft delete marker] |

**Indexes:**
- [Unique index on `field_1`]
- [Composite index on `(foreign_id, created_at)`]

**Business Rules:**
- [Rule 1: e.g., "field_1 must be unique within the scope of foreign_id"]
- [Rule 2: e.g., "Cannot be deleted if referenced by active Entity3 records"]

---

### 3.2 [Entity Name]

> *Module: [Owning Module] — [One-sentence description]*

<!-- TODO: Repeat the field table, indexes, and business rules pattern for each entity -->

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| [id] | [UUID] | [PK] | [Primary key] |
| ... | ... | ... | ... |

---

## 4. Relationships

### 4.1 One-to-Many Relationships

| Parent Entity | Child Entity | Foreign Key | Cascade Behavior |
|---------------|-------------|-------------|-----------------|
| [Entity A] | [Entity B] | [entity_a_id on Entity B] | [Cascade delete / Restrict / Set null] |
| [Entity C] | [Entity D] | [entity_c_id on Entity D] | [Cascade / Restrict] |

### 4.2 Many-to-Many Relationships

| Entity A | Entity B | Join Table | Additional Fields |
|----------|----------|-----------|-------------------|
| [Entity X] | [Entity Y] | [entity_x_entity_y] | [role, assigned_at, etc.] |

<!-- TODO: Define join table fields if the relationship carries data (e.g., a role or timestamp) -->

### 4.3 Cross-Module References

> *These are ID-only references — no EF Core navigation properties, no database-level foreign keys across module boundaries.*

| Source Entity (Module) | Target Entity (Module) | Field | Purpose |
|----------------------|----------------------|-------|---------|
| [EntityB (Module A)] | [EntityX (Module B)] | [entity_x_id] | [Why this reference exists] |

---

## 5. Enums and Value Types

### 5.1 Enums

#### [EnumName]

> *Used by: [Entity.field]*

| Value | Description |
|-------|-------------|
| [Value1] | [What it means] |
| [Value2] | [What it means] |
| [Value3] | [What it means] |

<!-- TODO: Repeat for each enum used in the domain -->

### 5.2 Value Objects

> *Complex types that don't have their own identity (not separate tables).*

#### [ValueObjectName]

| Property | Type | Description |
|----------|------|-------------|
| [property_1] | [type] | [Description] |
| [property_2] | [type] | [Description] |

---

## 6. Database Conventions

> *Conventions applied uniformly across all entities and modules.*

| Convention | Rule | Example |
|------------|------|---------|
| [Table naming] | [e.g., snake_case, plural] | [task_items] |
| [Column naming] | [e.g., snake_case] | [due_date, created_at] |
| [Primary keys] | [e.g., UUID, column named `id`] | [id UUID PK] |
| [Timestamps] | [e.g., TIMESTAMPTZ, always present] | [created_at, updated_at] |
| [Soft deletes] | [e.g., nullable `deleted_at`] | [deleted_at TIMESTAMPTZ NULL] |
| [String lengths] | [e.g., explicit max lengths] | [name VARCHAR(200)] |

---

## 7. Entity-Relationship Diagram

> *ASCII or text-based overview of the full data model.*

```
<!-- TODO: Replace with actual entity-relationship diagram -->

┌──────────────┐       1:N       ┌──────────────┐
│  [Entity A]  │────────────────→│  [Entity B]  │
│              │                 │              │
│  - field_1   │                 │  - field_1   │
│  - field_2   │                 │  - entity_a_id│
└──────────────┘                 └──────┬───────┘
                                        │
                                        │ N:M (via join table)
                                        │
                                 ┌──────┴───────┐
                                 │  [Entity C]  │
                                 │              │
                                 │  - field_1   │
                                 └──────────────┘
```

---

## Usage Notes for AI Task Generation

When generating tasks from this document:

1. **Module boundaries**: Every data-access task must target the correct module's DbContext — never query across module boundaries
2. **Field completeness**: Generated entity classes must include all fields defined here with correct types and constraints
3. **Relationship integrity**: Ensure cascade behaviors and cross-module ID-only references are respected in migrations
4. **Enum consistency**: Use the enum values defined here — do not invent new values without updating this document
5. **Index awareness**: Include index creation in migration tasks for fields marked with indexes
6. **Naming conventions**: Table and column names must follow the conventions in Section 6
7. **Cross-module lookups**: When a task needs data from another module, generate a service call through the interface in Shared — not a direct DB query
