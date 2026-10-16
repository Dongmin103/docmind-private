import type { DocMindHierarchyNode } from '@/services/docmind-service';
import { useState, type MouseEvent, type ReactNode } from 'react';
import { PdfOcrChoice } from './pdf-ocr-choice';
import { SourceIndexStatus } from './source-index-status';
import {
  OcrAcknowledgementKey,
  OcrProgress,
  ocrComplete,
  ocrIdentity,
  readOcrAcknowledgements,
} from './ocr-progress';

type Props = {
  nodes: DocMindHierarchyNode[];
  onConfirm: (
    documentId: string,
    versionId: string,
    key: string,
  ) => Promise<void>;
  renderInspection: (node: DocMindHierarchyNode) => ReactNode;
};

const ActiveStates = new Set([
  'PENDING',
  'PROCESSING',
  'RETRY_WAIT',
  'CLEANUP',
]);

export function OcrReviewPanel({ nodes, onConfirm, renderInspection }: Props) {
  const [acknowledged, setAcknowledged] = useState(readOcrAcknowledgements);
  const [accepted, setAccepted] = useState<Record<string, string>>({});
  const [storageFailed, setStorageFailed] = useState(false);
  const progressNodes = nodes.flatMap((node) => {
    if (node.type !== 'file' || !node.document_exists) return [];
    const history = (node.index_pdf_ocr_runs ?? []).map((run) => ({
      ...node,
      index_state: undefined,
      index_pdf_ocr_version_id: run.version_id,
      index_pdf_ocr_job_state: run.state,
      index_cleanup_state: run.cleanup_state,
      index_error_code: run.error_code,
      ocr_completed: run.completed,
      ocr_previous_version: run.previous_version,
    }));
    if (
      node.index_pdf_ocr_version_id &&
      !history.some(
        (item) =>
          item.index_pdf_ocr_version_id === node.index_pdf_ocr_version_id,
      ) &&
      (node.index_pdf_ocr_requested ||
        accepted[node.document_id ?? ''] === node.index_pdf_ocr_version_id)
    ) {
      return [
        ...history,
        node.index_pdf_ocr_requested
          ? node
          : {
              ...node,
              index_state: 'PENDING' as const,
              index_pdf_ocr_job_state: null,
              index_error_code: null,
            },
      ];
    }
    return history;
  });
  const pendingResults = progressNodes.filter(
    (node) => !ocrComplete(node) || !acknowledged.includes(ocrIdentity(node)),
  );
  const confirm = async (
    documentId: string,
    versionId: string,
    key: string,
  ) => {
    await onConfirm(documentId, versionId, key);
    setAccepted((previous) => ({ ...previous, [documentId]: versionId }));
  };
  const acknowledge = (event: MouseEvent<HTMLButtonElement>) => {
    const key = event.currentTarget.value;
    const next = [
      ...new Set([...readOcrAcknowledgements(), ...acknowledged, key]),
    ];
    setAcknowledged(next);
    try {
      localStorage.setItem(OcrAcknowledgementKey, JSON.stringify(next));
      setStorageFailed(false);
    } catch {
      setStorageFailed(true);
    }
  };
  const entries = nodes.filter(
    (node) =>
      node.type === 'file' &&
      node.document_exists &&
      !ActiveStates.has(node.index_state ?? '') &&
      node.index_pdf_ocr_option === 'zero_text' &&
      !node.index_pdf_ocr_requested &&
      !(
        node.index_pdf_ocr_version_id &&
        accepted[node.document_id ?? ''] === node.index_pdf_ocr_version_id
      ),
  );

  return (
    <section
      aria-label="OCR 필요"
      className="mt-6 rounded-xl border border-border-button bg-bg-card p-5"
    >
      <h3 className="text-lg font-semibold text-text-primary">OCR 필요</h3>
      <p className="mt-1 text-sm text-text-secondary">
        검색 가능한 글자가 추출되지 않아 OCR이 필요한 자료입니다. 요청한 OCR의
        진행 상황과 완료 결과도 확인할 수 있습니다.
      </p>
      <OcrProgress
        nodes={pendingResults}
        onAcknowledge={acknowledge}
        renderInspection={renderInspection}
      />
      {storageFailed && (
        <p role="alert" className="mt-2 text-sm text-state-warning">
          완료 확인 내역을 저장하지 못했습니다. 새로고침하면 완료 항목이 다시
          표시될 수 있습니다.
        </p>
      )}
      <p className="mt-4 text-sm text-text-primary" aria-live="polite">
        OCR 필요 {entries.length}건
      </p>
      {entries.length === 0 ? (
        <p className="py-8 text-center text-sm text-text-secondary">
          현재 OCR이 필요한 자료가 없습니다.
        </p>
      ) : (
        <ul className="mt-3 max-h-[32rem] divide-y divide-border-button overflow-y-auto">
          {entries.map((node) => {
            const folder =
              node.relative_path
                .replace(/\\/g, '/')
                .split('/')
                .slice(0, -1)
                .join('/') || '최상위 폴더';
            const canRequest = Boolean(
              node.document_id &&
              node.index_pdf_ocr_option &&
              node.index_pdf_ocr_version_id,
            );
            return (
              <li
                key={node.file_id}
                className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded border border-border-button px-2 py-0.5 text-xs text-text-secondary">
                      OCR 필요
                    </span>
                    <span className="break-all text-sm font-medium text-text-primary">
                      {node.name}
                    </span>
                  </div>
                  <p className="mt-1 break-all text-xs text-text-secondary">
                    {folder}
                  </p>
                  <p className="mt-2 text-sm text-text-primary">
                    검색 가능한 글자가 추출되지 않았습니다.
                  </p>
                  <div className="mt-1 break-words text-xs">
                    <SourceIndexStatus node={node} />
                  </div>
                  {!canRequest && (
                    <p className="mt-1 text-xs text-text-secondary">
                      OCR 실행 정보를 확인할 수 없습니다. 자료 목록을
                      새로고침하세요.
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {renderInspection(node)}
                  {canRequest && (
                    <PdfOcrChoice node={node} onConfirm={confirm} />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
