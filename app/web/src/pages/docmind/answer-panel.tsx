import {
  answerDocMind,
  type DocMindAnswerRequest,
  type DocMindAnswerResult,
  type DocMindSearchRequest,
} from '@/services/docmind-service';
import type { AnswerJobProgress } from '@/services/docmind-answer-job';
import { useMutation } from '@tanstack/react-query';
import {
  type FormEvent,
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { Search } from 'lucide-react';
import AnswerMarkdown from './answer-markdown';
import AnswerEvidence from './answer-evidence';
import './answer-panel.less';

const ErrorMessages: Record<string, string> = {
  DOCMIND_ANSWER_QUEUE_FULL:
    '현재 답변 요청이 많습니다. 잠시 후 다시 시도해 주세요.',
  DOCMIND_ANSWER_JOB_EXPIRED:
    '대기 시간이 만료되었습니다. 같은 질문을 다시 접수해 주세요.',
  DOCMIND_ANSWER_JOB_NOT_FOUND:
    '답변 작업의 보관 기간이 지났습니다. 질문을 다시 접수해 주세요.',
  DOCMIND_ANSWER_JOB_INTERRUPTED:
    '서버 재시작으로 답변 작업을 완료하지 못했습니다. 다시 시도해 주세요.',
  DOCMIND_ANSWER_AUTH_REQUIRED: '로그인을 확인한 뒤 질문을 다시 접수해 주세요.',

  DOCMIND_ANSWER_DISABLED:
    'LLM 답변 기능이 아직 활성화되지 않았습니다. 검색은 계속 사용할 수 있습니다.',
  DOCMIND_ANSWER_CONFIG_INVALID:
    '관리자가 LLM 모델과 연결 설정을 확인해야 합니다.',
  DOCMIND_LLM_UNAVAILABLE:
    'LLM 서비스에 연결할 수 없습니다. 검색 근거는 계속 확인할 수 있습니다.',
  DOCMIND_LLM_TIMEOUT:
    '모델 서버의 응답이 지연되어 답변 생성을 중단했습니다. 잠시 후 다시 시도해 주세요. 검색 근거는 계속 확인할 수 있습니다.',
  DOCMIND_LLM_AUTH_FAILED:
    '외부 LLM 인증에 실패했습니다. 관리자가 API 키를 확인해야 합니다.',
  DOCMIND_LLM_CREDITS_REQUIRED:
    '외부 LLM의 사용 가능 잔액 또는 키의 지출 한도가 부족합니다. 관리자에게 확인해 주세요.',
  DOCMIND_LLM_RATE_LIMITED:
    '외부 LLM 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
  DOCMIND_SOURCE_CHANGED: '문서가 변경되었습니다. 다시 검색해 주세요.',
  DOCMIND_CONTEXT_TOO_LARGE:
    '상위 근거가 모델 입력 한도를 넘었습니다. 질문이나 검색 범위를 좁혀 주세요.',
  DOCMIND_LLM_OUTPUT_TRUNCATED:
    '모델의 답변 길이 한도를 초과했습니다. 질문을 좁혀 주세요.',
  DOCMIND_LLM_CITATIONS_INVALID:
    '모델이 올바른 근거 번호를 제시하지 않아 답변을 표시하지 않았습니다.',
  DOCMIND_LLM_RESPONSE_INVALID:
    '모델의 응답 형식을 확인할 수 없어 답변을 표시하지 않았습니다.',
};

const RecoverableAnswerErrors = new Set([
  'DOCMIND_LLM_RESPONSE_INVALID',
  'DOCMIND_LLM_CITATIONS_INVALID',
  'DOCMIND_LLM_OUTPUT_TRUNCATED',
]);

class AnswerRequestError extends Error {
  constructor(readonly code: string) {
    super(
      ErrorMessages[code] ||
        '답변 생성에 실패했습니다. 검색 근거를 확인해 주세요.',
    );
  }
}

const pendingOwners = new WeakMap<
  MutableRefObject<boolean>,
  { key: string; token: symbol }
>();

export default function AnswerPanel({
  request,
  disabled = false,
  pending = false,
  pendingRef,
  onPendingChange,
  invalidationKey,
  composer,
  scopeControl,
  renderInspection,
}: {
  request: DocMindSearchRequest;
  disabled?: boolean;
  pending?: boolean;
  pendingRef?: MutableRefObject<boolean>;
  onPendingChange?: (pending: boolean) => void;
  invalidationKey?: string;
  composer?: ReactNode;
  scopeControl?: ReactNode;
  renderInspection?: (datasetId: string, documentId: string) => ReactNode;
}) {
  const localSubmitting = useRef(false);
  const submitting = pendingRef ?? localSubmitting;
  const [localPending, setLocalPending] = useState(false);
  const [jobProgress, setJobProgress] = useState<AnswerJobProgress>('queued');
  const isPending = pending || localPending;
  const requestKey = JSON.stringify(request);
  const displayKey = JSON.stringify([requestKey, invalidationKey]);
  const [submitted, setSubmitted] = useState<{
    key: string;
    question: string;
    payload?: DocMindAnswerRequest;
  }>({ key: '', question: '' });
  const activeSubmission = useRef(0);
  const mounted = useRef(false);
  const submittedQuestion =
    submitted.key === displayKey ? submitted.question : '';
  const [selectedEvidence, setSelectedEvidence] = useState<{
    number?: number;
    trigger: HTMLButtonElement;
    selectionId: number;
  }>();
  const panelRef = useRef<HTMLElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const answerStartRef = useRef<HTMLDivElement>(null);
  const followAnswer = useRef(false);
  const revealedTop = useRef(0);
  const restoreCitation = useRef<HTMLButtonElement | null>(null);
  const generation = useMutation<
    DocMindAnswerResult,
    Error,
    { payload: DocMindAnswerRequest; localToken: number; ownerToken: symbol }
  >({
    mutationFn: async ({ payload, ownerToken, localToken }) => {
      try {
        const { data } = await answerDocMind(payload, (state) => {
          if (mounted.current && localToken === activeSubmission.current)
            setJobProgress(state);
        });
        if (data.code !== 0) throw new AnswerRequestError(data.message);
        if (!data.data) throw new AnswerRequestError('DOCMIND_ANSWER_FAILED');
        return data.data;
      } finally {
        if (pendingOwners.get(submitting)?.token === ownerToken) {
          pendingOwners.delete(submitting);
          submitting.current = false;
          onPendingChange?.(false);
        }
      }
    },
    onSettled: (_data, _error, variables) => {
      if (!mounted.current || variables.localToken !== activeSubmission.current)
        return;
      setLocalPending(false);
    },
    gcTime: 0,
    retry: false,
  });
  const resetGeneration = generation.reset;
  useLayoutEffect(() => {
    mounted.current = true;
    followAnswer.current = false;
    activeSubmission.current += 1;
    const owner = pendingOwners.get(submitting);
    if (owner && owner.key !== displayKey) {
      pendingOwners.delete(submitting);
      submitting.current = false;
      onPendingChange?.(false);
    }
    // oxlint-disable-next-line react/set-state-in-effect -- A previous request must stop blocking the new request immediately.
    setLocalPending(false);
    resetGeneration();
    return () => {
      mounted.current = false;
      activeSubmission.current += 1;
    };
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- Request identity alone owns reset; callback/ref identity is independent.
  }, [requestKey, invalidationKey, resetGeneration]);
  useLayoutEffect(() => {
    const viewport = panelRef.current?.closest<HTMLElement>(
      '[data-docmind-search-scroll]',
    );
    if (!viewport) return;
    const stopFollowing = () => {
      followAnswer.current = false;
    };
    const handleScroll = () => {
      if (Math.abs(viewport.scrollTop - revealedTop.current) > 2)
        stopFollowing();
    };
    const handleKey = (event: KeyboardEvent) => {
      if (
        [
          'ArrowUp',
          'ArrowDown',
          'PageUp',
          'PageDown',
          'Home',
          'End',
          ' ',
        ].includes(event.key)
      )
        stopFollowing();
    };
    const handleSelection = () => {
      if (window.getSelection()?.isCollapsed === false) stopFollowing();
    };
    viewport.addEventListener('wheel', stopFollowing, { passive: true });
    viewport.addEventListener('touchstart', stopFollowing, { passive: true });
    viewport.addEventListener('pointerdown', stopFollowing);
    viewport.addEventListener('scroll', handleScroll, { passive: true });
    viewport.addEventListener('keydown', handleKey);
    document.addEventListener('selectionchange', handleSelection);
    return () => {
      viewport.removeEventListener('wheel', stopFollowing);
      viewport.removeEventListener('touchstart', stopFollowing);
      viewport.removeEventListener('pointerdown', stopFollowing);
      viewport.removeEventListener('scroll', handleScroll);
      viewport.removeEventListener('keydown', handleKey);
      document.removeEventListener('selectionchange', handleSelection);
    };
  }, []);
  useLayoutEffect(() => {
    if (
      !submittedQuestion ||
      !followAnswer.current ||
      window.getSelection()?.isCollapsed === false
    )
      return;
    const viewport = panelRef.current?.closest<HTMLElement>(
      '[data-docmind-search-scroll]',
    );
    const start = answerStartRef.current;
    if (!viewport || !start) return;
    const top = Math.max(
      0,
      viewport.scrollTop +
        start.getBoundingClientRect().top -
        viewport.getBoundingClientRect().top -
        16,
    );
    viewport.scrollTo({ top, behavior: 'instant' });
    // Read back the clamped position when the pending turn is shorter than the viewport.
    revealedTop.current = viewport.scrollTop;
  }, [submittedQuestion, isPending, generation.data, generation.isError]);
  const submitAnswer = (payload: DocMindAnswerRequest, key: string) => {
    if (
      disabled ||
      !payload.question.trim() ||
      submitting.current ||
      key !== displayKey
    )
      return;
    // oxlint-disable-next-line react/immutability -- The shared ref synchronously guards remounts before React updates pending state.
    submitting.current = true;
    const ownerToken = Symbol('docmind-answer-request');
    pendingOwners.set(submitting, { key, token: ownerToken });
    setLocalPending(true);
    setJobProgress('queued');
    onPendingChange?.(true);
    followAnswer.current = true;
    setSelectedEvidence(undefined);
    setSubmitted({ key, question: payload.question, payload });
    activeSubmission.current += 1;
    generation.mutate({
      payload,
      localToken: activeSubmission.current,
      ownerToken,
    });
  };
  const handleGenerate = (event: FormEvent) => {
    event.preventDefault();
    const payload = JSON.parse(
      JSON.stringify({ ...request, neighborWindow: 2 }),
    ) as DocMindAnswerRequest;
    submitAnswer(payload, displayKey);
  };
  const handleRetry = () => {
    if (submitted.key !== displayKey || !submitted.payload) return;
    submitAnswer(submitted.payload, submitted.key);
  };
  const selectCitation = useCallback(
    (number: number, trigger: HTMLButtonElement) => {
      setSelectedEvidence((current) => ({
        number,
        trigger,
        selectionId: (current?.selectionId ?? 0) + 1,
      }));
    },
    [],
  );
  const selectSource = (event: React.MouseEvent<HTMLButtonElement>) =>
    selectCitation(
      Number(event.currentTarget.dataset.citation),
      event.currentTarget,
    );
  const closeEvidence = () => {
    restoreCitation.current = selectedEvidence?.trigger ?? null;
    setSelectedEvidence((current) =>
      current ? { ...current, number: undefined } : undefined,
    );
  };
  useLayoutEffect(() => {
    const trigger = restoreCitation.current;
    if (!trigger?.isConnected) return;
    restoreCitation.current = null;
    trigger.focus({ preventScroll: true });
    const viewport = panelRef.current?.closest<HTMLElement>(
      '[data-docmind-search-scroll]',
    );
    if (!viewport) return;
    const bounds = trigger.getBoundingClientRect();
    const visible = viewport.getBoundingClientRect();
    const composerBounds = composerRef.current?.getBoundingClientRect();
    const visibleBottom =
      composerBounds &&
      composerBounds.height > 0 &&
      composerBounds.top > visible.top
        ? Math.min(visible.bottom, composerBounds.top)
        : visible.bottom;
    const delta =
      bounds.top < visible.top + 16
        ? bounds.top - visible.top - 16
        : bounds.bottom > visibleBottom - 16
          ? bounds.bottom - visibleBottom + 16
          : 0;
    if (delta)
      viewport.scrollTo({
        top: Math.max(0, viewport.scrollTop + delta),
        behavior: 'instant',
      });
  }, [selectedEvidence]);
  const currentSubmission = submitted.key === displayKey;
  const currentError = currentSubmission && generation.isError;
  const currentData = currentSubmission ? generation.data : undefined;
  const canRetry =
    currentError &&
    generation.error instanceof AnswerRequestError &&
    RecoverableAnswerErrors.has(generation.error.code);
  const citation =
    !isPending && !currentError
      ? currentData?.citations.find(
          (item) => item.number === selectedEvidence?.number,
        )
      : undefined;

  const state = isPending
    ? 'pending'
    : currentError
      ? 'error'
      : currentData
        ? 'complete'
        : 'idle';
  const answerStatus =
    currentData?.answer_status === 'partial'
      ? '일부 근거 확인'
      : currentData?.answer_status === 'insufficient'
        ? '근거 부족'
        : '답변 완료';
  return (
    <section
      ref={panelRef}
      aria-label="문서 근거 답변"
      data-state={state}
      className="docmind-answer-panel min-w-0 text-text-primary"
    >
      <div className="docmind-answer-body">
        {submittedQuestion && (
          <div aria-label="내 질문" className="docmind-answer-question">
            <p>질문</p>
            <h1>{submittedQuestion}</h1>
          </div>
        )}
        <div
          ref={answerStartRef}
          aria-label="답변 흐름"
          className={`docmind-answer-flow${citation ? ' docmind-answer-flow-with-evidence' : ''}`}
        >
          <div className="min-w-0">
            {isPending && (
              <div role="status" className="docmind-answer-state">
                <span className="docmind-search-pulse" aria-hidden="true" />
                <h2>문서에서 답변을 준비하고 있습니다</h2>
                <p>
                  {jobProgress === 'reconnecting'
                    ? '연결을 다시 확인하고 있습니다. 접수된 작업의 결과를 계속 조회합니다.'
                    : jobProgress === 'queued'
                      ? '질문을 접수하고 있습니다. 순서가 되면 답변 생성을 시작합니다.'
                      : '선택한 범위의 근거를 확인하고 있습니다. 오래 걸려도 완료될 때까지 결과를 조회합니다.'}
                </p>
              </div>
            )}
            {currentError && (
              <div
                role={canRetry ? 'status' : 'alert'}
                className={`docmind-answer-state${canRetry ? '' : ' docmind-answer-error'}`}
              >
                <h2>
                  {canRetry
                    ? '답변을 다시 준비할 수 있어요'
                    : '답변을 생성하지 못했습니다'}
                </h2>
                <p>
                  {canRetry
                    ? '응답을 정리하는 과정에서 문제가 생겼습니다. 같은 질문으로 다시 시도해 주세요.'
                    : generation.error.message}
                </p>
                {canRetry && (
                  <button
                    type="button"
                    onClick={handleRetry}
                    disabled={isPending || disabled}
                    className="bg-accent-primary text-primary-foreground disabled:opacity-40"
                  >
                    같은 질문 다시 시도
                  </button>
                )}
              </div>
            )}
            {currentData && !isPending && !currentError && (
              <article aria-label="AI 답변" className="docmind-answer-article">
                <header className="docmind-answer-heading">
                  <h2>답변</h2>
                  <span role="status">{answerStatus}</span>
                </header>
                <AnswerMarkdown
                  content={currentData.answer}
                  citationNumbers={currentData.citations.map(
                    (item) => item.number,
                  )}
                  onCitation={selectCitation}
                />
                <details className="docmind-answer-context">
                  <summary>검색 문맥 상세</summary>
                  <p>
                    검색 근거 {currentData.context.seed_count}개 · 문맥 확장{' '}
                    {currentData.context.expanded_count}개 · 실제 입력{' '}
                    {currentData.context.used_count}개
                  </p>
                  {currentData.context.budget_pruned && (
                    <p>입력 한도에 맞춰 주변 청크 일부 제외</p>
                  )}
                  {Boolean(currentData.context.seed_only_count) && (
                    <p>순서/버전 정보가 없는 근거는 확장하지 않음</p>
                  )}
                </details>
                <p className="docmind-answer-notice">
                  생성된 답변은 틀릴 수 있습니다. 인용 근거를 확인하세요.
                </p>
                {currentData.citations.length > 0 && (
                  <div
                    className="docmind-answer-sources"
                    role="group"
                    aria-label="답변 출처"
                  >
                    <h3>출처</h3>
                    {currentData.citations.map((item) => (
                      <button
                        type="button"
                        key={item.number}
                        data-citation={item.number}
                        onClick={selectSource}
                        aria-pressed={citation?.number === item.number}
                      >
                        <span className="docmind-answer-source-number">
                          [{item.number}]
                        </span>
                        <span>{item.document_name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </article>
            )}
          </div>
          <AnswerEvidence
            citation={citation}
            selectionId={selectedEvidence?.selectionId}
            datasetId={currentData?.dataset_id}
            onClose={closeEvidence}
            renderInspection={renderInspection}
          />
        </div>
      </div>
      <div
        ref={composerRef}
        data-docmind-composer
        className="docmind-question-dock"
      >
        <div className="docmind-question-composer docmind-search-controls">
          <form
            id="docmind-question"
            onSubmit={handleGenerate}
            aria-label="AI 질문 작성"
            className="docmind-question-form"
          >
            {composer}
          </form>
          {scopeControl}
          <button
            type="submit"
            form="docmind-question"
            disabled={disabled || !request.question.trim() || isPending}
            className="docmind-question-submit bg-accent-primary text-primary-foreground disabled:opacity-40"
          >
            <Search className="size-4" aria-hidden="true" />
            질문하기
          </button>
        </div>
        <details className="docmind-answer-guide">
          <summary>답변 생성 안내</summary>
          <p>
            선택한 범위와 앞뒤 2개 청크를 확인하여 답변합니다. 문서 안의
            지시문은 답변 지침으로 사용하지 않습니다.
          </p>
        </details>
      </div>
    </section>
  );
}
