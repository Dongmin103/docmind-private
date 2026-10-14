import { render, screen, fireEvent } from '@testing-library/react';
import PdfDrawer from './index';
import { ReferenceDocumentList } from '../next-message-item/reference-document-list';
import DocumentPreview from '../document-preview';
const mockHighlights = [{ position: [1, 2, 3, 4] }];
const mockSetWidthAndHeight = jest.fn();
jest.mock('@/hooks/use-document-request', () => ({
  useGetDocumentUrl: (id: string) => () =>
    '/api/v1/documents/' + id + '/preview',
  useGetChunkHighlights: () => ({ highlights: mockHighlights, setWidthAndHeight: mockSetWidthAndHeight }),
}));
jest.mock('@/components/document-preview', () => ({
  __esModule: true,
  default: jest.fn(() => null),
}));
jest.mock('@/components/icon-font', () => ({ FileIcon: () => null }));
jest.mock('../file-icon', () => () => null);
jest.mock('@/utils/common-util', () => ({ middleEllipsis: (x: string) => x }));
jest.mock('@/components/ui/modal/modal', () => ({
  Modal: ({ children }: any) => <div>{children}</div>,
}));
it('opens a retained citation using the document preview endpoint', () => {
  render(
    <ReferenceDocumentList
      list={[{ doc_id: 'doc', doc_name: 'citation.pdf', url: '' } as any]}
    />,
  );
  fireEvent.click(screen.getByText('citation.pdf'));
  expect(DocumentPreview).toHaveBeenLastCalledWith(
    expect.objectContaining({
      url: '/api/v1/documents/doc/preview',
      fileType: 'pdf',
      highlights: mockHighlights,
      setWidthAndHeight: mockSetWidthAndHeight,
    }),
    expect.anything(),
  );
});
it('preserves highlighted chunk preview props and layout', () => {
  render(
    <PdfDrawer
      visible
      hideModal={jest.fn()}
      documentId="doc"
      chunk={{ document_name: 'test.PDF', positions: [[1, 2]] }}
    />,
  );
  expect(DocumentPreview).toHaveBeenLastCalledWith(
    expect.objectContaining({
      url: '/api/v1/documents/doc/preview',
      fileType: 'pdf',
      highlights: mockHighlights,
      setWidthAndHeight: mockSetWidthAndHeight,
      className:
        '!h-[calc(100dvh-300px)] overflow-auto border-none padding-0 max-h-full',
    }),
    expect.anything(),
  );
});
