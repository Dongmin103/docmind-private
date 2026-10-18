import type { DocMindAnswerResult } from './docmind-service';

export type AnswerJobProgress = 'queued' | 'running' | 'reconnecting';
export interface AnswerJob {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  error_code?: string;
  result?: DocMindAnswerResult;
}
interface Envelope {
  data: { code: number; message?: string; data?: AnswerJob };
  response?: { status: number };
}
interface Transport {
  submit: (id: string) => Promise<Envelope>;
  get: (id: string) => Promise<Envelope>;
}

export class AnswerJobError extends Error {}

const StorageKey = 'docmind-answer-job-v1';
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// Keep only the opaque job ID and a digest; source passages never enter storage.
async function identity(payload: unknown) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}

function remembered() {
  try {
    return JSON.parse(sessionStorage.getItem(StorageKey) || 'null') as {
      digest: string;
      id: string;
      created: number;
    } | null;
  } catch {
    return null;
  }
}

export async function waitForAnswerJob(
  payload: unknown,
  transport: Transport,
  progress?: (state: AnswerJobProgress) => void,
): Promise<DocMindAnswerResult> {
  const digest = await identity(payload);
  const prior = remembered();
  const id =
    prior?.digest === digest && Date.now() - prior.created < 3_600_000
      ? prior.id
      : crypto.randomUUID().replaceAll('-', '');
  try {
    sessionStorage.setItem(
      StorageKey,
      JSON.stringify({ digest, id, created: Date.now() }),
    );
  } catch {
    // Storage restrictions do not prevent an answer in the current page.
  }
  let accepted = false;
  let failures = 0;
  const deadline = Date.now() + 2_700_000;
  while (Date.now() < deadline) {
    let response: Envelope;
    try {
      response = accepted
        ? await transport.get(id)
        : await transport.submit(id);
      if (response.response && response.response.status >= 500)
        throw new Error('server temporarily unavailable');
      if (
        !response?.data ||
        typeof response.data.code !== 'number' ||
        response.data.code === 1999
      )
        throw new Error('connection unavailable');
      if (response.data.code === 401)
        throw new AnswerJobError('DOCMIND_ANSWER_AUTH_REQUIRED');
      if (response.data.code !== 0)
        throw new AnswerJobError(
          response.data.message || 'DOCMIND_ANSWER_FAILED',
        );
      if (!response.data.data) throw new Error('connection unavailable');
      failures = 0;
    } catch (error) {
      if (error instanceof AnswerJobError) {
        if (error.message !== 'DOCMIND_ANSWER_AUTH_REQUIRED')
          clearRemembered(id);
        throw error;
      }
      failures += 1;
      progress?.('reconnecting');
      await pause(Math.min(10_000, 2000 * failures));
      continue;
    }
    accepted = true;
    const job = response.data.data;
    if (job.status === 'failed') {
      clearRemembered(id);
      throw new AnswerJobError(job.error_code || 'DOCMIND_ANSWER_FAILED');
    }
    if (job.status === 'completed' && job.result) {
      clearRemembered(id);
      return job.result;
    }
    if (job.status !== 'queued' && job.status !== 'running')
      throw new AnswerJobError('DOCMIND_ANSWER_FAILED');
    progress?.(job.status);
    await pause(2000);
  }
  throw new AnswerJobError('DOCMIND_ANSWER_JOB_EXPIRED');
}

function clearRemembered(id: string) {
  if (remembered()?.id !== id) return;
  try {
    sessionStorage.removeItem(StorageKey);
  } catch {
    // Session storage may be unavailable.
  }
}
