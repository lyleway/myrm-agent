/** @vitest-environment jsdom */

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const stableT = vi.hoisted(
  () => (key: string, values?: Record<string, string>) => (values ? `${key}|${Object.values(values).join('|')}` : key),
);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/store/useChatStore', () => ({ default: { getState: () => ({}) } }));

import { ArtifactPublicationBanners } from '../ArtifactPublicationBanners';

const publication = (overrides: Record<string, unknown> = {}) => ({
  id: 'p1',
  hosting_target_id: 't1',
  hosting_target_name: 'Vercel',
  publication_url: 'https://live.example.com/a',
  publication_status: 'READY',
  publication_project_ref: null,
  publication_version_id: 'v1',
  updated_at: null,
  ...overrides,
});

function renderInCard(publications: unknown[], latestVersionId: string | null | undefined = 'v1') {
  const onCardClick = vi.fn();
  const onRedeploy = vi.fn();
  const view = render(
    <div onClick={onCardClick} role="presentation">
      <ArtifactPublicationBanners
        publications={publications as never}
        latestVersionId={latestVersionId}
        onRedeploy={onRedeploy}
      />
    </div>,
  );
  return { ...view, onCardClick, onRedeploy };
}

describe('ArtifactPublicationBanners', () => {
  beforeEach(() => {
    vi.spyOn(window, 'open').mockImplementation(() => null);
  });

  afterEach(() => vi.restoreAllMocks());

  it('renders nothing for an artifact that was never published', () => {
    const { container } = renderInCard([]);

    expect(container.firstElementChild).toBeEmptyDOMElement();
  });

  it('shows a live link named after the target and the host, and opens it without selecting the card', () => {
    const { onCardClick } = renderInCard([publication()]);

    const chip = screen.getByRole('button', { name: /Vercel · live\.example\.com/ });
    expect(chip).toHaveAttribute('title', 'publish.openLiveWithTarget|Vercel|live.example.com');
    fireEvent.click(chip);

    expect(window.open).toHaveBeenCalledWith('https://live.example.com/a', '_blank');
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('names the target by id when it has no name, and shows only the host', () => {
    renderInCard([publication({ hosting_target_name: null })]);

    const chip = screen.getByRole('button', { name: 'live.example.com' });
    expect(chip).toHaveAttribute('title', 'publish.openLiveWithTarget|t1|live.example.com');
  });

  it.each([
    ['is still building', { publication_status: 'BUILDING' }],
    ['has no address', { publication_url: null }],
  ])('offers no live link for a copy that %s', (_name, overrides) => {
    renderInCard([publication(overrides)]);

    expect(screen.queryByRole('button', { name: /live\.example\.com/ })).not.toBeInTheDocument();
  });

  it('asks to redeploy each live copy that is older than the latest version, without selecting the card', () => {
    const { onRedeploy, onCardClick } = renderInCard(
      [
        publication({ id: 'p1', hosting_target_id: 't1' }),
        publication({ id: 'p2', hosting_target_id: 't2', hosting_target_name: null }),
      ],
      'v2',
    );

    expect(screen.getByText('deploy.redeployBannerForTarget|Vercel')).toBeInTheDocument();
    expect(screen.getByText('deploy.redeployBannerForTarget|t2')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'deploy.redeployAction' })[1]);

    expect(onRedeploy).toHaveBeenCalledWith('t2');
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('keeps clicks on the banners from selecting the card', () => {
    const { onCardClick, container } = renderInCard([publication()], 'v2');

    fireEvent.click(container.querySelectorAll('[role="presentation"]')[1]);
    fireEvent.click(container.querySelectorAll('[role="presentation"]')[2]);

    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('asks for no redeploy while the live copy is the latest, or the latest version is unknown', () => {
    const { rerender } = renderInCard([publication()], 'v1');
    expect(screen.queryByText(/deploy\.redeployBannerForTarget/)).not.toBeInTheDocument();

    rerender(
      <ArtifactPublicationBanners
        publications={[publication()] as never}
        latestVersionId={undefined}
        onRedeploy={vi.fn()}
      />,
    );
    expect(screen.queryByText(/deploy\.redeployBannerForTarget/)).not.toBeInTheDocument();
  });
});
