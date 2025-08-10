FROM node:23-alpine AS build

WORKDIR /app

# Install required build tools + wget/tar
RUN apk add --no-cache python3 make g++ wget tar bzip2

COPY package.json package-lock.json ./

COPY . .

# Download and extract Pyodide
RUN mkdir -p pyodide_artifacts/pyodide \
  && wget https://github.com/pyodide/pyodide/releases/download/0.28.1/pyodide-0.28.1.tar.bz2 -O /tmp/pyodide.tar.bz2 \
  && bzip2 -t /tmp/pyodide.tar.bz2 \
  && tar -xjf /tmp/pyodide.tar.bz2 -C pyodide_artifacts/pyodide --strip-components=1 \
  && rm /tmp/pyodide.tar.bz2


RUN npm ci \
    && npm run build

FROM node:23-alpine AS production

WORKDIR /app

COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/dist ./dist
COPY --from=build /app/pyodide_artifacts ./pyodide_artifacts

RUN npm ci --omit=dev

EXPOSE 3000

CMD ["node", "dist/main"]
