import { renderHook } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { useGetSharedChatSearchParams } from './use-shared-chat-search-params';

const readParams = (query: string) =>
  renderHook(useGetSharedChatSearchParams, {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={['/agent/share' + query]}>
        {children}
      </MemoryRouter>
    ),
  }).result.current;

it('preserves shared agent query values and strips only the data prefix', () => {
  expect(
    readParams(
      '?from=agent&shared_id=id&locale=ko&theme=dark&data_name=hello&data_data_x=two&ignored=no',
    ),
  ).toEqual({
    from: 'agent',
    sharedId: 'id',
    locale: 'ko',
    theme: 'dark',
    data: { name: 'hello', data_x: 'two' },
    visibleAvatar: true,
  });
});
it.each([
  ['', true],
  ['?visible_avatar=1', false],
  ['?visible_avatar=0', true],
  ['?visible_avatar=', true],
])('preserves the existing avatar convention %s', (query, expected) => {
  expect(readParams(query).visibleAvatar).toBe(expected);
});
