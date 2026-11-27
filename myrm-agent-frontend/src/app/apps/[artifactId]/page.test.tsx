/** @vitest-environment jsdom */

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/components/features/app-host/AppHostView', () => ({
  AppHostView: ({ artifactId }: { artifactId: string }) => <div data-testid="host" data-artifact={artifactId} />,
}));

import AppPage from './page';

describe('app host page', () => {
  it('hosts the app the route names', async () => {
    render(await AppPage({ params: Promise.resolve({ artifactId: 'art-1' }) }));

    expect(screen.getByTestId('host')).toHaveAttribute('data-artifact', 'art-1');
  });
});
