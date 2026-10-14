/*
 *  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
 *
 *  Licensed under the Apache License, Version 2.0 (the "License");
 *  you may not use this file except in compliance with the License.
 *  You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software
 *  distributed under the License is distributed on an "AS IS" BASIS,
 *  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *  See the License for the specific language governing permissions and
 *  limitations under the License.
 */

import { PaginationProps } from '@/interfaces/antd-compat';
import { ResponseGetType } from '@/interfaces/database/base';
import { IChunk } from '@/interfaces/database/chunk';
import { IKnowledgeFile } from '@/interfaces/database/document';
import chunkService from '@/services/chunk-service';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useDebounce } from 'ahooks';
import { useCallback, useState } from 'react';
import {
  useGetPaginationWithRouter,
  useHandleSearchChange,
} from './logic-hooks';
import { useGetKnowledgeSearchParams } from './route-hook';

export const ChunkKeys = {
  all: () => ['fetchChunkList'] as const,
  document: (datasetId: string, documentId: string) =>
    [...ChunkKeys.all(), datasetId, documentId] as const,
  list: (
    datasetId: string,
    documentId: string,
    page: number,
    size: number,
    search: string,
    available?: number,
  ) =>
    [
      ...ChunkKeys.document(datasetId, documentId),
      page,
      size,
      search,
      available,
    ] as const,
};

export interface IChunkListResult {
  error?: Error | null;
  retry?: () => unknown;
  searchString?: string;
  handleInputChange?: React.ChangeEventHandler<HTMLInputElement>;
  pagination: PaginationProps;
  setPagination?: (pagination: { page: number; pageSize?: number }) => void;
  available: number | undefined;
  handleSetAvailable: (available: number | undefined) => void;
  dataUpdatedAt?: number; // Timestamp when data was last updated - useful for cache busting
}

export const useSelectChunkList = () => {
  const queryClient = useQueryClient();
  const { knowledgeId, documentId } = useGetKnowledgeSearchParams();
  const data = queryClient.getQueriesData<{
    data: IChunk[];
    total: number;
    documentInfo: IKnowledgeFile;
  }>({ queryKey: ChunkKeys.document(knowledgeId, documentId) });

  return data?.at(-1)?.[1];
};

export const useFetchNextChunkList = (
  enabled = true,
): ResponseGetType<{
  data: IChunk[];
  total: number;
  documentInfo: IKnowledgeFile;
}> &
  IChunkListResult => {
  const { pagination, setPagination } = useGetPaginationWithRouter();
  const { documentId, knowledgeId } = useGetKnowledgeSearchParams();
  const { searchString, handleInputChange } = useHandleSearchChange();
  const [available, setAvailable] = useState<number | undefined>();
  const debouncedSearchString = useDebounce(searchString, { wait: 500 });

  const {
    data,
    isFetching: loading,
    dataUpdatedAt,
    error: requestError,
    refetch,
  } = useQuery({
    queryKey: ChunkKeys.list(
      knowledgeId,
      documentId,
      pagination.current,
      pagination.pageSize,
      debouncedSearchString,
      available,
    ),
    placeholderData: (previousData: any) =>
      previousData ?? { data: [], total: 0, documentInfo: {} }, // https://github.com/TanStack/query/issues/8183
    gcTime: 0,
    enabled: enabled && !!knowledgeId && !!documentId,
    queryFn: async () => {
      const { data } = await chunkService.chunkList({
        kb_id: knowledgeId,
        doc_id: documentId,
        page: pagination.current,
        size: Math.min(pagination.pageSize, 100),
        available_int: available,
        keywords: searchString,
      });
      if (data.code === 0) {
        const res = data.data;
        return {
          data: res.chunks,
          total: res.total,
          documentInfo: res.doc,
        };
      }

      return {
        data: [],
        total: 0,
        documentInfo: {} as IKnowledgeFile,
        errorMessage: data?.message || '문서의 청크를 불러오지 못했습니다.',
      };
    },
  });

  const onInputChange: React.ChangeEventHandler<HTMLInputElement> = useCallback(
    (e) => {
      setPagination({ page: 1 });
      handleInputChange(e);
    },
    [handleInputChange, setPagination],
  );

  const handleSetAvailable = useCallback(
    (a: number | undefined) => {
      setPagination({ page: 1 });
      setAvailable(a);
    },
    [setAvailable, setPagination],
  );

  return {
    data: data ?? { data: [], total: 0, documentInfo: {} as IKnowledgeFile },
    error:
      requestError ??
      (data?.errorMessage ? new Error(data.errorMessage) : null),
    retry: refetch,
    loading,
    pagination,
    setPagination,
    searchString,
    handleInputChange: onInputChange,
    available,
    handleSetAvailable,
    dataUpdatedAt, // Timestamp when data was last updated - useful for cache busting
  };
};
