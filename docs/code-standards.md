# Code Standards

Consolidated coding conventions for the DARE platform. For detailed rules, see the project-specific files:
- `dare-backend/rules.md` — Backend quality rules
- `dare-frontend/docs/RULES.md` — Frontend development rules
- `socraticbooks-backend/rules.md` — SocraticBooks backend rules
- `socraticbooks-react/rules.md` — SocraticBooks frontend rules

## Backend (Python / Django)

### Formatting
- **Black** (line length 88) for code formatting
- **isort** for import sorting
- Run `black . && isort .` before committing

### Architecture
- **DTOs over parameter sprawl**: 5+ parameters to a function -> use a frozen `@dataclass`
- **No `Dict[str, Any]` returns**: Use typed dataclasses for all return values
- **Services use ABCs + factory functions**: e.g., `AIService` ABC with `get_ai_service()` factory
- **Constants in `constants.py`**: Use `TextChoices` classes, never inline string literals
- **All imports at module top**: Fix circular imports via dependency injection, not inline imports

### Models
- New models inherit from `BaseModel` (combines `TimeStampMixin`, `IsActiveMixin`, `IsDeletedMixin`)
- Use `help_text` on all fields, `related_name` on all foreign keys
- Implement `__str__` on all models
- Use `ActiveObjectsManager` for filtered querysets (`Model.active_objects`)

### API Layer
- ViewSets override `get_queryset()` to filter by user
- Override `perform_create()` to set user ownership
- Use `@action` decorator for custom endpoints
- Wrap user-facing strings with `gettext_lazy`
- Camelize nested data before Socket.IO emission

### Error Handling
- Use `DareApiError` for inter-service errors
- Log exceptions with `logger.error()`, never translate logged messages
- Return user-friendly error messages in API responses

## Frontend (TypeScript / React)

### TypeScript
- **Interfaces over type aliases** for object shapes
- **No `any`**: Use explicit types everywhere
- **No `Record<string, T>`**: Define explicit interfaces
- **Enums in `utils/constants/`**: Never use string literals for type discrimination

### React
- Functional components only, one component per file
- Custom hooks for reusable logic
- **No `.css` files**: Tailwind only; `@apply` sparingly in `index.css`
- Use `cn()` utility for conditional class merging

### State Management
- **dare-frontend**: Redux Toolkit with `createSlice` + `createAsyncThunk`
- **socraticbooks-react**: Zustand for global state, TanStack Query for server state
- Type-safe hooks: `useAppSelector`, `useAppDispatch`

### API Integration Cycle
```
Component → dispatch(asyncThunk) → api/ function → Backend → slice handles thunk states
```

### Socket.IO
- All incoming workflow events validated with Zod schemas (`src/schemas/workflowSocket.ts`)
- Two separate middlewares: `socketMiddleware.ts` (/chat) and `workflowSocketMiddleware.ts` (/workflow)
- Event names are snake_case; payload keys are camelCase

### Forms
- **dare-frontend**: Formik + Yup
- **socraticbooks-react**: React Hook Form + Zod

## Git Workflow

- Branch naming: `[Name]/[Feature|Fix|Refactor]/[Description]`
- Run formatters and linters before committing
- Always create migrations for database changes
