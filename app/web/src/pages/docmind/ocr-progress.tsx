import type { DocMindHierarchyNode } from '@/services/docmind-service';
import type { MouseEvent, ReactNode } from 'react';

export type OcrProgressNode = DocMindHierarchyNode & {
  ocr_completed?: boolean;
  ocr_previous_version?: boolean;
};

export const OcrAcknowledgementKey = 'docmind:ocr-acknowledged:v1';

export function ocrIdentity(node: DocMindHierarchyNode) {
  return `${node.file_id}:${node.index_pdf_ocr_version_id}`;
}

export function readOcrAcknowledgements(): string[] {
  try {
    const value: unknown = JSON.parse(
      localStorage.getItem(OcrAcknowledgementKey) ?? '[]',
    );
    return Array.isArray(value)
      ? value.filter((key): key is string => typeof key === 'string')
      : [];
  } catch {
    return [];
  }
}

export function ocrComplete(node: OcrProgressNode) {
  if (node.ocr_completed !== undefined) return node.ocr_completed;
  return (
    node.index_pdf_ocr_job_state === 'COMPLETE' &&
    node.index_state === 'INDEXED' &&
    node.searchable === true &&
    node.index_cleanup_state === 'COMPLETE'
  );
}

const StageLabels: Record<string, string> = {
  DISCOVERED: 'OCR 대기',
  WAITING_SOURCE_STABLE: '원본 안정화 대기',
  DECRYPTING: '문서 준비 중',
  PARSING: 'OCR 글자 인식 중',
  INDEXING: '검색 색인 반영 중',
  CLEANUP: '임시 파일 정리 중',
  RETRY_WAIT: 'OCR 재시도 대기',
  FAILED: 'OCR 실패',
  ACTION_REQUIRED: 'OCR 확인 필요',
  CLEANUP_FAILED: '임시 파일 정리 실패',
  SUPERSEDED: '원본 변경으로 작업 중단',
};

export function OcrProgress({
  nodes,
  onAcknowledge,
  renderInspection,
}: {
  nodes: OcrProgressNode[];
  onAcknowledge: (event: MouseEvent<HTMLButtonElement>) => void;
  renderInspection: (node: DocMindHierarchyNode) => ReactNode;
}) {
  if (!nodes.length) return null;
  return (
    <section
      aria-label="OCR 진행 및 결과"
      className="mt-4 rounded-lg border border-border-button p-4"
    >
      <h4 className="font-semibold text-text-primary">OCR 진행 및 결과</h4>
      <p className="mt-1 text-sm text-text-secondary">
        처리 상태는 자동으로 갱신됩니다. 완료된 자료는 확인 후 이 목록에서
        제외됩니다. 확인 내역은 현재 브라우저에 저장됩니다.
      </p>
      <ul className="mt-3 max-h-[32rem] divide-y divide-border-button overflow-y-auto">
        {nodes.map((node) => {
          const complete = ocrComplete(node);
          const failed =
            [
              'FAILED',
              'ACTION_REQUIRED',
              'CLEANUP_FAILED',
              'SUPERSEDED',
            ].includes(node.index_pdf_ocr_job_state ?? '') ||
            node.index_cleanup_state === 'FAILED' ||
            node.index_state === 'FAILED';
          const label = complete
            ? 'OCR 완료'
            : node.index_pdf_ocr_job_state === 'COMPLETE'
              ? 'OCR 결과 확인 필요'
              : (StageLabels[node.index_pdf_ocr_job_state ?? ''] ??
                'OCR 요청 접수 · 상태 확인 중');
          return (
            <li
              key={ocrIdentity(node)}
              className="flex flex-wrap items-center justify-between gap-3 py-3"
            >
              <div className="min-w-0 flex-1">
                <p className="break-all text-sm font-medium text-text-primary">
                  {node.name}
                </p>
                <p className="mt-1 break-all text-xs text-text-secondary">
                  {node.relative_path}
                </p>
                {node.ocr_previous_version && (
                  <p className="mt-1 text-xs text-text-secondary">
                    이전 원본 버전의 OCR 작업
                  </p>
                )}
                <p
                  role="status"
                  className={`mt-2 text-sm ${failed ? 'text-state-warning' : 'text-text-primary'}`}
                >
                  {label}
                </p>
                {failed && node.index_error_code && (
                  <p className="mt-1 text-xs text-state-warning">
                    {node.index_error_code}
                  </p>
                )}
                {complete && (
                  <p className="mt-1 text-xs text-text-secondary">
                    {node.ocr_previous_version
                      ? '이전 원본 버전의 OCR이 완료됐습니다. 현재 버전의 처리 상태는 문서 상세에서 확인하세요.'
                      : 'OCR 결과가 검색에 반영됐습니다.'}
                  </p>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {renderInspection(node)}
                {complete && (
                  <button
                    type="button"
                    value={ocrIdentity(node)}
                    onClick={onAcknowledge}
                    className="rounded border border-border-button px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-accent-primary"
                  >
                    완료 확인
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
