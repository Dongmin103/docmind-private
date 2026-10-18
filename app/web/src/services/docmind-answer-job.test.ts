import { webcrypto } from 'node:crypto';
import { TextEncoder } from 'node:util';
import { waitForAnswerJob } from './docmind-answer-job';

const envelope = (status: string, extra = {}) => ({
  data: { code: 0, data: { job_id: 'a'.repeat(32), status, ...extra } },
});

beforeAll(() => {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto });
  Object.defineProperty(globalThis, 'TextEncoder', { value: TextEncoder });
});
beforeEach(() => {
  sessionStorage.clear();
});
afterEach(() => jest.restoreAllMocks());

it('waits beyond the old 90 seconds using short polls without repeating submission', async () => {
  const realSetTimeout = global.setTimeout;
  let clock = 0;
  jest.spyOn(Date, 'now').mockImplementation(() => clock);
  jest.spyOn(global, 'setTimeout').mockImplementation(((
    callback: () => void,
    ms: number,
  ) => {
    clock += ms;
    return realSetTimeout(callback, 0);
  }) as typeof setTimeout);
  const submit = jest.fn().mockResolvedValue(envelope('queued'));
  let polls = 0;
  const get = jest.fn().mockImplementation(async () => {
    polls += 1;
    return polls > 50
      ? envelope('completed', { result: { answer: 'verified', citations: [] } })
      : envelope('running');
  });
  const result = await waitForAnswerJob(
    { question: 'synthetic' },
    { submit, get },
  );
  expect(result.answer).toBe('verified');
  expect(clock).toBeGreaterThan(90_000);
  expect(submit).toHaveBeenCalledTimes(1);
  expect(sessionStorage.length).toBe(0);
});

it('reuses the same ID after a lost acceptance response and transient polling failure', async () => {
  const realSetTimeout = global.setTimeout;
  jest
    .spyOn(global, 'setTimeout')
    .mockImplementation(((callback: () => void) =>
      realSetTimeout(callback, 0)) as typeof setTimeout);
  const submit = jest
    .fn()
    .mockRejectedValueOnce(new Error('lost ACK'))
    .mockResolvedValue(envelope('running'));
  const get = jest
    .fn()
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValue(envelope('completed', { result: { answer: 'done' } }));
  const progress = jest.fn();
  await waitForAnswerJob({}, { submit, get }, progress);
  expect(submit.mock.calls[0][0]).toBe(submit.mock.calls[1][0]);
  expect(progress).toHaveBeenCalledWith('reconnecting');
});

it('surfaces a terminal server error instead of showing an unchecked draft', async () => {
  const submit = jest
    .fn()
    .mockResolvedValue(
      envelope('failed', { error_code: 'DOCMIND_SOURCE_CHANGED' }),
    );
  await expect(
    waitForAnswerJob({}, { submit, get: jest.fn() }),
  ).rejects.toThrow('DOCMIND_SOURCE_CHANGED');
});

it('retrieves a completed job after its acceptance response was lost', async () => {
  const submit = jest.fn().mockResolvedValue(envelope('completed'));
  const get = jest
    .fn()
    .mockResolvedValue(
      envelope('completed', { result: { answer: 'resumed' } }),
    );
  expect((await waitForAnswerJob({}, { submit, get })).answer).toBe('resumed');
  expect(submit).toHaveBeenCalledTimes(1);
  expect(get).toHaveBeenCalledTimes(1);
});
