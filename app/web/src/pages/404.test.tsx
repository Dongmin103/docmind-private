import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import NoFoundPage from './404';

jest.mock('@/routes', () => ({
  Routes: { DocMind: '/docmind' },
}));

it.each(['/admin/unknown', '/user-setting/chat-channel', '/unknown'])(
  'returns from missing route %s to DocMind',
  async (path) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/docmind" element={<p>DocMind workspace</p>} />
          <Route path="*" element={<NoFoundPage />} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(await screen.findByText('DocMind workspace')).toBeInTheDocument();
  },
);
