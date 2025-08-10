import { parentPort, workerData } from 'worker_threads';
import { loadPyodide, PyodideAPI, } from 'pyodide';
import { LRUCache } from 'lru-cache';
import * as fs from 'fs';
import * as path from 'path';

// --- Type Augmentation for Pyodide FS ---
declare module 'pyodide' {
  export interface FSType {
    readFile(path: string, opts?: { encoding?: 'utf8' | 'binary' }): string | Uint8Array;
    analyzePath(path: string, nofollow?: boolean): { exists: boolean; };
  }
}

interface PyodideSessionContext {
    id: string;
    globals: any;
    createdAt: Date;
}

let pyodide: PyodideAPI;
const workerId = workerData?.workerId ?? 'unknown';

// A promise-based lock to serialize filesystem operations within a single worker
let executionChain = Promise.resolve();

const SESSIONS_BASE_PATH = './pyodide_sessions';
const VIRTUAL_SESSIONS_PATH = '/sessions';

const sessions = new LRUCache<string, PyodideSessionContext>({
    max: 100, // Max sessions per worker
    ttl: 1000 * 60 * 60, // 1 hour
    dispose: (value, key) => {
        console.log(`[Worker #${workerId}] Disposing session ${key}.`);
        try {
            if (value.globals && typeof value.globals.destroy === 'function') {
                value.globals.destroy();
            }
            const sessionHostPath = path.join(SESSIONS_BASE_PATH, key);
            if (fs.existsSync(sessionHostPath)) {
                fs.rmSync(sessionHostPath, { recursive: true, force: true });
            }
        } catch (e) {
            console.error(`[Worker #${workerId}] Error during disposal of session ${key}:`, e);
        }
    },
});

async function initializePyodide() {
    console.log(`[Worker #${workerId}] Initializing shared Pyodide instance...`);
    const startTime = Date.now();

    const pyodideLoadStart = Date.now();
    pyodide = await loadPyodide({
        indexURL: path.resolve(process.cwd(), 'pyodide_artifacts/pyodide'),
        stdout: (text) => console.log(`[Pyodide STDOUT #${workerId}] ${text}`),
        stderr: (text) => console.error(`[Pyodide STDERR #${workerId}] ${text}`),
    });
    console.log(`[Timing #${workerId}] Pyodide loaded in ${Date.now() - pyodideLoadStart} ms`);

    const packagesLoadStart = Date.now();
    await pyodide.loadPackage([ "micropip", "matplotlib", "numpy", "pandas", "scipy", "sympy", "statsmodels", "networkx", "gmpy2", "mpmath", "shapely" ]);
    console.log(`[Timing #${workerId}] Packages loaded in ${Date.now() - packagesLoadStart} ms`);

    // Ensure the base directory for all sessions exists on the host.
    fs.mkdirSync(SESSIONS_BASE_PATH, { recursive: true });

    // Create the virtual base directory within this worker's Pyodide FS.
    pyodide.FS.mkdirTree(VIRTUAL_SESSIONS_PATH);
    
    console.log(`[Timing #${workerId}] Total initialization time: ${Date.now() - startTime} ms`);
    parentPort!.postMessage({ type: 'ready' });
}

parentPort!.on('message', async (msg) => {
    try {
        switch (msg.type) {
            case 'createSession': {
                const { sessionId } = msg;
                // LOG: Log session creation on this worker
                console.log(`[Worker ${workerId}] Creating session [${sessionId}] as instructed by main service.`);
                const virtualSessionPath = `${VIRTUAL_SESSIONS_PATH}/${sessionId}`;
                const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);

                pyodide.FS.mkdirTree(virtualSessionPath);
                fs.mkdirSync(sessionHostPath, { recursive: true });

                const sessionGlobals = pyodide.runPython('{}');
                const sessionContext: PyodideSessionContext = { id: sessionId, globals: sessionGlobals, createdAt: new Date() };
                sessions.set(sessionId, sessionContext);
                
                parentPort!.postMessage({ type: 'sessionCreated', taskId: msg.taskId, sessionId });
                break;
            }

            case 'uploadFile': {
                const { sessionId, file } = msg;
                if (!sessions.has(sessionId)) throw new Error(`Session with ID "${sessionId}" not found on worker #${workerId}.`);
                
                const filename = path.basename(file.originalname);
                if (filename !== file.originalname) throw new Error('Invalid filename. Subdirectories are not allowed.');

                const hostFilePath = path.join(SESSIONS_BASE_PATH, sessionId, filename);
                const virtualFilePath = path.join(VIRTUAL_SESSIONS_PATH, sessionId, filename);
                
                // FIX: The `file.buffer` is already a Buffer-like object (or transferable).
                const fileBuffer = Buffer.from(file.buffer);

                fs.writeFileSync(hostFilePath, fileBuffer);
                pyodide.FS.writeFile(virtualFilePath, fileBuffer);

                parentPort!.postMessage({ type: 'fileUploaded', taskId: msg.taskId, filename });
                break;
            }
            case 'executeCode': {
                const { sessionId, code, taskId } = msg;
                if (!sessions.has(sessionId)) {
                    // ERROR LOG: Be specific about which worker failed
                    throw new Error(`Session with ID "${sessionId}" not found on worker [${workerId}].`);
                }
                executionChain = executionChain.then(async () => {
                    const session = sessions.get(sessionId);
                    if (!session) throw new Error(`Session with ID "${sessionId}" not found on worker #${workerId}.`);
                    
                    const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);
                    const sessionVirtualPath = path.join(VIRTUAL_SESSIONS_PATH, sessionId);

                    try {
                        pyodide.mountNodeFS(sessionVirtualPath, sessionHostPath);
                        pyodide.FS.chdir(sessionVirtualPath);

                        const result = await pyodide.runPythonAsync(code, { globals: session.globals });
                        const toJsResult = typeof result?.toJs === 'function' ? result.toJs({ create_proxies: false }) : result;
                        parentPort!.postMessage({ type: 'executionComplete', taskId: taskId, result: toJsResult });

                    } finally {
                        pyodide.FS.unmount(sessionVirtualPath);
                        pyodide.FS.chdir('/');
                    }
                }).catch(error => {
                    if (parentPort) {
                        parentPort.postMessage({ type: 'error', taskId: taskId, error: error.message });
                    }
                });
                break;
            }
            case 'downloadFile': {
                const { sessionId, path: userPath } = msg;
                if (!sessions.has(sessionId)) {
                    throw new Error(`Session with ID "${sessionId}" not found.`);
                }

                // --- FIX: Robust Path Resolution and Security Check ---

                // 1. Define the user's sandbox/jail root in the virtual filesystem.
                const sessionVirtualRoot = path.join(VIRTUAL_SESSIONS_PATH, sessionId);

                // 2. Resolve the user's requested path against their sandbox root.
                //    `path.join` handles creating a full path from a relative `userPath`.
                //    `path.normalize` resolves segments like `.` and `..`.
                const requestedVirtualPath = path.normalize(path.join(sessionVirtualRoot, userPath));

                // 3. SECURITY: The crucial check. Ensure the final, resolved path is still
                //    inside the user's designated session directory. This prevents all
                //    forms of path traversal attacks (e.g., `../other_session_id/file.txt`).
                if (!requestedVirtualPath.startsWith(sessionVirtualRoot)) {
                    throw new Error('Path traversal attempt detected. Access denied.');
                }

                // 4. Verify the file actually exists at the safe, resolved path.
                //    Use `analyzePath` for a safe, non-destructive existence check.
                if (!(pyodide.FS as any).analyzePath(requestedVirtualPath).exists) {
                    throw new Error(`File not found at path: "${userPath}"`);
                }

                // 5. Read the file using the safe, absolute virtual path.
                //    FIX: Cast to `any` to bypass incomplete type definitions.
                const data: Uint8Array = (pyodide.FS as any).readFile(requestedVirtualPath, { encoding: 'binary' });
                // Post the data back as a Buffer, which is expected by the main thread.
                parentPort!.postMessage({ type: 'fileDownloaded', taskId: msg.taskId, data: Buffer.from(data) });
                break;
            }
        }
    } catch (error) {
        console.log("error", error)
        if (parentPort) {
            parentPort.postMessage({ type: 'error', taskId: msg.taskId, error: (error as Error).message });
        }
    }
});

initializePyodide();