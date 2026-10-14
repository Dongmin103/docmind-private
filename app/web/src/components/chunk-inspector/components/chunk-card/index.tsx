import Image from '@/components/image';
import { Card } from '@/components/ui/card';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import type { ChunkDocType, IChunk } from '@/interfaces/database/chunk';
import { cn } from '@/lib/utils';
import { sanitizeHtmlWithImagesAsText } from '@/utils/dom-util';
import { getSourceLocations } from '@/utils/source-location';
import classNames from 'classnames';
import { useTranslation } from 'react-i18next';
import { ChunkTextMode } from '../../constant';
import styles from './index.module.less';

interface IProps {
  item: IChunk;
  selected: boolean;
  clickChunkCard: (chunkId: string) => void;
  textMode: ChunkTextMode;
  t?: string | number; // Cache-busting key for images
}

const ChunkCard = ({
  item,
  selected,
  clickChunkCard,
  textMode,
  t: imageCacheKey,
}: IProps) => {
  const { t } = useTranslation();
  const handleContentClick = () => {
    clickChunkCard(item.chunk_id);
  };

  const chunkType =
    ((item.doc_type_kwd &&
      String(item.doc_type_kwd)?.toLowerCase()) as ChunkDocType) || 'text';
  const parser = item.parser_platform;
  const locatorLabel = getSourceLocations(item);

  return (
    <Card
      as="article"
      className={classNames(
        'relative flex-none p-3 pt-6 shadow-none transition-colors',
        selected && 'bg-text-primary/15',
      )}
    >
      <span
        className="
        absolute top-0 right-0 px-4 py-1
        leading-none text-xs text-text-disabled
        bg-bg-card rounded-bl-2xl rounded-tr-lg
        border-l-0.5 border-b-0.5 border-border-button"
      >
        {t(`chunk.docType.${chunkType}`)}
      </span>

      <div className="flex items-start justify-between gap-2.5">
        {/* Using <Tooltip> instead of <Popover> to avoid flickering when hovering over the image */}
        {item.image_id && (
          <Tooltip>
            <TooltipTrigger>
              <Image
                t={imageCacheKey}
                id={item.image_id}
                className="mt-1 rounded !w-28 object-contain"
              />
            </TooltipTrigger>

            <TooltipContent
              className="p-0"
              align="start"
              side="left"
              sideOffset={-20}
              tabIndex={-1}
            >
              <Image
                t={imageCacheKey}
                id={item.image_id}
                className="size-full max-w-[50vw] max-h-[50vh] object-contain"
              />
            </TooltipContent>
          </Tooltip>
        )}

        <section
          className={cn(styles.content, 'flex-1')}
          onClick={handleContentClick}
        >
          {(parser || locatorLabel) && (
            <div className="mb-2 text-xs text-text-secondary">
              {parser && (
                <span>
                  {parser.parser_name} {parser.parser_version}
                </span>
              )}
              {parser && locatorLabel && <span> · </span>}
              {locatorLabel && <span>{locatorLabel}</span>}
            </div>
          )}
          <div
            dangerouslySetInnerHTML={{
              __html: sanitizeHtmlWithImagesAsText(
                item.display_html || item.content_with_weight,
              ),
            }}
            className={classNames(
              'whitespace-pre-wrap break-words',
              textMode === ChunkTextMode.Ellipse && 'line-clamp-3',
            )}
          />
        </section>
      </div>
    </Card>
  );
};

export default ChunkCard;
