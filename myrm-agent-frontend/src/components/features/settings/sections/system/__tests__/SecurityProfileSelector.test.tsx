/** @vitest-environment jsdom */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchWithTimeout } from '@/lib/api';
import { toast } from '@/lib/utils/toast';
import SecurityProfileSelector from '../SecurityProfileSelector';

const stableT = (key: string, values?: { name?: string }) => (values?.name ? `${key}:${values.name}` : key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/lib/utils/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/api', () => ({ fetchWithTimeout: vi.fn() }));

// The wire shape of GET /security/profiles and POST /security/profiles/{key}/activate (camelCase aliases).
const profile = (profileKey: string, displayName: string, isActive: boolean) => ({
  id: `id-${profileKey}`,
  profileKey,
  displayName,
  description: `${displayName} profile`,
  configJson: { permissions: { '*': 'ask' } },
  isBuiltin: true,
  isActive,
  createdAt: '2026-11-26T14:18:02',
  updatedAt: '2026-11-26T14:18:02',
});

const listing = {
  profiles: [profile('full_access', 'Full Access', false), profile('workspace', 'Workspace', true)],
  activeKey: 'workspace',
};

const json = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body }) as Response;

describe('SecurityProfileSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchWithTimeout).mockImplementation(async (_url, init) => {
      if (init?.method === 'POST') {
        return json(profile('full_access', 'Full Access', true));
      }
      return json(listing);
    });
  });

  it('names the profile the server reports as active on the button', async () => {
    render(<SecurityProfileSelector />);

    const trigger = await screen.findByTestId('security-profile-trigger');

    expect(trigger).toHaveTextContent('Workspace');
  });

  it('activates the picked profile, renames the button and hands the profile to the parent', async () => {
    const onProfileSelect = vi.fn();
    const user = userEvent.setup();
    render(<SecurityProfileSelector onProfileSelect={onProfileSelect} />);

    await user.click(await screen.findByTestId('security-profile-trigger'));
    await user.click(await screen.findByTestId('security-profile-option-full_access'));

    await waitFor(() =>
      expect(fetchWithTimeout).toHaveBeenCalledWith(
        '/security/profiles/full_access/activate',
        { method: 'POST' },
        10_000,
      ),
    );
    await waitFor(() => expect(screen.getByTestId('security-profile-trigger')).toHaveTextContent('Full Access'));
    expect(toast.success).toHaveBeenCalledWith('profile.activated:Full Access');
    expect(onProfileSelect).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ configJson: { permissions: { '*': 'ask' } } }),
    );
  });

  it('keeps the previous profile and reports the failure when the server refuses the activation', async () => {
    vi.mocked(fetchWithTimeout).mockImplementation(async (_url, init) =>
      init?.method === 'POST' ? json({ detail: 'Profile not found' }, false, 404) : json(listing),
    );
    const onProfileSelect = vi.fn();
    const user = userEvent.setup();
    render(<SecurityProfileSelector onProfileSelect={onProfileSelect} />);

    await user.click(await screen.findByTestId('security-profile-trigger'));
    await user.click(await screen.findByTestId('security-profile-option-full_access'));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('profile.activateFailed'));
    expect(screen.getByTestId('security-profile-trigger')).toHaveTextContent('Workspace');
    expect(onProfileSelect).not.toHaveBeenCalled();
  });
});
