# Serialization Contract

How data is transformed between the Django backends (snake_case) and React frontends (camelCase).

## Automatic Conversion

Both backends use `djangorestframework-camel-case` which handles conversion transparently:

- **Incoming requests**: camelCase JSON keys from frontend are converted to snake_case before reaching Django views
- **Outgoing responses**: snake_case Python dict keys are converted to camelCase before sending to frontend

This is configured in `REST_FRAMEWORK` settings:

```python
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": (
        "djangorestframework_camel_case.render.CamelCaseJSONRenderer",
        "djangorestframework_camel_case.render.CamelCaseBrowsableAPIRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "djangorestframework_camel_case.parser.CamelCaseJSONParser",
        ...
    ),
}
```

## Excluded Keys

Certain keys are excluded from the camelCase conversion to prevent mangling:

```python
"JSON_UNDERSCOREIZE": {
    # Password fields: kept as-is because allauth expects exact field names
    "ignore_keys": (
        "password1", "password2",
        "new_password1", "new_password2",
        "old_password"
    ),
    # Dict keys under these fields are preserved from camelCase conversion
    # nodeStates has user-generated node IDs (e.g. '-ybkjiGpAUdvp01WwZodV_9')
    # that contain underscores - camelize() would mangle them
    "ignore_fields": ("nodeStates",),
}
```

## Socket.IO Events

Socket.IO events do **not** go through DRF's renderer pipeline. The backend manually applies camelCase conversion before emitting:

```python
from djangorestframework_camel_case.util import camelize

# Before emitting any Socket.IO event:
data = camelize(data)
await sio.emit('event_name', data, room=room)
```

**Event names** remain snake_case (e.g., `step_started`, `workflow_event`). Only **payload keys** are camelized.

## Frontend Conventions

- All TypeScript interfaces use camelCase property names
- API response types match the camelized output
- When sending data to the backend, use camelCase — the parser converts to snake_case automatically
- File uploads via `multipart/form-data` use snake_case field names (not processed by the JSON parser)

## Common Pitfalls

1. **Dict keys that look like snake_case**: If a dict has user-generated keys containing underscores (like node IDs), add the field name to `ignore_fields` to prevent mangling.

2. **Nested serializers**: The conversion is recursive — nested objects are also converted. No manual conversion needed.

3. **Socket.IO payloads**: Must manually call `camelize()` since Socket.IO bypasses DRF renderers. The `WebSocketResponseService` and workflow `EventEmitter` handle this.

4. **File upload fields**: `multipart/form-data` requests use the `MultiPartParser`, not the `CamelCaseJSONParser`. Field names in form data are NOT automatically converted — use snake_case for form fields.
