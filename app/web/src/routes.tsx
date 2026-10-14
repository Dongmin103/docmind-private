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

import { lazy, memo, Suspense } from 'react';
import {
  createBrowserRouter,
  Navigate,
  redirect,
  type RouteObject,
} from 'react-router';
import FallbackComponent from './components/fallback-component';
import DocMindWorkspaceError from './pages/docmind/workspace-error';
import authorizationUtil from './utils/authorization-util';
import { initializeDocMindSharedWorkspace } from './utils/docmind-shared-workspace';

export enum Routes {
  Root = '/',
  Logout = '/logout',
  DocMind = '/docmind',
  ProfileSetting = '/profile-setting',
  Profile = '/profile',
  Api = '/api',
  Team = '/team',
  Plan = '/plan',
  Model = '/model',
  Prompt = '/prompt',
  ProfileTeam = `${ProfileSetting}${Team}`,
  ProfilePlan = `${ProfileSetting}${Plan}`,
  ProfileModel = `${ProfileSetting}${Model}`,
  ProfilePrompt = `${ProfileSetting}${Prompt}`,
  ProfileProfile = `${ProfileSetting}${Profile}`,
  UserSetting = '/user-setting',
}

const defaultRouteFallback = (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[1px]">
    <div className="h-8 w-8 animate-spin rounded-full border-2 border-white/70 border-t-transparent" />
  </div>
);

type LazyRouteConfig = Omit<RouteObject, 'Component' | 'children'> & {
  Component?: () => Promise<{ default: React.ComponentType<any> }>;
  children?: LazyRouteConfig[];
};

const withLazyRoute = (
  importer: () => Promise<{ default: React.ComponentType<any> }>,
  fallback: React.ReactNode = defaultRouteFallback,
) => {
  const LazyComponent = lazy(importer);
  const Wrapped: React.FC<any> = (props) => (
    <Suspense fallback={fallback}>
      <LazyComponent {...props} />
    </Suspense>
  );
  Wrapped.displayName = `LazyRoute(${
    (LazyComponent as unknown as React.ComponentType<any>).displayName ||
    LazyComponent.name ||
    'Component'
  })`;
  return process.env.NODE_ENV === 'development' ? LazyComponent : memo(Wrapped);
};

const redirectLegacyEntry = ({ request }: { request: Request }) => {
  const url = new URL(request.url);
  const auth = url.searchParams.get('auth');
  if (auth) {
    authorizationUtil.setAuthorization(auth);
    url.searchParams.delete('auth');
  }
  return redirect(`${Routes.DocMind}${url.search}`);
};

const routeConfigOptions: LazyRouteConfig[] = [
  { path: Routes.Root, loader: redirectLegacyEntry },
  ...['/home', '/login', '/login-next'].map((path) => ({
    path,
    loader: redirectLegacyEntry,
  })),
  { path: '/document/:id', Component: () => import('@/pages/document-viewer') },
  {
    path: Routes.DocMind,
    Component: () => import('@/pages/docmind'),
    loader: ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      const auth = url.searchParams.get('auth');
      if (auth) {
        authorizationUtil.setAuthorization(auth);
        url.searchParams.delete('auth');
        return redirect(`${Routes.DocMind}${url.search}`);
      }
      return initializeDocMindSharedWorkspace();
    },
    errorElement: <DocMindWorkspaceError />,
  },
  {
    path: '/chunk/parsed/chunks',
    Component: () => import('@/components/chunk-inspector'),
  },
  {
    path: Routes.Root,
    Component: () => import('@/layouts/root-layout'),
    children: [
      {
        path: Routes.UserSetting,
        Component: () => import('@/pages/user-setting'),
        children: [
          {
            path: Routes.UserSetting,
            element: <Navigate to="/user-setting/model" replace />,
          },
          {
            path: '/user-setting/profile',
            Component: () => import('@/pages/user-setting/profile'),
          },
          {
            path: '/user-setting/model',
            Component: () => import('@/pages/user-setting/setting-model'),
          },
          {
            path: '/user-setting/team',
            Component: () => import('@/pages/user-setting/setting-team'),
          },
          {
            path: '/user-setting/api',
            Component: () => import('@/pages/user-setting/setting-api'),
          },
        ],
      },
    ],
  },
  { path: '/*', Component: () => import('@/pages/404') },
];

const wrapRoutes = (routes: LazyRouteConfig[]): RouteObject[] =>
  routes.map((item) => {
    const { Component, children, ...rest } = item;
    const next: RouteObject = { errorElement: <FallbackComponent />, ...rest };
    if (Component) {
      next.Component = withLazyRoute(Component);
    }
    if (children) {
      next.children = wrapRoutes(children);
    }
    return next;
  });

const routeConfig = wrapRoutes(routeConfigOptions);

const routers = createBrowserRouter(routeConfig, {
  basename: import.meta.env.VITE_BASE_URL || '/',
});

export { routers };
