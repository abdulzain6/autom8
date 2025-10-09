# Dockerfile for TypeScript Proxy Server (using proxy-chain)
FROM node:20-slim

# Set working directory
WORKDIR /app

# Copy package files (for dep install)
COPY package.json tsconfig.json ./

# Install dependencies (production + dev for build)
RUN npm install

# Copy source code
COPY src/ ./src/

# Build TypeScript to JavaScript (ESM output)
RUN npm run build

# Expose default port 3000
EXPOSE 3000

ENV PORT=3000
CMD ["node", "dist/index.js"]