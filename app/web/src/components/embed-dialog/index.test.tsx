jest.mock('@/components/switch-fom-field', () => ({ SwitchFormField: () => null }));
import { render, screen } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';
import EmbedDialog from './index';
import { SharedFrom } from '@/constants/chat';
jest.mock('react-router', () => ({ ...jest.requireActual('react-router'), createBrowserRouter: (routes: unknown) => ({ routes }) }));
jest.mock('@/components/theme-provider', () => ({ useIsDarkTheme: () => false }));
jest.mock('@/components/copy-to-clipboard', () => () => null);
jest.mock('@/components/originui/select-with-search', () => ({ SelectWithSearch: jest.requireActual('react').forwardRef(() => null) }));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
jest.mock('react-syntax-highlighter', () => ({ Prism: ({ children }: any) => <pre data-testid="embed-code">{children}</pre> }));
jest.mock('react-syntax-highlighter/dist/esm/styles/prism', () => ({ oneDark: {}, oneLight: {} }));
beforeAll(() => { global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} } as any; });
it('keeps agent fullscreen embedding and offers no retired chatbot widget option', () => {
  render(<TooltipProvider><EmbedDialog visible hideModal={jest.fn()} token="agent-id" beta="token" from={SharedFrom.Agent} isAgent /></TooltipProvider>);
  expect(screen.getByTestId('embed-code').textContent).toContain('/agent/share?shared_id=agent-id');
  expect(screen.getByTestId('embed-code').textContent).not.toContain('/chats/');
  expect(screen.queryByText('chat.floatingWidget')).not.toBeInTheDocument();
});
