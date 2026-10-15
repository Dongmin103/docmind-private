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

import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import { mapDocumentForInspection } from './document-service';

const getDatasetId = (params: Record<string, any>) =>
  params.dataset_id || params.kb_id || params.knowledge_id;

const getDocumentId = (params: Record<string, any>) =>
  params.document_id || params.doc_id;

const mapChunkForInspection = (chunk: Record<string, any>) => ({
  ...chunk,
  chunk_id: chunk.chunk_id || chunk.id,
  content_with_weight: chunk.content_with_weight || chunk.content,
  doc_id: chunk.doc_id || chunk.document_id,
  doc_name: chunk.doc_name || chunk.docnm_kwd,
  image_id: chunk.image_id || chunk.img_id,
  important_kwd: chunk.important_kwd || chunk.important_keywords || [],
  question_kwd: chunk.question_kwd || chunk.questions || [],
  available_int: chunk.available_int ?? (chunk.available === false ? 0 : 1),
  positions: chunk.positions || chunk.position_int || [],
});

const getAvailableParam = (available?: number) => {
  if (available === undefined) {
    return undefined;
  }
  return available === 1 ? 'true' : 'false';
};

const chunkEndpoints = registerNextServer({
  detail: {
    url: ({ datasetId, documentId, chunkId }: { datasetId: string; documentId: string; chunkId: string }) =>
      api.chunkDetail(datasetId, documentId, chunkId),
    method: 'get',
  },
  list: {
    url: ({
      datasetId,
      documentId,
    }: {
      datasetId: string;
      documentId: string;
    }) => api.chunkList(datasetId, documentId),
    method: 'get',
  },
});

const chunkService = {
  getChunk: async (params: Record<string, any>) => {
    const response = await chunkEndpoints.detail({
      datasetId: getDatasetId(params),
      documentId: getDocumentId(params),
      chunkId: params.chunk_id || params.id,
    }, true);
    if (response.data?.code === 0 && response.data.data) {
      response.data.data = mapChunkForInspection(response.data.data);
    }
    return response;
  },
  chunkList: async (params: Record<string, any>) => {
    const response = await chunkEndpoints.list(
      {
        datasetId: getDatasetId(params),
        documentId: getDocumentId(params),
        params: {
          page: params.page,
          page_size: params.page_size || params.size,
          keywords: params.keywords,
          available: getAvailableParam(params.available_int),
        },
      },
      true,
    );
    if (response.data?.code === 0) {
      response.data.data = {
        ...response.data.data,
        chunks: (response.data.data?.chunks || []).map(mapChunkForInspection),
        doc: mapDocumentForInspection(response.data.data?.doc || {}),
      };
    }
    return response;
  },
};

export default chunkService;
