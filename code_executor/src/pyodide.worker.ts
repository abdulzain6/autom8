import { parentPort, workerData } from 'worker_threads';
import { loadPyodide, PyodideAPI } from 'pyodide';
import { LRUCache } from 'lru-cache';
import * as fs from 'fs';
import * as path from 'path';

// This guard clause is essential for a stable worker
if (!parentPort) {
  throw new Error('This script is intended to be run as a worker thread.');
}

// --- Type Augmentation for Pyodide FS ---
// This helps TypeScript understand the full FS API
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

// --- Asynchronous Lock (Mutex) ---
// This class ensures that only one async filesystem operation (mount/unmount)
// runs at a time, preventing race conditions.
class AsyncMutex {
    private queue: Promise<void> = Promise.resolve();
    lock<T>(fn: () => Promise<T>): Promise<T> {
        const promise = this.queue.then(fn);
        this.queue = promise.then(() => {}).catch(() => {});
        return promise;
    }
}
const fsLock = new AsyncMutex();

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

    pyodide = await loadPyodide({
        indexURL: path.resolve(process.cwd(), 'pyodide_artifacts/pyodide'),
        stdout: (text) => console.log(`[Pyodide STDOUT #${workerId}] ${text}`),
        stderr: (text) => console.error(`[Pyodide STDERR #${workerId}] ${text}`),
    });
    console.log(`[Timing #${workerId}] Pyodide loaded in ${Date.now() - startTime} ms`);

    const packagesLoadStart = Date.now();
    await pyodide.loadPackage(["micropip", "matplotlib", "numpy", "pandas", "scipy", "sympy", "statsmodels", "networkx", "gmpy2", "mpmath", "shapely"]);
    console.log(`[Timing #${workerId}] Packages loaded in ${Date.now() - packagesLoadStart} ms`);

    fs.mkdirSync(SESSIONS_BASE_PATH, { recursive: true });

    console.log(`[Timing #${workerId}] Total initialization time: ${Date.now() - startTime} ms`);
    parentPort!.postMessage({ type: 'ready' });
}

parentPort!.on('message', async (msg) => {
    try {
        switch (msg.type) {
            case 'createSession': {
                const { sessionId } = msg;
                console.log(`[Worker #${workerId}] Creating session [${sessionId}]`);
                
                // Create the physical directory on the host. This is a safe, atomic operation.
                const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);
                fs.mkdirSync(sessionHostPath, { recursive: true });

                const sessionGlobals = pyodide.runPython('{}');
                const sessionContext: PyodideSessionContext = { id: sessionId, globals: sessionGlobals, createdAt: new Date() };
                sessions.set(sessionId, sessionContext);

                parentPort!.postMessage({ type: 'sessionCreated', taskId: msg.taskId, sessionId });
                break;
            }

            case 'uploadFile': {
                // Wrap the entire operation in the lock
                await fsLock.lock(async () => {
                    const { sessionId, file } = msg;
                    if (!sessions.has(sessionId)) throw new Error(`Session with ID "${sessionId}" not found on worker #${workerId}.`);
                    
                    const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);
                    const virtualSessionPath = `${VIRTUAL_SESSIONS_PATH}/${sessionId}`;
                    const filename = path.basename(file.originalname);
                    if (filename !== file.originalname) throw new Error('Invalid filename. Subdirectories are not allowed.');

                    const hostFilePath = path.join(sessionHostPath, filename);
                    const fileBuffer = Buffer.from(file.buffer.data);

                    try {
                        pyodide.mountNodeFS(virtualSessionPath, sessionHostPath);
                        // Write directly to the host filesystem, which is reflected in the virtual one.
                        fs.writeFileSync(hostFilePath, fileBuffer);
                    } finally {
                        pyodide.FS.unmount(virtualSessionPath);
                    }
                    parentPort!.postMessage({ type: 'fileUploaded', taskId: msg.taskId, filename });
                });
                break;
            }

            case 'executeCode': {
                // Wrap the entire operation in the lock
                await fsLock.lock(async () => {
                    const { sessionId, code, taskId } = msg;
                    const session = sessions.get(sessionId);
                    if (!session) throw new Error(`Session with ID "${sessionId}" not found on worker #${workerId}.`);

                    const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);
                    const virtualSessionPath = `${VIRTUAL_SESSIONS_PATH}/${sessionId}`;

                    try {
                        pyodide.mountNodeFS(virtualSessionPath, sessionHostPath);
                        pyodide.FS.chdir(virtualSessionPath);

                        const result = await pyodide.runPythonAsync(code, { globals: session.globals });
                        const toJsResult = typeof result?.toJs === 'function' ? result.toJs({ create_proxies: false }) : result;
                        parentPort!.postMessage({ type: 'executionComplete', taskId: taskId, result: toJsResult });

                    } finally {
                        pyodide.FS.unmount(virtualSessionPath);
                        pyodide.FS.chdir('/');
                    }
                });
                break;
            }

            case 'downloadFile': {
                // Wrap the entire operation in the lock
                await fsLock.lock(async () => {
                    const { sessionId, path: userPath } = msg;
                    if (!sessions.has(sessionId)) throw new Error(`Session with ID "${sessionId}" not found.`);
                    
                    const sessionHostPath = path.join(SESSIONS_BASE_PATH, sessionId);
                    const virtualSessionPath = `${VIRTUAL_SESSIONS_PATH}/${sessionId}`;
                    
                    // Your excellent path traversal security check
                    const requestedHostPath = path.normalize(path.join(sessionHostPath, userPath));
                    if (!requestedHostPath.startsWith(sessionHostPath)) {
                        throw new Error('Path traversal attempt detected. Access denied.');
                    }

                    if (!fs.existsSync(requestedHostPath)) {
                        throw new Error(`File not found at path: "${userPath}"`);
                    }

                    // Read directly from the host filesystem
                    const data: Buffer = fs.readFileSync(requestedHostPath);
                    parentPort!.postMessage({ type: 'fileDownloaded', taskId: msg.taskId, data });
                });
                break;
            }
        }
    } catch (error) {
        if (parentPort) {
            parentPort.postMessage({ type: 'error', taskId: msg.taskId, error: (error as Error).message });
        }
    }
});

initializePyodide();
