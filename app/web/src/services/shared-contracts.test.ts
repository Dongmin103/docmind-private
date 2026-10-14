jest.mock('@/utils/register-server', () => ({
  __esModule: true, default: (methods: unknown) => methods,
  registerNextServer: (methods: unknown) => methods,
}));
jest.mock('@/utils/request', () => ({ __esModule: true, default: { get: jest.fn() } }));
import chatService from './next-chat-service';
import fileService, { downloadDatasetDocument } from './file-manager-service';
import request from '@/utils/request';

it('retains message delete and feedback on the session message endpoints', () => {
  const methods = chatService as any;
  expect(methods.deleteMessage.method).toBe('delete');
  expect(methods.deleteMessage.url('chat', 'session', 'message')).toBe('/api/v1/chats/chat/sessions/session/messages/message');
  expect(methods.thumbup.method).toBe('put');
  expect(methods.thumbup.url('chat', 'session', 'message')).toBe('/api/v1/chats/chat/sessions/session/messages/message/feedback');
});
it('retains document preview and dataset document download contracts', () => {
  expect((fileService as any).getDocumentFile.url).toBe('/api/v1/documents');
  downloadDatasetDocument({ datasetId: 'dataset', docId: 'document', ext: 'pdf' });
  expect(request.get).toHaveBeenCalledWith('/api/v1/datasets/dataset/documents/document', { params: { ext: 'pdf' }, responseType: 'blob' });
});
