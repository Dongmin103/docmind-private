import DocumentPreview from '@/components/document-preview';
import TemporaryOriginalPreview from '@/components/document-preview/temporary-original-preview';
import DocumentHeader from '@/components/document-preview/document-header';
import { IHighlight } from 'react-pdf-highlighter';

interface DocumentViewSwitchProps {
  documentInfo?: {
    size: number;
    name: string;
    create_date: string;
  };
  fileType: string;
  highlights: IHighlight[];
  setWidthAndHeight: (width: number, height: number) => void;
  url: string;
  documentId?: string;
  sourceVersionId?: string;
  chunkSetId?: string;
  temporaryOriginal?: boolean;
  onChunkSetChanged?: () => void;
}

export default function DocumentViewSwitch({
  documentInfo,
  fileType,
  highlights,
  setWidthAndHeight,
  url,
  documentId,
  sourceVersionId,
  chunkSetId,
  temporaryOriginal,
  onChunkSetChanged,
}: DocumentViewSwitchProps) {
  return (
    <>
      <DocumentHeader
        className="w-full min-w-0 @lg:flex-1 @lg:w-auto"
        wrapperClassName="flex flex-col items-stretch @lg:flex-row @lg:items-center @lg:justify-between p-5 pb-0 gap-2"
        size={documentInfo?.size ?? 0}
        name={documentInfo?.name ?? ''}
        create_date={documentInfo?.create_date ?? ''}
      />

      <div className="flex-1 h-0 min-h-0 overflow-hidden p-5 pt-2.5 [&>section]:h-full [&>section]:min-h-0">
        {temporaryOriginal && documentId ? (
          <div className="h-full min-h-0">
            <TemporaryOriginalPreview
              key={`${documentId}:${sourceVersionId ?? ''}`}
              documentId={documentId}
              sourceFormat={fileType}
              sourceVersionId={sourceVersionId}
              chunkSetId={chunkSetId}
              onChunkSetChanged={onChunkSetChanged}
              className="h-full min-h-0 overflow-auto [&_img]:max-w-full [&_img]:h-auto"
              highlights={highlights}
              setWidthAndHeight={setWidthAndHeight}
            />
          </div>
        ) : (
          <DocumentPreview
            className="h-full min-h-0 overflow-auto [&_img]:max-w-full [&_img]:h-auto"
            fileType={fileType}
            highlights={highlights}
            setWidthAndHeight={setWidthAndHeight}
            url={url}
          />
        )}
      </div>
    </>
  );
}
