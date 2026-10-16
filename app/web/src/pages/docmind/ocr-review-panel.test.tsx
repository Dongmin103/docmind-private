import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import type { DocMindHierarchyNode } from '@/services/docmind-service';
import { OcrReviewPanel } from './ocr-review-panel';
import { OcrAcknowledgementKey } from './ocr-progress';

const node: DocMindHierarchyNode = {
  file_id: 'file-ocr',
  document_id: 'doc-ocr',
  name: 'scan.pdf',
  relative_path: 'folder/scan.pdf',
  depth: 1,
  type: 'file',
  document_exists: true,
  index_state: 'PENDING',
  index_pdf_ocr_requested: true,
  index_pdf_ocr_job_state: 'DISCOVERED',
  index_pdf_ocr_version_id: 'version-1',
};
const props = {
  onConfirm: jest.fn().mockResolvedValue(undefined),
  renderInspection: () => null,
};
beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
});

it('merges acknowledgements from another already-open tab and retains older OCR results after a source update', () => {
  const current: DocMindHierarchyNode = {
    ...node,
    index_pdf_ocr_requested: false,
    index_pdf_ocr_version_id: null,
    index_pdf_ocr_runs: [
      {
        version_id: 'old-version',
        state: 'COMPLETE',
        cleanup_state: 'COMPLETE',
        error_code: null,
        completed: true,
        previous_version: true,
      },
    ],
  };
  render(<OcrReviewPanel {...props} nodes={[current]} />);
  expect(screen.getByText('OCR 완료')).toBeVisible();
  expect(screen.getByText('이전 원본 버전의 OCR 작업')).toBeVisible();
  localStorage.setItem(
    OcrAcknowledgementKey,
    JSON.stringify(['another-file:another-version']),
  );
  fireEvent.click(screen.getByRole('button', { name: '완료 확인' }));
  expect(JSON.parse(localStorage.getItem(OcrAcknowledgementKey)!)).toEqual([
    'another-file:another-version',
    'file-ocr:old-version',
  ]);
  expect(screen.queryByText('scan.pdf')).not.toBeInTheDocument();
});

it('keeps an OCR request through progress and completion until acknowledged, including reloads', () => {
  const view = render(<OcrReviewPanel {...props} nodes={[node]} />);
  expect(screen.getByText('scan.pdf')).toBeVisible();
  expect(screen.getByText('OCR 대기')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: '완료 확인' }),
  ).not.toBeInTheDocument();
  view.rerender(
    <OcrReviewPanel
      {...props}
      nodes={[
        {
          ...node,
          index_state: 'PROCESSING',
          index_pdf_ocr_job_state: 'PARSING',
        },
      ]}
    />,
  );
  expect(screen.getByText('OCR 글자 인식 중')).toBeVisible();
  const complete: DocMindHierarchyNode = {
    ...node,
    index_state: 'INDEXED',
    index_pdf_ocr_job_state: 'COMPLETE',
    index_cleanup_state: 'COMPLETE',
    searchable: true,
  };
  view.rerender(<OcrReviewPanel {...props} nodes={[complete]} />);
  expect(screen.getByText('OCR 완료')).toBeVisible();
  view.unmount();
  const reloaded = render(<OcrReviewPanel {...props} nodes={[complete]} />);
  fireEvent.click(screen.getByRole('button', { name: '완료 확인' }));
  expect(screen.queryByText('scan.pdf')).not.toBeInTheDocument();
  reloaded.unmount();
  const acknowledged = render(<OcrReviewPanel {...props} nodes={[complete]} />);
  expect(screen.queryByText('scan.pdf')).not.toBeInTheDocument();
  acknowledged.rerender(
    <OcrReviewPanel
      {...props}
      nodes={[{ ...node, index_pdf_ocr_version_id: 'version-2' }]}
    />,
  );
  expect(screen.getByText('OCR 대기')).toBeVisible();
});

it('keeps requested OCR failures visible and does not report an old searchable index as OCR completion', () => {
  const view = render(
    <OcrReviewPanel
      {...props}
      nodes={[
        {
          ...node,
          index_state: 'FAILED',
          index_pdf_ocr_job_state: 'FAILED',
          searchable: true,
          index_error_code: 'PARSER_PDF_OCR_NO_TEXT',
        },
      ]}
    />,
  );
  expect(screen.queryByText(/OCR 검토/)).toBeNull();
  expect(screen.getByText('OCR 실패')).toBeVisible();
  expect(screen.getByText(/PARSER_PDF_OCR_NO_TEXT/)).toBeVisible();
  expect(
    screen.queryByRole('button', { name: '완료 확인' }),
  ).not.toBeInTheDocument();
  view.rerender(
    <OcrReviewPanel
      {...props}
      nodes={[
        {
          ...node,
          index_state: 'CLEANUP',
          index_pdf_ocr_job_state: 'CLEANUP',
          searchable: true,
        },
      ]}
    />,
  );
  expect(screen.getByText('임시 파일 정리 중')).toBeVisible();
  expect(screen.queryByText('OCR 완료')).not.toBeInTheDocument();
});

it('shows accepted requests while waiting for the refreshed hierarchy', async () => {
  render(
    <OcrReviewPanel
      {...props}
      nodes={[
        {
          ...node,
          index_state: 'FAILED',
          index_pdf_ocr_requested: false,
          index_pdf_ocr_job_state: null,
          index_pdf_ocr_option: 'zero_text',
        },
      ]}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'OCR 선택' }));
  fireEvent.click(screen.getByRole('button', { name: 'OCR로 인덱싱' }));
  await waitFor(() =>
    expect(screen.getByText('OCR 요청 접수 · 상태 확인 중')).toBeVisible(),
  );
  const progress = within(
    screen.getByRole('region', { name: 'OCR 진행 및 결과' }),
  );
  expect(progress.getByText('scan.pdf')).toBeVisible();
  expect(
    screen.queryByRole('button', { name: 'OCR 선택' }),
  ).not.toBeInTheDocument();
});
