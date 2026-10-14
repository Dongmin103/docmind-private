import chunkService from './chunk-service';
import documentService from './document-service';
import request from '@/utils/next-request';

jest.mock('@/utils/next-request', () => ({
  __esModule: true,
  default: jest.fn(),
}));

it('preserves chunk positions, images, references and version fencing while adapting REST names', async () => {
  const chunk = {
    id: 'chunk-1',
    content: 'Grounded text',
    document_id: 'document-1',
    docnm_kwd: 'document.pdf',
    img_id: 'image-1',
    available: false,
    position_int: [[2, 10, 20, 30, 40]],
    source_version_id: 'v1',
    chunk_set_id: 's1',
    document_relative_path: 'folder/document.pdf',
    document_url: '/document/document-1',
    provenance: [{ source_id: 'source-1' }],
    office_locator: { kind: 'xlsx', sheet: 'Sheet1' },
  };
  (request as unknown as jest.Mock).mockResolvedValue({
    data: {
      code: 0,
      data: {
        chunks: [chunk],
        total: 1,
        doc: {
          id: 'document-1',
          chunk_count: 1,
          source_version_id: 'v1',
          chunk_set_id: 's1',
        },
      },
    },
  });
  const response = await chunkService.chunkList({
    kb_id: 'dataset-1',
    doc_id: 'document-1',
    page: 2,
    size: 10,
    available_int: 0,
    keywords: 'Grounded',
  });
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      url: '/api/v1/datasets/dataset-1/documents/document-1/chunks',
      method: 'get',
      params: {
        page: 2,
        page_size: 10,
        available: 'false',
        keywords: 'Grounded',
      },
    }),
  );
  expect(response.data.data.chunks[0]).toMatchObject({
    ...chunk,
    chunk_id: 'chunk-1',
    content_with_weight: 'Grounded text',
    doc_id: 'document-1',
    doc_name: 'document.pdf',
    image_id: 'image-1',
    available_int: 0,
    positions: chunk.position_int,
  });
  expect(response.data.data.doc).toMatchObject({
    chunk_num: 1,
    source_version_id: 'v1',
    chunk_set_id: 's1',
  });
  expect(Object.keys(chunkService)).toEqual(['chunkList']);
});

it('keeps the ordinary document thumbnail GET contract in the neutral service', async () => {
  (request as unknown as jest.Mock).mockResolvedValue({
    data: { code: 0, data: {} },
  });
  await documentService.documentThumbnails({ doc_ids: ['document-1'] });
  expect(request).toHaveBeenLastCalledWith({
    url: '/api/v1/thumbnails',
    method: 'get',
    params: { doc_ids: ['document-1'] },
  });
});
