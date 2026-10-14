jest.mock('@/utils/css-support', () => ({ supportsCssAnchor: false }));
jest.mock('react-router', () => ({ ...jest.requireActual('react-router'), createBrowserRouter: (routes: unknown) => ({ routes }) }));
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Link } from 'react-router';
import { DesktopNavbar, MobileNavbar } from './global-navbar';
jest.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
jest.mock('@/components/svg-icon', () => ({ HomeIcon: () => null }));

afterEach(() => jest.clearAllMocks());
it.each([false, true])(
  'removes retired launchers from the %s mobile menu',
  (mobile) => {
    const footer = () => <Link to="/user-setting/model">Settings</Link>;
    render(
      <MemoryRouter>
        {mobile ? (
          <MobileNavbar renderFooter={footer} />
        ) : (
          <>
            <DesktopNavbar />
            {footer()}
          </>
        )}
      </MemoryRouter>,
    );
    if (mobile) fireEvent.click(screen.getByRole('button', { name: 'Menu' }));
    const links = screen.getAllByRole('link');
    expect(links.map((link) => link.getAttribute('href'))).not.toEqual(
      expect.arrayContaining(['/chats', '/searches', '/files']),
    );
    for (const retired of ['/chats', '/searches', '/files'])
      expect(links.some((link) => link.getAttribute('href') === retired)).toBe(
        false,
      );
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'href',
      '/user-setting/model',
    );
    expect(screen.getByRole('link', { name: 'DocMind' })).toHaveAttribute(
      'href',
      '/docmind',
    );
  },
);
