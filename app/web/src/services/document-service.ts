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

export const mapDocumentForInspection = (doc: Record<string, any>) => ({
  ...doc,
  chunk_num: doc.chunk_num ?? doc.chunk_count,
  kb_id: doc.kb_id || doc.dataset_id,
  parser_id: doc.parser_id || doc.chunk_method,
});

const endpoints = registerNextServer({
  documentThumbnails: { url: api.documentThumbnails, method: 'get' },
});

export default {
  documentThumbnails: (params: { doc_ids: string[] }) =>
    endpoints.documentThumbnails({ params }, true),
};
