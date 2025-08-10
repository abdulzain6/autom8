# Use the official Caddy builder image to compile our custom version
FROM caddy:2-builder AS builder

# Use xcaddy to build Caddy with the required layer4 module
RUN xcaddy build \
    --with github.com/mholt/caddy-l4

# Create the final, small production image
FROM caddy:2

# Copy our custom-built Caddy binary from the builder stage
COPY --from=builder /usr/bin/caddy /usr/bin/caddy