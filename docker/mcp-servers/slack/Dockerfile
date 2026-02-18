FROM node:20-alpine

# Install the Slack MCP server globally
RUN npm install -g slack-mcp-server@latest

# The server reads from stdin and writes to stdout (MCP protocol)
CMD ["slack-mcp-server"]

