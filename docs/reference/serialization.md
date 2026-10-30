# Serialization

Use this to get key casing right between the Django backends (snake_case) and the React frontends (camelCase).

## Automatic conversion

Both backends use `djangorestframework-camel-case`, which converts keys for you:

- **Incoming requests.** camelCase JSON keys from the frontend become snake_case before they reach Django views.
- **Outgoing responses.** snake_case Python dict keys become camelCase before they reach the frontend.

The `REST_FRAMEWORK` setting wires this up:

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

## Excluded keys

These keys skip conversion so they are not mangled:

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

Socket.IO events do **not** go through DRF's renderer pipeline. The backend camelizes payloads itself before emitting:

```python
from djangorestframework_camel_case.util import camelize

# Before emitting any Socket.IO event:
data = camelize(data)
await sio.emit('event_name', data, room=room)
```

**Event names** stay snake_case (for example `step_started`, `workflow_event`). Only **payload keys** are camelized.

## Frontend conventions

- TypeScript interfaces use camelCase property names.
- API response types match the camelized output.
- Send camelCase to the backend; the parser converts it to snake_case.
- File uploads via `multipart/form-data` use snake_case field names (the JSON parser does not process them).

## Common pitfalls

1. **Dict keys that look like snake_case.** If a dict has user-generated keys with underscores (like node IDs), add the field name to `ignore_fields`.

2. **Nested serializers.** Conversion is recursive, so nested objects are converted too.

3. **Socket.IO payloads.** Call `camelize()` yourself, because Socket.IO bypasses DRF renderers. `WebSocketResponseService` and the workflow `EventEmitter` do this.

4. **File upload fields.** `multipart/form-data` requests use `MultiPartParser`, not `CamelCaseJSONParser`, so form field names are not converted. Use snake_case for them.
