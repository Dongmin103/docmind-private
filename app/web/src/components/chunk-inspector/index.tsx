import { useFetchNextChunkList } from '@/hooks/use-chunk-request';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import ChunkCard from './components/chunk-card';
import {
  useGetChunkHighlights,
  useHandleChunkCardClick,
} from './hooks';

import DocumentViewSwitch from './components/document-view-switch';

import { useGetDocumentUrl } from '@/components/document-preview/hooks';
import { PageHeader } from '@/components/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SearchInput } from '@/components/ui/input';
import { Card, CardContent } from '@/components/ui/card';
import { RAGFlowPagination } from '@/components/ui/ragflow-pagination';
import { ResizableHandle, ResizablePanel } from '@/components/ui/resizable';
import { Spin } from '@/components/ui/spin';
import { useNavigate } from 'react-router';
import { InspectionSource, useInspectionSource } from '@/hooks/route-hook';
import { cn } from '@/lib/utils';
import { LucideArrowBigLeft } from 'lucide-react';
import { useSize } from 'ahooks';
import {
  PanelGroup,
  type ImperativePanelGroupHandle,
} from 'react-resizable-panels';
import { getExtension } from '@/utils/document-util';

export interface ChunkProps {
  embedded?: boolean;
  onBack?: () => void;
}

function Chunk({ embedded = false, onBack }: ChunkProps) {
  const [layoutContainer, setLayoutContainer] = useState<HTMLDivElement | null>(
    null,
  );
  const layoutSize = useSize(layoutContainer);
  const narrowLayout = !!layoutSize?.width && layoutSize.width < 900;
  const panelGroupRef = useRef<ImperativePanelGroupHandle>(null);
  const navigate = useNavigate();
  const {
    data: { documentInfo, data = [], total },
    pagination,
    searchString,
    handleInputChange,
    loading,
    dataUpdatedAt,
    error: inspectionError,
    retry: retryChunkList,
  } = useFetchNextChunkList();
  useLayoutEffect(() => {
    panelGroupRef.current?.setLayout(narrowLayout ? [70, 30] : [40, 60]);
  }, [narrowLayout, inspectionError]);

  const { handleChunkCardClick, selectedChunkId } = useHandleChunkCardClick();

  const { t } = useTranslation();
  const [chunkList, setChunkList] = useState(data);
  const inspectionSource = useInspectionSource();
  const fileUrl = useGetDocumentUrl();
  const handleBack = onBack ?? (() => navigate('/docmind?view=library'));
  const retryInspection = () => {
    void retryChunkList?.();
  };
  useEffect(() => {
    setChunkList(data);
  }, [data]);
  const { highlights, setWidthAndHeight } =
    useGetChunkHighlights(selectedChunkId);

  const fileType = useMemo(() => {
    switch (documentInfo?.type) {
      case 'doc':
        return getExtension(documentInfo?.name ?? '') || 'doc';
      case 'visual':
        return getExtension(documentInfo?.name ?? '') || 'visual';
      case 'docx':
      case 'txt':
      case 'md':
      case 'mdx':
      case 'pdf':
        return documentInfo?.type;
    }
    return 'unknown';
  }, [documentInfo]);

  return (
    <main
      className={cn('flex flex-col', embedded ? 'h-full min-h-0' : 'h-dvh')}
      data-embedded={embedded || undefined}
    >
      {embedded ? (
        <header className="flex shrink-0 items-center gap-3 border-b-0.5 border-border-button bg-bg-base px-5 py-3">
          <Button variant="outline" size="sm" onClick={handleBack}>
            <LucideArrowBigLeft />
            {t('common.back')}
          </Button>
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-text-primary">
              {documentInfo?.name}
            </div>
            <div className="text-xs text-text-secondary">
              문서 내용
            </div>
          </div>
          <Badge variant="secondary" className="ml-auto shrink-0">
            보기 전용
          </Badge>
        </header>
      ) : (
        <PageHeader>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={handleBack}>
              <LucideArrowBigLeft />
              {t('common.back')}
            </Button>
            {inspectionSource === InspectionSource.DocMind && (
              <Badge variant="secondary">DocMind에서 연 인덱싱 검사</Badge>
            )}
          </div>
        </PageHeader>
      )}

      {inspectionError ? (
        <section
          role="alert"
          className="m-5 rounded-lg border border-border-button p-5"
        >
          <h2 className="font-semibold">문서를 불러오지 못했습니다.</h2>
          <p className="mt-2 text-sm text-text-secondary">
            {inspectionError.message}
          </p>
          <p className="mt-1 text-sm text-text-secondary">
            문서가 삭제되었거나 접근 권한이 변경되었을 수 있습니다.
          </p>
          <Button
            className="mt-4"
            variant="outline"
            onClick={retryInspection}
            disabled={loading}
          >
            다시 불러오기
          </Button>
        </section>
      ) : (
        <Card
          className={cn(
            'flex-1 h-0 min-h-0 p-0 bg-transparent shadow-none',
            embedded ? 'm-0 rounded-none border-0' : 'mx-5 mb-5',
          )}
        >
          <CardContent
            ref={setLayoutContainer}
            className="p-0 h-full min-w-0 flex"
          >
            <PanelGroup
              ref={panelGroupRef}
              direction={narrowLayout ? 'vertical' : 'horizontal'}
              className="flex h-full w-full flex-1 min-w-0 data-[panel-group-direction=vertical]:flex-col"
            >
              <ResizablePanel defaultSize={narrowLayout ? 70 : 40} minSize={30}>
                <article className="@container h-full min-w-0 flex flex-col">
                  <DocumentViewSwitch
                    documentInfo={documentInfo}
                    sourceVersionId={documentInfo?.source_version_id}
                    chunkSetId={documentInfo?.chunk_set_id}
                    documentId={documentInfo?.id}
                    temporaryOriginal={
                      inspectionSource === InspectionSource.DocMind
                    }
                    onChunkSetChanged={retryInspection}
                    fileType={fileType}
                    highlights={highlights}
                    setWidthAndHeight={setWidthAndHeight}
                    url={fileUrl}
                  />
                </article>
              </ResizablePanel>

              <ResizableHandle
                withHandle
                className={
                  narrowLayout
                    ? 'bg-border-button h-[0.5px]'
                    : 'bg-border-button w-[0.5px]'
                }
              />

              <ResizablePanel defaultSize={narrowLayout ? 30 : 60} minSize={30}>
                <article className="h-full flex flex-col">
                  <header className="flex-0 p-5 pb-2.5 border-b-0.5 border-b-border-button">
                    <h2 className="text-[24px]">문서 내용</h2>
                    <div className="text-[14px] text-text-secondary">
                      검색과 AI 답변에 사용되는 문서 내용입니다.
                    </div>
                  </header>

                  <Spin spinning={loading} className="flex-1 h-0" size="large">
                    <div className="relative @container h-full p-5 overflow-hidden flex flex-col">
                      <div className="flex justify-end pb-5" role="toolbar">
                        <SearchInput
                          className="w-28"
                          placeholder={t('common.search')}
                          onChange={handleInputChange}
                          value={searchString}
                        />
                      </div>
                      <div className="space-y-4 flex-1 overflow-y-auto min-h-0">
                        {chunkList.map((item) => (
                          <ChunkCard
                            item={item}
                            key={item.chunk_id}
                            clickChunkCard={handleChunkCardClick}
                            selected={item.chunk_id === selectedChunkId}
                            t={dataUpdatedAt}
                          />
                        ))}
                      </div>

                      <footer className="mt-5">
                        <RAGFlowPagination
                          pageSize={pagination.pageSize}
                          current={pagination.current}
                          total={total}
                          onChange={pagination.onChange}
                        />
                      </footer>
                    </div>
                  </Spin>
                </article>
              </ResizablePanel>
            </PanelGroup>
          </CardContent>
        </Card>
      )}
    </main>
  );
}

export default Chunk;
