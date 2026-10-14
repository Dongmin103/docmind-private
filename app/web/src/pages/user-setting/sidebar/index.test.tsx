import { fireEvent, render, screen } from '@testing-library/react';
import { SideBar } from './index';

const mockNavigate = jest.fn();
jest.mock('./hooks', () => ({
  useHandleMenuClick: () => ({
    active: '/model',
    handleMenuClick: (path: string) => () => mockNavigate(path),
  }),
}));
jest.mock('@/routes', () => ({
  Routes: {
    Model: '/model',
    Mcp: '/mcp',
    Team: '/team',
    Profile: '/profile',
    Api: '/api',
  },
}));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/hooks/use-login-request', () => ({
  useLogout: () => ({ logout: jest.fn() }),
}));
jest.mock('@/hooks/use-user-setting-request', () => ({
  useFetchUserInfo: () => ({
    data: { nickname: 'Test', email: 'test@example.test' },
  }),
  useFetchSystemVersion: () => ({
    version: 'test',
    fetchSystemVersion: jest.fn(),
  }),
}));
jest.mock('@/components/icon-font', () => ({ IconFontFill: () => null }));
jest.mock('@/components/ragflow-avatar', () => ({ RAGFlowAvatar: () => null }));
jest.mock('@/components/theme-switch', () => ({
  __esModule: true,
  default: () => null,
}));

it('offers model and profile navigation without external integrations', () => {
  render(<SideBar />);
  expect(
    screen.queryByRole('button', { name: 'setting.dataSources' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'setting.model' }));
  expect(mockNavigate).toHaveBeenLastCalledWith('/model');
  expect(
    screen.queryByRole('button', { name: 'setting.chatChannels' }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'setting.profile' }));
  expect(mockNavigate).toHaveBeenLastCalledWith('/profile');
});
