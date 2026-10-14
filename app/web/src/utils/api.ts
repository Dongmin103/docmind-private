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

const webAPI = `/v1`;
const restAPIv1 = `/api/v1`;

export { restAPIv1, webAPI };

export default {
  // user
  login: `${restAPIv1}/auth/login`,
  logout: `${restAPIv1}/auth/logout`,
  register: `${restAPIv1}/users`,
  setting: `${restAPIv1}/users/me`,
  userInfo: `${restAPIv1}/users/me`,
  tenantInfo: `${restAPIv1}/users/me/models`,
  loginChannels: `${restAPIv1}/auth/login/channels`,
  loginChannel: (channel: string) => `${restAPIv1}/auth/login/${channel}`,

  // team
  addTenantUser: (tenantId: string) => `${restAPIv1}/tenants/${tenantId}/users`,
  listTenantUser: (tenantId: string) =>
    `${restAPIv1}/tenants/${tenantId}/users`,
  deleteTenantUser: (tenantId: string) =>
    `${restAPIv1}/tenants/${tenantId}/users`,
  listTenant: `${restAPIv1}/tenants`,
  agreeTenant: (tenantId: string) => `${restAPIv1}/tenants/${tenantId}`,

  // llm model
  listAllAddedModels: `${restAPIv1}/models`,
  defaultModel: `${restAPIv1}/models/default`,
  // AIMLAPI agent-authorization (OAuth device grant) — obtain a key from the provider dialog
  aimlapiAuthorizeStart: `${restAPIv1}/llm/aimlapi/authorize/start`,
  aimlapiAuthorizePoll: `${restAPIv1}/llm/aimlapi/authorize/poll`,
  listProviders: `${restAPIv1}/providers`,
  addProvider: `${restAPIv1}/providers`,
  addProviderInstance: ({ llm_factory }: { llm_factory: string }) =>
    `${restAPIv1}/providers/${llm_factory}/instances`,
  verifyProviderConnection: ({ provider_name }: { provider_name: string }) =>
    `${restAPIv1}/providers/${provider_name}/connection`,
  listProviderModels: ({ provider_name }: { provider_name: string }) =>
    `${restAPIv1}/providers/${provider_name}/models`,
  listProviderInstances: ({ provider_name }: { provider_name: string }) =>
    `${restAPIv1}/providers/${provider_name}/instances`,
  listInstanceModels: ({
    provider_name,
    instance_name,
  }: {
    provider_name: string;
    instance_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models`,
  showProviderInstance: ({
    provider_name,
    id,
  }: {
    provider_name: string;
    id: string;
  }) => `${restAPIv1}/providers/${provider_name}/instances/${id}`,
  addInstanceModel: ({
    provider_name,
    instance_name,
  }: {
    provider_name: string;
    instance_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models`,
  editInstanceModel: ({
    provider_name,
    instance_name,
  }: {
    provider_name: string;
    instance_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models`,
  deleteProviderInstance: ({ provider_name }: { provider_name: string }) =>
    `${restAPIv1}/providers/${provider_name}/instances`,
  updateProviderInstance: ({
    provider_name,
    id,
  }: {
    provider_name: string;
    id: string;
  }) => `${restAPIv1}/providers/${provider_name}/instances/${id}`,
  updateModelStatus: ({
    provider_name,
    instance_name,
    model_name,
  }: {
    provider_name: string;
    instance_name: string;
    model_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models/${model_name}`,
  patchInstanceModel: ({
    provider_name,
    instance_name,
    model_name,
  }: {
    provider_name: string;
    instance_name: string;
    model_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models/${model_name}`,
  deleteInstanceModels: ({
    provider_name,
    instance_name,
  }: {
    provider_name: string;
    instance_name: string;
  }) =>
    `${restAPIv1}/providers/${provider_name}/instances/${instance_name}/models`,

  chatsTranscriptions: `${restAPIv1}/chat/audio/transcription`,

  // chunk
  chunkList: (datasetId: string, documentId: string) =>
    `${restAPIv1}/datasets/${datasetId}/documents/${documentId}/chunks`,
  chunkDetail: (datasetId: string, documentId: string, chunkId: string) =>
    `${restAPIv1}/datasets/${datasetId}/documents/${documentId}/chunks/${chunkId}`,
  // document
  getDatasetDocumentFileDownload: (datasetId: string, documentId: string) =>
    `${restAPIv1}/datasets/${datasetId}/documents/${documentId}`,
  documentThumbnails: `${restAPIv1}/thumbnails`,
  getDocumentFile: `${restAPIv1}/documents`,
  documentInfoUpload: `${restAPIv1}/documents/upload`,
  // chat
  createChat: `${restAPIv1}/chats`,
  listChats: `${restAPIv1}/chats`,
  getChat: (chatId: string) => `${restAPIv1}/chats/${chatId}`,
  updateChat: (chatId: string) => `${restAPIv1}/chats/${chatId}`,
  patchChat: (chatId: string) => `${restAPIv1}/chats/${chatId}`,
  deleteChat: (chatId: string) => `${restAPIv1}/chats/${chatId}`,
  bulkDeleteChats: `${restAPIv1}/chats`,
  createSession: (chatId: string) => `${restAPIv1}/chats/${chatId}/sessions`,
  listSessions: (chatId: string) => `${restAPIv1}/chats/${chatId}/sessions`,
  getSession: (chatId: string, sessionId: string) =>
    `${restAPIv1}/chats/${chatId}/sessions/${sessionId}`,
  updateSession: (chatId: string, sessionId: string) =>
    `${restAPIv1}/chats/${chatId}/sessions/${sessionId}`,
  removeSessions: (chatId: string) => `${restAPIv1}/chats/${chatId}/sessions`,
  deleteMessage: (chatId: string, sessionId: string, msgId: string) =>
    `${restAPIv1}/chats/${chatId}/sessions/${sessionId}/messages/${msgId}`,
  thumbup: (chatId: string, sessionId: string, msgId: string) =>
    `${restAPIv1}/chats/${chatId}/sessions/${sessionId}/messages/${msgId}/feedback`,
  completionUrl: `${restAPIv1}/chat/completions`,
  chatsTts: `${restAPIv1}/chat/audio/speech`,
  chatsRelatedQuestions: `${restAPIv1}/chat/recommendation`,

  // file manager
  listFile: `${restAPIv1}/files`,
  uploadFile: `${restAPIv1}/files`,
  removeFile: `${restAPIv1}/files`,
  getAllParentFolder: `${restAPIv1}/files`,
  createFolder: `${restAPIv1}/files`,
  connectFileToKnowledge: `${restAPIv1}/files/link-to-datasets`,
  getFile: `${restAPIv1}/files`,
  moveFile: `${restAPIv1}/files/move`,

  // system
  getSystemVersion: `${restAPIv1}/system/version`,
  getSystemTokenList: `${restAPIv1}/system/tokens`,
  createSystemToken: `${restAPIv1}/system/tokens`,
  removeSystemToken: `${restAPIv1}/system/tokens`,
  getSystemConfig: `${restAPIv1}/system/config`,
  setLangfuseConfig: `${restAPIv1}/langfuse/api-key`,

  // ordinary message attachments
  getAttachmentFileDownload: (docId: string) =>
    `${restAPIv1}/agents/attachments/${docId}/download`,
  getAttachmentFilePreview: ({
    docId,
    ext,
    mimeType,
    filename,
  }: {
    docId: string;
    ext?: string;
    mimeType?: string;
    filename?: string;
  }) => {
    const params = new URLSearchParams();
    if (ext) params.set('ext', ext);
    if (mimeType) params.set('mime_type', mimeType);
    if (filename) params.set('filename', filename);
    const query = params.toString();
    return `${restAPIv1}/agents/attachments/${docId}/preview${query ? `?${query}` : ''}`;
  },
};
