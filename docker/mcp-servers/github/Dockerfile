FROM node:20-alpine

# Install the GitHub MCP server globally
RUN npm install -g @modelcontextprotocol/server-github

# The server reads from stdin and writes to stdout (MCP protocol)
CMD ["mcp-server-github"]
