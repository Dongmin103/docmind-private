jest.mock('react-router', () => ({
  useLocation: jest.fn(),
  useNavigate: jest.fn(),
  useSearchParams: jest.fn(),
}));

import { renderHook } from '@testing-library/react';
import { useSearchParams } from 'react-router';
import { useGetKnowledgeSearchParams, InspectionSource, parseInspectionSource } from './route-hook';

describe('parseInspectionSource', () => {
  it('accepts the bounded docmind source enum', () => {
    expect(parseInspectionSource('docmind')).toBe(InspectionSource.DocMind);
  });

  it.each([
    null,
    '',
    'DocMind',
    'ragflow',
    '/docmind',
    'https://example.com/docmind',
  ])('rejects unsupported source value %p', (source) => {
    expect(parseInspectionSource(source)).toBeUndefined();
  });
});


it('retains direct and embedded inspection dataset/document query IDs', () => {
  (useSearchParams as jest.Mock).mockReturnValue([new URLSearchParams('id=dataset-1&doc_id=document-1&type=pdf&source=docmind')]);
  const { result } = renderHook(() => useGetKnowledgeSearchParams());
  expect(result.current).toEqual({ knowledgeId: 'dataset-1', documentId: 'document-1', type: 'pdf' });
});
