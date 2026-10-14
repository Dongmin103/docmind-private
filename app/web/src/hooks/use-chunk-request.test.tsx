import { renderHook } from '@testing-library/react';
import { ChunkKeys, useFetchNextChunkList } from './use-chunk-request';

let mockQueryOptions: any;
let mockQueryResult: any;
const mockChunkList = jest.fn();
const mockRefetch = jest.fn();

jest.mock('@/components/ui/message', () => ({
  __esModule: true,
  default: { success: jest.fn() },
}));
jest.mock('@/services/chunk-service', () => ({
  __esModule: true,
  default: { chunkList: (...args: any[]) => mockChunkList(...args) },
}));
jest.mock('@tanstack/react-query', () => ({
  useQuery: (options: any) => {
    mockQueryOptions = options;
    return mockQueryResult;
  },
}));
jest.mock('./logic-hooks', () => ({
  useGetPaginationWithRouter: () => ({
    pagination: { current: 1, pageSize: 10 },
    setPagination: jest.fn(),
  }),
  useHandleSearchChange: () => ({
    searchString: '',
    handleInputChange: jest.fn(),
  }),
}));
jest.mock('./route-hook', () => ({
  useGetKnowledgeSearchParams: () => ({
    knowledgeId: 'dataset',
    documentId: 'document',
  }),
}));

describe('chunk inspection error information', () => {
  beforeEach(() => {
    mockQueryResult = {
      data: { data: [], total: 0, documentInfo: {} },
      isFetching: false,
      refetch: mockRefetch,
    };
    mockChunkList.mockReset();
  });

  it('preserves a server rejection as error information', async () => {
    mockChunkList.mockResolvedValue({
      data: { code: 102, message: '문서 접근 권한이 없습니다.' },
    });
    const { rerender, result } = renderHook(() => useFetchNextChunkList());
    const response = await mockQueryOptions.queryFn();
    mockQueryResult = { ...mockQueryResult, data: response };
    rerender();
    expect(result.current.error?.message).toBe('문서 접근 권한이 없습니다.');
    expect(result.current.data.data).toEqual([]);
    expect(result.current.retry).toBe(mockRefetch);
  });

  it('keeps the document shape safe when a network request has no data', () => {
    mockQueryResult = {
      ...mockQueryResult,
      data: undefined,
      error: new Error('network unavailable'),
    };
    const { result } = renderHook(() => useFetchNextChunkList());
    expect(result.current.error?.message).toBe('network unavailable');
    expect(result.current.data).toEqual({
      data: [],
      total: 0,
      documentInfo: {},
    });
  });
});


it('requests the retained dataset/document IDs without representation filters', async () => {
  mockQueryResult = { data: undefined, refetch: mockRefetch };
  mockChunkList.mockResolvedValue({ data: { code: 0, data: { chunks: [], total: 0, doc: { source_version_id: 'v1', chunk_set_id: 's1' } } } });
  renderHook(() => useFetchNextChunkList());
  expect(mockQueryOptions.queryKey).toEqual(ChunkKeys.list('dataset', 'document', 1, 10, '', undefined));
  const response = await mockQueryOptions.queryFn();
  expect(mockChunkList).toHaveBeenLastCalledWith({ kb_id: 'dataset', doc_id: 'document', page: 1, size: 10, available_int: undefined, keywords: '' });
  expect(response.documentInfo).toMatchObject({ source_version_id: 'v1', chunk_set_id: 's1' });
});
