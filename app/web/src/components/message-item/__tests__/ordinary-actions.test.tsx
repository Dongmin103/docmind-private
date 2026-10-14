import { fireEvent, render, screen } from '@testing-library/react';
import { AssistantGroupButton } from '../group-button';

const mockFeedback = jest.fn();
jest.mock('../hooks', () => ({
  useSendFeedback: () => ({ visible: false, hideModal: jest.fn(), showModal: jest.fn(), onFeedbackOk: mockFeedback, loading: false }),
  useRemoveMessage: () => ({}),
  useSpeech: () => { throw new Error('retired speech hook called'); },
}));
jest.mock('@/hooks/common-hooks', () => ({ useSetModalState: () => ({ visible: false, hideModal: jest.fn(), showModal: jest.fn() }) }));
jest.mock('react-i18next', () => ({ useTranslation: () => ({ t: (value: string) => value }) }));
jest.mock('@/components/copy-to-clipboard', () => ({ __esModule: true, default: ({ text }: { text: string }) => <button>{text}</button> }));
jest.mock('@/components/ui/tooltip', () => ({ Tooltip: ({ children }: any) => children, TooltipContent: ({ children }: any) => children, TooltipTrigger: ({ children }: any) => children }));
jest.mock('../../feedback-dialog', () => ({ __esModule: true, default: () => null }));
jest.mock('../../prompt-dialog', () => ({ PromptDialog: () => null }));

it('keeps text copy and mockFeedback actions without audio controls', () => {
  const { container } = render(<AssistantGroupButton messageId="synthetic" content="Visible answer" showLikeButton />);
  expect(screen.getByRole('button', { name: 'Visible answer' })).toBeInTheDocument();
  const buttons = screen.getAllByRole('button');
  expect(buttons).toHaveLength(3);
  fireEvent.click(buttons[1]);
  expect(mockFeedback).toHaveBeenCalledWith({ thumbup: true });
  expect(container.querySelector('audio')).toBeNull();
});
