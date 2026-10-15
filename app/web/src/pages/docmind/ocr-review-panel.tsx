import type { DocMindHierarchyNode } from '@/services/docmind-service';
import { useMemo, useState, type MouseEvent, type ReactNode } from 'react';
import { PdfOcrChoice } from './pdf-ocr-choice';
import { SourceIndexStatus } from './source-index-status';

type Category = 'required' | 'review' | 'other';
type Filter = 'ocr' | Category;
type Props = {
  nodes: DocMindHierarchyNode[];
  onConfirm: (
    documentId: string,
    versionId: string,
    key: string,
  ) => Promise<void>;
  renderInspection: (node: DocMindHierarchyNode) => ReactNode;
};

const Categories: { id: Category; label: string; description: string }[] = [
  { id: 'required', label: 'OCR 필요', description: '검색 가능한 글자 없음' },
  { id: 'review', label: 'OCR 검토', description: '이미지 속 글자 확인 필요' },
  {
    id: 'other',
    label: '기타 인덱싱 확인',
    description: '실패·일부 내용 미추출',
  },
];
const ActiveStates = new Set([
  'PENDING',
  'PROCESSING',
  'RETRY_WAIT',
  'CLEANUP',
]);

function categoryOf(node: DocMindHierarchyNode): Category | null {
  if (
    node.type !== 'file' ||
    !node.document_exists ||
    ActiveStates.has(node.index_state ?? '')
  )
    return null;
  if (node.index_pdf_ocr_option === 'zero_text') return 'required';
  if (
    node.index_pdf_ocr_option === 'partial_images' ||
    node.index_image_ocr_not_run
  )
    return 'review';
  if (
    node.index_partial_coverage ||
    ['FAILED', 'ACTION_REQUIRED', 'CLEANUP_FAILED'].includes(
      node.index_state ?? '',
    )
  )
    return 'other';
  return null;
}

export function OcrReviewPanel({ nodes, onConfirm, renderInspection }: Props) {
  const [filter, setFilter] = useState<Filter>('ocr');
  const entries = useMemo(
    () =>
      nodes.flatMap((node) => {
        const category = categoryOf(node);
        return category ? [{ node, category }] : [];
      }),
    [nodes],
  );
  const counts = { required: 0, review: 0, other: 0 };
  for (const entry of entries) counts[entry.category]++;
  const visible = entries.filter(({ category }) =>
    filter === 'ocr' ? category !== 'other' : category === filter,
  );
  const selectFilter = (event: MouseEvent<HTMLButtonElement>) => {
    setFilter(event.currentTarget.value as Filter);
  };

  return (
    <section
      aria-label="OCR 확인 필요"
      className="mt-6 rounded-xl border border-border-button bg-bg-card p-5"
    >
      <h3 className="text-lg font-semibold text-text-primary">OCR 확인 필요</h3>
      <p className="mt-1 text-sm text-text-secondary">
        글자가 추출되지 않았거나 이미지 속 글자 확인이 필요한 자료를 모았습니다.
        처리 중인 자료는 제외됩니다.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {Categories.map(({ id, label, description }) => (
          <button
            key={id}
            type="button"
            value={id}
            onClick={selectFilter}
            aria-label={`${label} ${counts[id]}건`}
            aria-pressed={filter === id}
            className={`rounded-lg border p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary ${filter === id ? 'border-accent-primary bg-bg-base' : 'border-border-button'}`}
          >
            <span className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-text-primary">
                {label}
              </span>
              <span
                className={`text-2xl font-semibold ${id === 'required' && counts[id] ? 'text-state-warning' : 'text-text-primary'}`}
              >
                {counts[id]}
                <span className="ml-1 text-xs font-normal">건</span>
              </span>
            </span>
            <span className="mt-1 block text-xs text-text-secondary">
              {description}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          value="ocr"
          onClick={selectFilter}
          aria-pressed={filter === 'ocr'}
          className={`rounded-md border px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-accent-primary ${filter === 'ocr' ? 'border-accent-primary text-accent-primary' : 'border-border-button text-text-secondary'}`}
        >
          OCR 대상 전체 {counts.required + counts.review}건
        </button>
        <p className="text-xs text-text-secondary" aria-live="polite">
          현재 목록 {visible.length}건
        </p>
      </div>
      {filter === 'other' && (
        <p className="mt-3 text-sm text-text-secondary">
          OCR 필요 여부가 확인되지 않은 인덱싱 문제입니다. 문서 상태와 추출
          내용을 확인하세요.
        </p>
      )}
      {visible.length === 0 ? (
        <p className="py-8 text-center text-sm text-text-secondary">
          {filter === 'ocr'
            ? '현재 OCR 확인이 필요한 자료가 없습니다.'
            : '이 분류에 해당하는 자료가 없습니다.'}
        </p>
      ) : (
        <ul className="mt-3 max-h-[32rem] divide-y divide-border-button overflow-y-auto">
          {visible.map(({ node, category }) => {
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
                      {Categories.find(({ id }) => id === category)?.label}
                    </span>
                    <span className="break-all text-sm font-medium text-text-primary">
                      {node.name}
                    </span>
                  </div>
                  <p className="mt-1 break-all text-xs text-text-secondary">
                    {folder}
                  </p>
                  <p className="mt-2 text-sm text-text-primary">
                    {category === 'required'
                      ? '검색 가능한 글자가 추출되지 않았습니다.'
                      : category === 'review'
                        ? '이미지 속 글자를 인식하지 않아 검색에서 빠진 내용이 있을 수 있습니다.'
                        : node.index_partial_coverage
                          ? '일부 내용이 추출되지 않았습니다.'
                          : '인덱싱 처리 상태를 확인해야 합니다.'}
                  </p>
                  <div className="mt-1 break-words text-xs">
                    <SourceIndexStatus node={node} />
                  </div>
                  {category !== 'other' && !canRequest && (
                    <p className="mt-1 text-xs text-text-secondary">
                      {node.index_pdf_ocr_option
                        ? 'OCR 실행 정보를 확인할 수 없습니다. 자료 목록을 새로고침하세요.'
                        : '이 형식은 현재 화면에서 OCR을 실행할 수 없습니다.'}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {renderInspection(node)}
                  {category !== 'other' && canRequest && (
                    <PdfOcrChoice node={node} onConfirm={onConfirm} />
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
