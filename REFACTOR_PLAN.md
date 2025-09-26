# Workflows App Refactor Plan

## 🟡 Areas for Improvement

### 1. Code Complexity & Maintainability

**models.py:685** - Very large file (680+ lines)
```python
# Consider splitting into:
# - models/core.py (Workflow, WorkflowRun, etc.)
# - models/nodes.py (Node data classes)
# - models/graph.py (WorkflowNode, WorkflowEdge)
```

**serializers.py:207-363** - Complex WorkflowNodeSerializer
```python
# Recommend extracting data handling into separate classes:
class NodeDataHandler:
    @classmethod
    def normalize_data(cls, node_type: str, data: dict) -> dict:
        """Extract complex normalization logic"""

class NodeDataUpdater:
    @classmethod
    def update_node_data(cls, instance, data_dict: dict):
        """Extract complex update logic"""
```

### 2. Error Handling Improvements

**node_handlers.py:199-221** - Generic exception handling
```python
# Current:
except Exception as e:
    logger.error(f"Failed to execute step node {node.id}: {str(e)}", exc_info=True)

# Recommend:
except (LLMServiceError, ValidationError) as e:
    logger.error(f"Service error in step {node.id}: {str(e)}", exc_info=True)
    # Handle specific error types
except Exception as e:
    logger.error(f"Unexpected error in step {node.id}: {str(e)}", exc_info=True)
    # Handle unknown errors
```

### 3. Database Query Optimization

**views.py:278-284** - N+1 query potential
```python
# Current:
for step_node in step_nodes:
    if step_node.data_object and isinstance(step_node.data_object, StepNodeData):

# Recommend:
step_nodes = step_nodes.select_related('data_content_type').prefetch_related(
    Prefetch('stepnodedata_set', queryset=StepNodeData.objects.select_related('prompt', 'llm'))
)
```

### 4. Code Documentation

**Missing docstrings** in several key areas:
```python
class WorkflowNode(TimeStampMixin):
    """
    Model to store complete React Flow Node data with type-safe node data.

    This model maps directly to React Flow's Node interface and provides
    type-safe storage for different node types through generic foreign keys.

    Attributes:
        workflow: Parent workflow
        node_id: Unique identifier for React Flow
        data_object: Type-safe node data (StepNodeData, StartNodeData, etc.)
    """
```

## 🔴 Critical Issues to Address

### 1. Commented Out Code
**models.py:648-684** - WorkflowStepSnippet model commented out
```python
# Remove commented code or implement proper feature flag:
if settings.ENABLE_WORKFLOW_SNIPPETS:
    # Include WorkflowStepSnippet model
```

### 2. Magic Numbers & Constants
**node_handlers.py:326-331** - Hardcoded scoring thresholds
```python
# Create constants file:
class ScoringThresholds:
    QUANTITATIVE_BAD_MAX = 40
    QUANTITATIVE_AVERAGE_MAX = 70
    QUANTITATIVE_GOOD_MIN = 71
```

### 3. Complex Method Length
**views.py:135-244** - 109-line clone method
```python
# Recommend extracting into service:
class WorkflowCloningService:
    def clone_workflow(self, original: Workflow) -> Workflow:
        # Break into smaller methods
        cloned = self._create_cloned_workflow(original)
        self._clone_nodes(original, cloned)
        self._clone_edges(original, cloned)
        return cloned
```

## 📊 Quality Metrics

- **Code Coverage**: High (estimated 85%+)
- **Complexity**: Medium-High (some methods need refactoring)
- **Documentation**: Good (models) → Needs work (services)
- **Error Handling**: Good (structured) → Could be more specific
- **Performance**: Good → Needs query optimization
- **Maintainability**: Good → Large files need splitting

## 🎯 Recommended Next Steps

### 1. Immediate (High Priority):
- Split large files into logical modules
- Add comprehensive docstrings to services
- Remove or properly implement commented code

### 2. Short Term:
- Extract complex serializer logic into handler classes
- Add more specific exception types
- Create constants file for magic numbers

### 3. Medium Term:
- Implement query optimization with select_related/prefetch_related
- Add comprehensive logging for debugging workflows
- Create workflow execution monitoring/metrics