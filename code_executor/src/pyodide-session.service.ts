import {
  Injectable,
  OnModuleInit,
  OnModuleDestroy,
  InternalServerErrorException,
  NotFoundException,
  RequestTimeoutException,
  Logger,
} from '@nestjs/common';
import { Worker } from 'worker_threads';
import * as path from 'path';
import { v4 as uuidv4 } from 'uuid';
import { createHash } from 'crypto';
import * as fs from 'fs';

const SESSIONS_BASE_PATH = './pyodide_sessions';
if (process.env.NUM_WORKERS){
  var NUM_WORKERS = parseInt(process.env.NUM_WORKERS) || 2
}
else {
  var NUM_WORKERS = 2;
}



interface WorkerInstance {
  id: string; // Unique UUID for the worker process
  instance: Worker | null;
}

interface QueuedTask {
  task: any;
  resolve: (value: any) => void;
  reject: (reason?: any) => void;
}

interface ActiveTask extends QueuedTask {
  workerId: string; // The UUID of the worker handling the task
  startTime: number;
}

@Injectable()
export class PyodideSessionService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(PyodideSessionService.name);
  private workers: WorkerInstance[] = [];
  private taskQueues: QueuedTask[][] = [];
  private activeTasks = new Map<string, ActiveTask>();
  private workerPath: string;

  private getWorkerIndexForSession(sessionId: string): number {
    const hash = createHash('sha256').update(sessionId).digest('hex');
    const hashAsInt = parseInt(hash.substring(0, 8), 16);
    return hashAsInt % NUM_WORKERS;
  }

  onModuleInit() {
    this.logger.log(`Initializing Pyodide Session Service with ${NUM_WORKERS} workers.`);
    this.workerPath = path.join(__dirname, 'pyodide.worker.js');

    // --- NEW, MORE ROBUST CLEANUP LOGIC ---
    this.logger.log(`Performing one-time cleanup of session directory: ${SESSIONS_BASE_PATH}`);
    try {
      // Ensure the base directory exists. If not, create it.
      if (!fs.existsSync(SESSIONS_BASE_PATH)) {
        fs.mkdirSync(SESSIONS_BASE_PATH, { recursive: true });
        this.logger.log('Session directory created.');
      } else {
        // If it exists, read its contents and delete them one by one.
        // This is safer than deleting the parent directory itself.
        const files = fs.readdirSync(SESSIONS_BASE_PATH);
        if (files.length > 0) {
          this.logger.log(`Found ${files.length} old session files/directories to clean up...`);
          for (const file of files) {
            fs.rmSync(path.join(SESSIONS_BASE_PATH, file), { recursive: true, force: true });
          }
          this.logger.log('Session directory cleaned successfully.');
        } else {
          this.logger.log('Session directory was already empty.');
        }
      }
    } catch (error) {
      this.logger.error('Failed to clean up session directory on startup!', error);
      // It's safer to continue than to crash the whole app if cleanup fails.
    }
    // --- END OF NEW LOGIC ---

    // Now, spawn workers into the clean environment
    for (let i = 0; i < NUM_WORKERS; i++) {
      this.taskQueues[i] = [];
      this.spawnWorker(i);
    }
  }

  onModuleDestroy() {
    this.logger.log('Terminating all Pyodide workers.');
    this.workers.forEach(w => w.instance?.terminate());
  }

  private spawnWorker(index: number) {
    const workerUUID = uuidv4();
    this.logger.log(`Spawning new Pyodide worker at index #${index} with ID: ${workerUUID}`);
    const worker = new Worker(this.workerPath, { workerData: { workerId: workerUUID } });
    
    this.workers[index] = { id: workerUUID, instance: worker };

    worker.on('message', (msg) => {
      if (msg.type === 'ready') {
        this.logger.log(`✅ Worker [${workerUUID}] is ready.`);
        this.processQueueForWorker(index);
        return;
      }

      const task = this.activeTasks.get(msg.taskId);
      if (task) {
        if (msg.type === 'error') {
          const errorMsg = msg.error || 'An unknown error occurred in the worker.';
          if (errorMsg.includes('Session with ID')) {
            task.reject(new NotFoundException(errorMsg));
          } else {
            task.reject(new InternalServerErrorException(errorMsg));
          }
        } else {
          task.resolve(msg);
        }
        this.activeTasks.delete(msg.taskId);
        this.processQueueForWorker(index);
      }
    });

    worker.on('error', (err) => {
      this.logger.error(`❌ Worker [${workerUUID}] at index #${index} encountered a fatal error:`, err);
      this.replaceWorker(worker);
    });

    worker.on('exit', (code) => {
      if (code !== 0) {
        this.logger.warn(`Worker [${workerUUID}] at index #${index} exited unexpectedly with code ${code}. Replacing it.`);
        this.replaceWorker(worker);
      } else {
        this.logger.log(`Worker [${workerUUID}] at index #${index} exited gracefully.`);
      }
    });
  }
  
  private replaceWorker(crashedWorker: Worker) {
    const workerIndex = this.workers.findIndex(w => w.instance === crashedWorker);
    if (workerIndex === -1) return; // Already replaced

    const crashedWorkerId = this.workers[workerIndex].id;
    this.logger.warn(`--- Replacing Worker ---`);
    this.logger.warn(`Crashed Worker ID: ${crashedWorkerId} at index #${workerIndex}`);
    this.workers[workerIndex].instance = null;
    crashedWorker.terminate().catch(err => this.logger.error(`Error terminating crashed worker [${crashedWorkerId}]:`, err));

    // Reject tasks active on the crashed worker
    this.activeTasks.forEach((task, taskId) => {
      if (task.workerId === crashedWorkerId) {
        this.logger.warn(`Rejecting active task ${taskId} from crashed worker [${crashedWorkerId}]`);
        task.reject(new InternalServerErrorException(`Worker [${crashedWorkerId}] crashed. Your task has been cancelled.`));
        this.activeTasks.delete(taskId);
      }
    });

    // Reject tasks in the queue for the crashed worker's index
    const rejectedQueueTasks = this.taskQueues[workerIndex].length;
    if (rejectedQueueTasks > 0) {
        this.logger.warn(`Rejecting ${rejectedQueueTasks} queued tasks for crashed worker index #${workerIndex}`);
        this.taskQueues[workerIndex].forEach(({ reject }) => {
            reject(new InternalServerErrorException(`Worker at index #${workerIndex} crashed before your task could start.`));
        });
        this.taskQueues[workerIndex] = [];
    }
    
    this.logger.warn(`--- End Worker Replacement ---`);
    this.spawnWorker(workerIndex);
  }

  private processQueueForWorker(workerIndex: number) {
    const worker = this.workers[workerIndex];
    if (!worker || !worker.instance) return;

    const isWorkerBusy = Array.from(this.activeTasks.values()).some(t => t.workerId === worker.id);
    if (this.taskQueues[workerIndex].length === 0 || isWorkerBusy) {
      return;
    }

    const nextTask = this.taskQueues[workerIndex].shift();
    if (!nextTask) return;

    const { task, resolve, reject } = nextTask;
    this.activeTasks.set(task.taskId, { ...nextTask, workerId: worker.id, startTime: Date.now() });
    worker.instance.postMessage(task);
  }

  private sendTaskToWorker<T>(task: any, timeoutMs?: number): Promise<T> {
    const taskId = uuidv4();
    const taskWithId = { ...task, taskId };
    const sessionId = task.sessionId || '(new session)';
    const workerIndex = this.getWorkerIndexForSession(sessionId);

    // This check is important for when the service is starting up
    const workerId = this.workers[workerIndex]?.id || 'N/A (worker not spawned yet)';
    
    this.logger.debug(
      `Routing task [${task.type}] for session [${sessionId}] -> ` +
      `Hash determined index #${workerIndex} -> ` +
      `Mapped to worker ID [${workerId}]`
    );

    const promise = new Promise<T>((resolve, reject) => {
      if (workerIndex < 0 || workerIndex >= this.workers.length) {
          return reject(new InternalServerErrorException('Could not determine a valid worker for the task.'));
      }
      this.taskQueues[workerIndex].push({ task: taskWithId, resolve, reject });
      this.processQueueForWorker(workerIndex);
    });

    // If no timeout is specified, return the simple promise
    if (!timeoutMs) {
      return promise;
    }

    // If a timeout is specified, race the task against a timer
    return new Promise<T>((resolve, reject) => {
      const timeoutHandle = setTimeout(() => {
        const activeTask = this.activeTasks.get(taskId);
        if (activeTask) {
          // The task timed out. This worker is stuck. It must be replaced.
          const stuckWorkerId = activeTask.workerId;
          const stuckWorkerIndex = this.workers.findIndex(w => w.id === stuckWorkerId);
          
          this.logger.error(`Task ${taskId} on worker [${stuckWorkerId}] at index #${stuckWorkerIndex} timed out. Replacing worker.`);
          
          if (stuckWorkerIndex !== -1 && this.workers[stuckWorkerIndex].instance) {
              this.replaceWorker(this.workers[stuckWorkerIndex].instance);
          }
          
          // The rejection for the task itself is handled by the timeout exception below.
          // We must clean up the task from the active map.
          this.activeTasks.delete(taskId);
        }
        reject(new RequestTimeoutException(`Task timed out after ${timeoutMs}ms. The responsible worker has been restarted.`));
      }, timeoutMs);

      // Let the original promise run.
      // If it completes first, `finally` will clear the timeout.
      // If the timeout completes first, it rejects, and this block is done.
      promise
        .then(resolve)
        .catch(reject)
        .finally(() => {
          clearTimeout(timeoutHandle);
        });
    });
  }

  async createSession(): Promise<string> {
    const sessionId = uuidv4();
    this.logger.log(`Pre-generating new session ID: [${sessionId}]`);
    await this.sendTaskToWorker<{ sessionId: string }>({ type: 'createSession', sessionId });
    return sessionId;
  }

  // ... other public methods are unchanged ...
  async uploadFile(sessionId: string, file: Express.Multer.File): Promise<string> {
    const serializableFile = { originalname: file.originalname, buffer: file.buffer };
    const response = await this.sendTaskToWorker<{ filename: string }>({ type: 'uploadFile', sessionId, file: serializableFile });
    return response.filename;
  }

  async executeCode(sessionId: string, code: string, timeoutMs: number = 30000): Promise<any> {
    const response = await this.sendTaskToWorker<{ result: any }>({ type: 'executeCode', sessionId, code }, timeoutMs);
    return response.result;
  }

  async downloadFile(sessionId: string, path: string): Promise<Buffer> {
    const response = await this.sendTaskToWorker<{ data: Uint8Array }>({ type: 'downloadFile', sessionId, path });
    return Buffer.from(response.data);
  }
}