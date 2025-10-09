import { Server } from "proxy-chain";

// Interface for the proxy server (kept for completeness)
export interface IProxyServer {
  readonly url: string;
  readonly upstreamProxyUrl: string;
  readonly txBytes: number;
  readonly rxBytes: number;
  listen(): Promise<void>;
  close(force?: boolean): Promise<void>;
}

export class ProxyServer extends Server implements IProxyServer {
  public url: string;
  public upstreamProxyUrl: string;
  public txBytes = 0;
  public rxBytes = 0;

  constructor(proxyUrl: string) {
    const bindAddress = process.env.PROXY_BIND_ADDRESS || '127.0.0.1';
    const port = parseInt(process.env.PROXY_PORT || '0', 10);

    super({
      port,  // Use env port or 0 for random

      prepareRequestFunction: (options) => {
        const { hostname } = options;

        // Default internal bypass hosts (localhost, etc.)
        const internalBypassTests = new Set(["0.0.0.0", "127.0.0.1", "localhost"]);

        // Add env-based bypass hosts
        const bypassEnv = process.env.PROXY_INTERNAL_BYPASS || '';
        for (const host of bypassEnv.split(',')) {
          if (host.trim()) {
            internalBypassTests.add(host.trim());
          }
        }

        const isInternalBypass = internalBypassTests.has(hostname);

        if (isInternalBypass) {
          console.log(`Bypassing proxy for internal host: ${hostname}`);
          // Direct connection (no upstream proxy)
          return { upstreamProxyUrl: null };
        }

        // Forward to upstream proxy
        return {
          upstreamProxyUrl: proxyUrl,
        };
      },
    });

    this.on("connectionClosed", ({ stats }) => {
      // Accumulate traffic stats (trgTxBytes/RxBytes for target/upstream)
      if (stats) {
        this.txBytes += stats.trgTxBytes || 0;
        this.rxBytes += stats.trgRxBytes || 0;
      }
    });

    this.upstreamProxyUrl = proxyUrl;
    this.url = `http://${bindAddress}:${this.port}`;  // Updated after listen
  }

  async listen(): Promise<void> {
    await super.listen();
    const bindAddress = process.env.PROXY_BIND_ADDRESS || '127.0.0.1';
    this.url = `http://${bindAddress}:${this.port}`;
    console.log(`\n=== Proxy Server Started ===`);
    console.log(`Local URL: ${this.url}`);
    console.log(`Upstream Proxy: ${this.upstreamProxyUrl}`);
    console.log(`Bypass Hosts: ${process.env.PROXY_INTERNAL_BYPASS || 'localhost,127.0.0.1'}`);
    console.log(`Press Ctrl+C to stop.\n`);
  }

  async close(force?: boolean): Promise<void> {
    console.log('\nShutting down proxy server...');
    await super.close(force ?? false);
    console.log(`Final Stats: TX=${this.txBytes} RX=${this.rxBytes} bytes`);
    console.log('Proxy closed.');
  }
}

// Standalone main function
async function main() {
  // Use env or default upstream (format: [user:]pass@host:port)
  const upstreamProxy = process.env.UPSTREAM_PROXY;
  if (!upstreamProxy) {
    console.error('Error: UPSTREAM_PROXY environment variable is not set.');
    process.exit(1);
  }
  
  const proxyServer = new ProxyServer(upstreamProxy);
  await proxyServer.listen();

  // Graceful shutdown
  process.on('SIGINT', async () => {
    await proxyServer.close();
    process.exit(0);
  });

  // Optional: Log stats periodically (every 30s)
  setInterval(() => {
    console.log(`Current Stats: TX=${proxyServer.txBytes} RX=${proxyServer.rxBytes} bytes`);
  }, 30000);
}

// Run if this file is executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    console.error('Proxy server error:', error);
    process.exit(1);
  });
}