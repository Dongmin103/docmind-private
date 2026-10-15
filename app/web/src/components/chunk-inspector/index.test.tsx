import { act, fireEvent, render, screen } from '@testing-library/react';
import * as React from 'react';
import Chunk from './index';

jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));
jest.mock('@/components/ui/resizable', () => ({
  ResizableHandle: () => <div />,
  ResizablePanel: ({ children }: any) => <div>{children}</div>,
  ResizablePanelGroup: ({ children }: any) => <div>{children}</div>,
}));

Object.assign(globalThis, { React });

let mockSearch = '';
let mockLayoutSize = { width: 1100, height: 700 };
let mockDocumentInfo: any = {
  name: 'document.pdf',
  parser_id: 'na',
  type: 'pdf',
};
const mockSetLayout = jest.fn();

jest.mock('ahooks', () => ({
  ...jest.requireActual('ahooks'),
  useSize: () => mockLayoutSize,
}));
jest.mock('react-resizable-panels', () => ({
  PanelGroup: jest
    .requireActual('react')
    .forwardRef(({ children, direction }: any, ref: any) => {
      const ReactLib = jest.requireActual('react');
      ReactLib.useImperativeHandle(ref, () => ({ setLayout: mockSetLayout }));
      return ReactLib.createElement(
        'div',
        { 'data-testid': 'panel-group', 'data-direction': direction },
        children,
      );
    }),
}));
const mockBack = jest.fn();
let mockChunks: any[] = [];
let mockInspectionError: Error | null = null;
const mockRetryInspection = jest.fn();
const mockSearchChange = jest.fn();

jest.mock('react-router', () => ({
  useLocation: () => ({ pathname: '/chunk/parsed/chunks' }),
  useNavigate: () => mockBack,
  useSearchParams: () => [new URLSearchParams(mockSearch)],
}));

jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

jest.mock('@/hooks/use-chunk-request', () => ({
  useFetchNextChunkList: () => {
    return {
      data: {
        data: mockChunks,
        documentInfo: mockDocumentInfo,
        total: 0,
      },
      pagination: { current: 1, onChange: jest.fn(), pageSize: 10 },
      loading: false,
      searchString: '',
      handleInputChange: mockSearchChange,
      available: undefined,
      handleSetAvailable: jest.fn(),
      dataUpdatedAt: 0,
      error: mockInspectionError,
      retry: mockRetryInspection,
    };
  },
  useSelectChunkList: () => ({ data: mockChunks }),
}));

let mockPreviewProps: any;
let mockTemporaryProps: any;
jest.mock('@/components/document-preview', () => (props: any) => {
  mockPreviewProps = props;
  return <div data-testid="source-preview" data-type={props.fileType} />;
});
jest.mock(
  '@/components/document-preview/temporary-original-preview',
  () => (props: any) => {
    mockTemporaryProps = props;
    return (
      <button onClick={props.onChunkSetChanged}>
        Refresh temporary preview chunks
      </button>
    );
  },
);
jest.mock(
  '@/components/document-preview/document-header',
  () =>
    ({ children }: any) => <div>{children}</div>,
);
jest.mock('./components/document-view-switch', () =>
  jest.requireActual('./components/document-view-switch'),
);
jest.mock('@/components/ui/segmented', () => ({
  Segmented: ({ options, onChange }: any) => (
    <div>
      {options.map((option: any) => (
        <button key={option.value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  ),
}));
jest.mock('@/components/document-preview/hooks', () => ({
  useGetDocumentUrl: () => '',
}));
jest.mock('@/components/ui/message', () => ({ warning: jest.fn() }));
jest.mock('@/components/ui/ragflow-pagination', () => ({
  RAGFlowPagination: () => null,
}));
jest.mock('@/components/ui/spin', () => ({
  Spin: ({ children }: { children?: JSX.Element }) => children ?? null,
}));
jest.mock('lucide-react', () => ({
  ...jest.requireActual('lucide-react'),
  LucideArrowBigLeft: () => null,
  File: () => null,
  LayoutList: () => null,
  LucideFilter: () => null,
}));

const renderChunk = (search = '') => {
  mockSearch = search;
  return render(<Chunk />);
};

beforeEach(() => {
  mockBack.mockClear();
  mockLayoutSize = { width: 1100, height: 700 };
  mockDocumentInfo = { name: 'document.pdf', parser_id: 'na', type: 'pdf' };
  mockSetLayout.mockClear();
  mockChunks = [];
  mockInspectionError = null;
  mockRetryInspection.mockClear();
  mockSearchChange.mockClear();
});

describe('RAGFlow chunk inspection source', () => {
  it('shows the DocMind source beside the existing Back button', () => {
    renderChunk('?id=dataset-1&doc_id=document-1&source=docmind');

    expect(screen.getByRole('button', { name: 'common.back' })).toBeVisible();
    expect(screen.getByText('DocMind에서 연 인덱싱 검사')).toBeVisible();
  });

  it.each([
    '',
    '?source=ragflow',
    '?source=DocMind',
    '?source=https%3A%2F%2Fexample.com%2Fdocmind',
  ])('does not show the source badge for unsupported source %p', (search) => {
    renderChunk(search);

    expect(screen.getByRole('button', { name: 'common.back' })).toBeVisible();
    expect(
      screen.queryByText('DocMind에서 연 인덱싱 검사'),
    ).not.toBeInTheDocument();
  });
});

describe('embedded read-only chunk viewer', () => {
  it('uses the embedded layout and invokes the supplied back callback', () => {
    const onBack = jest.fn();

    const { container } = render(<Chunk embedded onBack={onBack} />);

    expect(container.querySelector('main')).toHaveClass('h-full', 'min-h-0');
    expect(container.querySelector('main')).not.toHaveClass('h-dvh');
    expect(screen.getByText('document.pdf')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'common.back' }));

    expect(onBack).toHaveBeenCalledTimes(1);
    expect(mockBack).not.toHaveBeenCalled();
  });

  it('shows Korean document content with search and without legacy controls', () => {
    mockChunks = [
      {
        chunk_id: 'chunk-1',
        content_with_weight: 'Selected chunk text',
        positions: [],
        available_int: 1,
      },
    ];

    render(<Chunk embedded onBack={jest.fn()} />);

    expect(screen.getByRole('textbox')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Selected' } });
    expect(mockSearchChange).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('toolbar').querySelector('button')).toBeNull();
    expect(screen.queryByRole('button', { name: 'ellipse' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'full' })).toBeNull();
    expect(screen.getByRole('heading', { name: '문서 내용' })).toBeVisible();
    expect(screen.getAllByText('문서 내용')).toHaveLength(2);
    expect(screen.getByText('검색과 AI 답변에 사용되는 문서 내용입니다.')).toBeVisible();
    expect(screen.queryByText('chunk.chunkResult')).toBeNull();
    expect(screen.getByText('Selected chunk text')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'add chunk' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'edit chunk' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'delete chunks' })).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows a recoverable inspection error instead of an empty document', () => {
    mockInspectionError = new Error('문서에 접근할 수 없습니다.');
    render(<Chunk embedded onBack={jest.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      '문서에 접근할 수 없습니다.',
    );
    expect(screen.queryByTestId('chunk-card')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    expect(mockRetryInspection).toHaveBeenCalledTimes(1);
  });

  it('makes the standalone inspector read-only by default', () => {
    mockChunks = [
      {
        chunk_id: 'chunk-1',
        content_with_weight: 'Selected chunk text',
        positions: [],
        available_int: 1,
      },
    ];

    const { container } = renderChunk();

    expect(container.querySelector('main')).toHaveClass('h-dvh');
    expect(screen.queryByRole('button', { name: 'add chunk' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'edit chunk' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'delete chunks' })).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

test('breakpoint changes retain the source preview without representations', async () => {
  const view = render(<Chunk embedded />);
  expect(screen.queryByRole('button', { name: 'Artifact' })).toBeNull();
  mockLayoutSize = { width: 564, height: 700 };
  await act(async () => view.rerender(<Chunk embedded />));
  expect(screen.getByTestId('source-preview')).toBeVisible();
  expect(mockSetLayout).toHaveBeenLastCalledWith([70, 30]);
  mockLayoutSize = { width: 1100, height: 700 };
  await act(async () => view.rerender(<Chunk embedded />));
  expect(mockSetLayout).toHaveBeenLastCalledWith([40, 60]);
});

test.each(['XLS', 'XLSX'])(
  'normalizes %s document metadata before mounting the source preview',
  (extension) => {
    mockDocumentInfo = {
      name: 'table.' + extension,
      parser_id: 'na',
      type: 'doc',
    };
    render(<Chunk embedded />);
    expect(screen.getByTestId('source-preview')).toHaveAttribute(
      'data-type',
      extension.toLowerCase(),
    );
  },
);

test.each([false, true])(
  'DocMind preview passes fenced IDs and refreshes list (embedded=%s)',
  (embedded) => {
    mockSearch = '?id=dataset-1&doc_id=document-1&source=docmind';
    mockDocumentInfo = {
      id: 'document-1',
      name: 'document.pdf',
      type: 'pdf',
      source_version_id: 'source-v1',
      chunk_set_id: 'chunks-v1',
    };
    const view = render(<Chunk embedded={embedded} />);
    expect(mockTemporaryProps).toMatchObject({
      documentId: 'document-1',
      sourceVersionId: 'source-v1',
      chunkSetId: 'chunks-v1',
      sourceFormat: 'pdf',
    });
    fireEvent.click(
      screen.getByRole('button', { name: 'Refresh temporary preview chunks' }),
    );
    expect(mockRetryInspection).toHaveBeenCalledTimes(1);
    mockDocumentInfo = {
      ...mockDocumentInfo,
      source_version_id: 'source-v2',
      chunk_set_id: 'chunks-v2',
    };
    view.rerender(<Chunk embedded={embedded} />);
    expect(mockTemporaryProps).toMatchObject({
      sourceVersionId: 'source-v2',
      chunkSetId: 'chunks-v2',
    });
  },
);

test('selects actual chunk text/image positions and refreshes preview dimensions', () => {
  mockSearch = '';
  mockChunks = [
    {
      chunk_id: 'positioned',
      available_int: 1,
      content_with_weight: 'Positioned text',
      positions: [[2, 10, 20, 30, 40]],
    },
  ];
  render(<Chunk />);
  fireEvent.click(screen.getByText('Positioned text'));
  expect(mockPreviewProps.highlights[0]).toMatchObject({
    content: { text: 'Positioned text' },
    position: {
      pageNumber: 2,
      boundingRect: {
        x1: 10,
        x2: 20,
        y1: 30,
        y2: 40,
        width: 849,
        height: 1200,
      },
    },
  });
  act(() => mockPreviewProps.setWidthAndHeight(1000, 1400));
  expect(mockPreviewProps.highlights[0].position.boundingRect).toMatchObject({
    width: 1000,
    height: 1400,
  });
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.queryByRole('switch')).toBeNull();
  fireEvent.doubleClick(screen.getByText('Positioned text'));
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(screen.getByText('Positioned text')).not.toHaveClass('line-clamp-3');
  expect(screen.getByText('Positioned text')).toBeVisible();
});

test('direct Back returns to the retained DocMind library', () => {
  renderChunk();
  fireEvent.click(screen.getByRole('button', { name: 'common.back' }));
  expect(mockBack).toHaveBeenCalledWith('/docmind?view=library');
});
