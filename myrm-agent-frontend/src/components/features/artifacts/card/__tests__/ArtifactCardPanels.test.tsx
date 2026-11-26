/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  chat: {
    chatId: 'chat-1' as string | null,
    workspaceDir: '/ws' as string | null,
    agentConfig: { agentName: 'Agent', presetName: 'Preset' } as { agentName: string; presetName: string } | undefined,
  },
}));

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/store/useChatStore', () => ({
  default: (selector: (state: typeof mocks.chat) => unknown) => selector(mocks.chat),
}));
vi.mock('../../WeChatDraftPanel', () => ({
  WeChatDraftPanel: (props: Record<string, unknown>) => (
    <div
      data-testid="wechat-panel"
      data-artifact={String(props.artifactId)}
      data-path={String(props.htmlPath)}
      data-chat={String(props.chatId)}
      data-agent={String(props.agentName)}
      data-preset={String(props.presetName)}
    >
      <button onClick={props.onComplete as () => void}>finish-draft</button>
    </div>
  ),
}));
vi.mock('../../OrganizePlanPanel', () => ({
  OrganizePlanPanel: (props: { workspace: string; planContent: string; onPlanChange: (text: string) => void }) => (
    <div data-testid="organize-panel" data-workspace={props.workspace}>
      {props.planContent}
      <button onClick={() => props.onPlanChange('edited plan')}>edit-plan</button>
    </div>
  ),
}));

import { ArtifactCardPanels } from '../ArtifactCardPanels';

const artifact = (overrides: Record<string, unknown> = {}) =>
  ({ id: 'art-1', filename: 'page.html', file_path: '/ws/page.html', ...overrides }) as never;

function renderPanels(props: Partial<Parameters<typeof ArtifactCardPanels>[0]> = {}) {
  const onCardClick = vi.fn();
  const onWeChatDone = vi.fn();
  const fetchContent = props.fetchContent ?? vi.fn(async () => 'the plan');
  const view = render(
    <div onClick={onCardClick} role="presentation">
      <ArtifactCardPanels
        current={artifact()}
        fetchContent={fetchContent}
        wechatOpen={false}
        organizeOpen={false}
        onWeChatDone={onWeChatDone}
        {...props}
      />
    </div>,
  );
  return { ...view, onCardClick, onWeChatDone, fetchContent };
}

describe('ArtifactCardPanels', () => {
  beforeEach(() => {
    mocks.chat.chatId = 'chat-1';
    mocks.chat.workspaceDir = '/ws';
    mocks.chat.agentConfig = { agentName: 'Agent', presetName: 'Preset' };
  });

  it('shows no panel while both are closed, and loads nothing', () => {
    const { fetchContent } = renderPanels();

    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();
    expect(screen.queryByTestId('organize-panel')).not.toBeInTheDocument();
    expect(fetchContent).not.toHaveBeenCalled();
  });

  it('opens the WeChat draft for the file on disk, with the chat and agent it belongs to', () => {
    const { onWeChatDone } = renderPanels({ wechatOpen: true });

    const panel = screen.getByTestId('wechat-panel');
    expect(panel).toHaveAttribute('data-artifact', 'art-1');
    expect(panel).toHaveAttribute('data-path', '/ws/page.html');
    expect(panel).toHaveAttribute('data-chat', 'chat-1');
    expect(panel).toHaveAttribute('data-agent', 'Agent');
    expect(panel).toHaveAttribute('data-preset', 'Preset');
    fireEvent.click(screen.getByText('finish-draft'));
    expect(onWeChatDone).toHaveBeenCalledTimes(1);
  });

  it('passes no chat to the draft when none is open', () => {
    mocks.chat.chatId = null;
    mocks.chat.agentConfig = undefined;

    renderPanels({ wechatOpen: true });

    expect(screen.getByTestId('wechat-panel')).toHaveAttribute('data-chat', 'null');
    expect(screen.getByTestId('wechat-panel')).toHaveAttribute('data-agent', 'undefined');
  });

  it('has no WeChat draft for an artifact that is not a file on disk', () => {
    renderPanels({ wechatOpen: true, current: artifact({ file_path: undefined }) });

    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();
  });

  it('loads the plan when the organize panel opens, with the workspace it applies to', async () => {
    renderPanels({ organizeOpen: true });

    const panel = await screen.findByTestId('organize-panel');
    expect(panel).toHaveTextContent('the plan');
    expect(panel).toHaveAttribute('data-workspace', '/ws');
  });

  it('shows a spinner while the plan is loading', () => {
    const { container } = renderPanels({ organizeOpen: true, fetchContent: () => new Promise(() => undefined) });

    expect(container.querySelector('.animate-spin')).toBeInTheDocument();
    expect(screen.queryByTestId('organize-panel')).not.toBeInTheDocument();
  });

  it('says the plan is invalid when it cannot be loaded', async () => {
    renderPanels({ organizeOpen: true, fetchContent: async () => null });

    expect(await screen.findByText('organizePlan.invalidPlan')).toBeInTheDocument();
  });

  it('applies the plan edits it is given and falls back to an empty workspace', async () => {
    mocks.chat.workspaceDir = null;
    renderPanels({ organizeOpen: true });

    const panel = await screen.findByTestId('organize-panel');
    expect(panel).toHaveAttribute('data-workspace', '');
    fireEvent.click(screen.getByText('edit-plan'));

    await waitFor(() => expect(screen.getByTestId('organize-panel')).toHaveTextContent('edited plan'));
  });

  it('keeps clicks on the organize panel from selecting the card', async () => {
    const { onCardClick } = renderPanels({ organizeOpen: true });
    await screen.findByTestId('organize-panel');

    fireEvent.click(screen.getByTestId('organize-panel'));

    expect(onCardClick).not.toHaveBeenCalled();
  });
});
