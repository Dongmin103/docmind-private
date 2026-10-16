const render = jest.fn();
let resolveLocale: () => void;
const localeReady = new Promise<void>((resolve) => { resolveLocale = resolve; });
const initLanguage = jest.fn(() => localeReady);

jest['doMock']('react-dom/client', () => ({
  __esModule: true,
  default: { createRoot: jest.fn(() => ({ render })) },
}));
jest['doMock']('../app', () => ({ __esModule: true, default: () => null }));
jest['doMock']('../locales/config', () => ({ initLanguage }));
jest['doMock']('react-dev-inspector', () => ({ Inspector: () => null }));

it('renders after locale initialization without probing backend language', async () => {
  document.body.innerHTML = '<div id="root"></div>';
  const fetch = jest.fn();
  global.fetch = fetch;
  require('../main');
  expect(initLanguage).toHaveBeenCalledTimes(1);
  expect(render).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  resolveLocale();
  await localeReady;
  await Promise.resolve();
  expect(render).toHaveBeenCalledTimes(1);
  expect(fetch).not.toHaveBeenCalled();
});
