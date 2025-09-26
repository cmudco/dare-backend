# Conditional Node Implementation Plan

## Overview
This document outlines the implementation plan for adding a new Conditional Node type to the workflow system. The Conditional Node acts as a decision point that routes workflow execution based on AI evaluation of input data.

## 🎯 Feature Requirements

### Core Functionality
- **Single Input Connection**: Accepts output from one step or aggregator node
- **Dual Output Routes**: Two user-configurable output paths
- **AI-Powered Decision**: Uses LLM to evaluate input and choose route
- **Custom Evaluation Prompt**: User-defined criteria for decision making
- **Structured Response**: Forces LLM to provide structured routing decision

### Key Differences from Aggregator Node
- **Input Limitation**: Only one input connection (vs multiple for aggregator)
- **User-Defined Routes**: Custom route names (vs fixed true/false or good/bad/average)
- **Step-Only Outputs**: Can only connect to step nodes (not other node types)
- **Simpler Evaluation**: Binary decision making with custom criteria

## 📋 Implementation Tasks

### Backend Implementation

#### 1. Data Model Creation
- **Create ConditionalNodeData Model**
  - Custom evaluation prompt field
  - Route A name and description
  - Route B name and description
  - Step number for execution ordering
  - Validation rules for route names
- **Add to Generic Foreign Key System**
  - Integrate with existing WorkflowNode data_object pattern
  - Add content type registration
- **Database Migration**
  - Create new model table
  - Add model to admin interface

#### 2. Node Handler Implementation
- **Create ConditionalNodeHandler Class**
  - Inherit from BaseNodeHandler
  - Implement single input validation
  - Handle AI evaluation with structured prompts
  - Parse routing decisions from LLM response
  - Support custom route naming
- **Register Handler**
  - Add to NodeHandlerRegistry
  - Implement can_handle method for 'conditional' type

#### 3. API Integration
- **Extend Serializers**
  - Add ConditionalNodeDataSerializer
  - Update WorkflowNodeSerializer to handle conditional type
  - Implement data normalization for route configurations
- **Update Validation**
  - Single input connection validation
  - Output connection restrictions (steps only)
  - Route name uniqueness validation

### Frontend Implementation

#### 4. React Component Development
- **Create ConditionalNode Component**
  - Single input handle on left side
  - Two configurable output handles on right side
  - Route name input fields
  - Custom evaluation prompt textarea
  - Visual indication of routing logic
- **Handle Management**
  - Dynamic input handle positioning
  - User-configurable output handle labels
  - Connection validation for step-only outputs
- **State Management**
  - Local state for route configurations
  - Redux integration for data persistence
  - Error handling and validation feedback

#### 5. Workflow Builder Integration
- **Node Type Registration**
  - Add conditional node to available node types
  - Create node creation interface
  - Add to node palette/toolbar
- **Connection Logic**
  - Implement input connection limits
  - Validate output connection types
  - Handle edge pruning on configuration changes
- **Visual Design**
  - Unique icon and styling for conditional nodes
  - Color coding for different route outputs
  - Status indicators for execution state

### Execution Engine Updates

#### 6. Workflow Execution Logic
- **Routing Decision Processing**
  - Parse LLM structured responses
  - Map decisions to user-defined route names
  - Handle routing failures and fallbacks
- **Execution Flow Control**
  - Skip non-selected route branches
  - Continue execution on selected route
  - Update workflow run status appropriately
- **Error Handling**
  - Invalid routing decisions
  - Missing input data scenarios
  - LLM service failures

## 🔧 Technical Implementation Details

### Data Structure Design

#### ConditionalNodeData Fields
- `custom_prompt`: Text field for evaluation criteria
- `route_a_name`: CharField for first route label
- `route_b_name`: CharField for second route label
- `route_a_description`: Optional description for route A
- `route_b_description`: Optional description for route B
- `step_number`: Integer for execution ordering
- Standard timestamp fields (created_at, updated_at)

#### LLM Prompt Structure
```
{custom_prompt}

Based on the following input, choose one of these routes:
- Route A ({route_a_name}): {route_a_description}
- Route B ({route_b_name}): {route_b_description}

Input to evaluate:
{input_data}

Provide your reasoning and end with: ROUTING_DECISION: [{route_a_name}|{route_b_name}]
```

### Connection Validation Rules
- **Input Restrictions**: Maximum one incoming connection
- **Input Sources**: Accept from step nodes or aggregator nodes
- **Output Restrictions**: Only connect to step nodes
- **Route Uniqueness**: Route names must be different

### User Interface Design
- **Configuration Panel**: Route name inputs and prompt textarea
- **Visual Handles**: Clearly labeled output handles with user-defined names
- **Status Indicators**: Show which route was selected during execution
- **Validation Feedback**: Real-time validation of configurations

## 🧪 Testing Strategy

### Unit Tests
- ConditionalNodeData model validation
- ConditionalNodeHandler execution logic
- Serializer data transformation
- Connection validation rules

### Integration Tests
- End-to-end workflow execution with conditional nodes
- Frontend-backend data synchronization
- Error handling scenarios
- Edge case routing decisions

### User Acceptance Tests
- Node creation and configuration workflow
- Visual feedback and status indicators
- Complex workflow scenarios with multiple conditionals
- Performance testing with large workflows

## 🚀 Implementation Timeline

### Phase 1: Backend Foundation (Week 1)
- Create ConditionalNodeData model and migrations
- Implement ConditionalNodeHandler class
- Add serializer support and API endpoints
- Basic admin interface integration

### Phase 2: Frontend Component (Week 2)
- Develop ConditionalNode React component
- Implement configuration interface
- Add connection validation logic
- Integration with workflow builder

### Phase 3: Execution Integration (Week 3)
- Update workflow execution engine
- Implement routing logic and decision parsing
- Add error handling and fallback mechanisms
- Performance optimization and testing

### Phase 4: Testing & Polish (Week 4)
- Comprehensive testing suite
- User interface refinements
- Documentation and examples
- Performance testing and optimization

**Total Estimated Time: 4 weeks**

## 📊 Success Criteria

### Functional Requirements
- ✅ Users can create conditional nodes with custom route names
- ✅ Single input connection enforced
- ✅ AI-powered routing decisions work reliably
- ✅ Only step nodes can connect to outputs
- ✅ Custom evaluation prompts function correctly

### Quality Requirements
- ✅ 95%+ routing decision accuracy
- ✅ Sub-second routing decision time
- ✅ Comprehensive error handling
- ✅ Intuitive user interface
- ✅ 90%+ test coverage

### User Experience
- ✅ Simple node configuration process
- ✅ Clear visual feedback on routing decisions
- ✅ Helpful error messages and validation
- ✅ Consistent with existing node patterns
- ✅ Responsive and performant interface

## 🔮 Future Enhancements

### Advanced Features
- **Multiple Route Support**: Extend beyond two routes
- **Conditional Chaining**: Link multiple conditional nodes
- **Route Probability**: Show confidence scores for decisions
- **Historical Analytics**: Track routing decision patterns
- **A/B Testing**: Compare different routing strategies

### Integration Opportunities
- **External Data Sources**: Route based on external API data
- **User Input**: Interactive routing with human decisions
- **Scheduled Routing**: Time-based conditional logic
- **ML Model Integration**: Custom trained routing models