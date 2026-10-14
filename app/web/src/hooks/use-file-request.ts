import fileManagerService from '@/services/file-manager-service';
import { useMutation } from '@tanstack/react-query';

const FileApiAction = { FetchPureFileList: 'fetchPureFileList' } as const;

export const useFetchPureFileList = () => {
  const { mutateAsync, isPending: loading } = useMutation({
    mutationKey: [FileApiAction.FetchPureFileList],
    gcTime: 0,

    mutationFn: async (parentId: string) => {
      const { data } = await fileManagerService.listFile({
        parent_id: parentId,
        page_size: 100,
        page: 1,
      });

      return data;
    },
  });

  return { loading, fetchList: mutateAsync };
};
