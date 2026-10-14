import { lazy } from 'react';

// Load the existing large viewers only when their workspace view is opened.
export const ChunkInspection = lazy(
  () =>
    import('@/components/chunk-inspector'),
);
export const WorkspaceSettings = lazy(() => import('./workspace-settings'));
