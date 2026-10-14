jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  createBrowserRouter: (routes: unknown) => ({ routes }),
  redirect: (target: string) => ({
    status: 302,
    headers: { get: (name: string) => (name === 'Location' ? target : null) },
  }),
}));

import authorizationUtil from './utils/authorization-util';
import { routers } from './routes';
import { matchRoutes } from 'react-router';

const load = async (path: string, search = '') => {
  const route = routers.routes.find((item) => item.path === path);
  if (typeof route?.loader !== 'function')
    throw new Error(`Missing loader for ${path}`);
  return route.loader({
    request: { url: `http://localhost${path}${search}` } as Request,
    params: {},
    context: {},
  } as Parameters<typeof route.loader>[0]) as Promise<Response>;
};

it.each(['/', '/home', '/login', '/login-next'])(
  'routes %s to the DocMind workspace',
  async (path) => {
    const result = await load(path);
    expect(result.status).toBe(302);
    expect(result.headers.get('Location')).toBe('/docmind');
  },
);

it('stores an OAuth callback token before redirecting to DocMind', async () => {
  const setAuthorization = jest.spyOn(authorizationUtil, 'setAuthorization');
  const result = await load('/login', '?auth=sample-token&view=library');
  expect(setAuthorization).toHaveBeenCalledWith('sample-token');
  expect(result.headers.get('Location')).toBe('/docmind?view=library');
  setAuthorization.mockRestore();
});

it.each([
  '/user-setting/data-source',
  '/user-setting/data-source/data-source-detail-page',
])('does not expose the removed external connector route %s', (path) => {
  expect(matchRoutes(routers.routes, path)?.at(-1)?.route.path).toBe('/*');
});

it.each([
  '/admin',
  '/admin/users',
  '/admin/users/example',
  '/admin/services',
  '/admin/sandbox-settings',
  '/admin/roles',
  '/admin/whitelist',
  '/admin/monitoring',
  '/user-setting/chat-channel',
])('rejects the retired administration or messaging route %s', (path) => {
  expect(matchRoutes(routers.routes, path)?.at(-1)?.route.path).toBe('/*');
});

it.each(['/docmind', '/user-setting/model', '/user-setting/profile'])(
  'preserves the supported route %s',
  (path) => {
    const matches = matchRoutes(routers.routes, path);
    expect(matches?.at(-1)?.route.path).toBe(path);
  },
);

it.each([
  '/chats/share?shared_id=example',
  '/chats/widget?mode=master',
  '/chat/example',
  '/chats',
  '/searches',
  '/search/example',
  '/files?folderId=example',
  '/search/share?shared_id=example',
])('rejects the retired generic application route %s', (path) => {
  expect(matchRoutes(routers.routes, path)?.at(-1)?.route.path).toBe('/*');
});

it.each([
  ['/document/example', '/document/:id'],
  ['/chunk', '/chunk'],
  ['/chunk/chunk/example', '/chunk/chunk/:id'],
  ['/files/skills', '/files/skills'],
  ['/dataset/files/example', '/dataset//files/:id'],
])('preserves shared and deferred route %s', (path, pattern) => {
  expect(matchRoutes(routers.routes, path)?.at(-1)?.route.path).toBe(pattern);
});
