import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from '@/lib/api';
import { getJobDeliveryHealth } from '@/services/cron';

describe('getJobDeliveryHealth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('asks silently about the job: the badge stays away when the verdict cannot be had, so no toast', async () => {
    vi.mocked(apiRequest).mockResolvedValue({});

    await getJobDeliveryHealth('job-1');

    expect(apiRequest).toHaveBeenCalledExactlyOnceWith('/cron/job-1/delivery-health', { silent: true });
  });

  it('hands back the verdict the server gave', async () => {
    const verdict = {
      status: 'degraded',
      failure_streak: 2,
      last_attempt_at: '2026-06-17T08:30:00Z',
      last_error_category: 'timeout',
    };
    vi.mocked(apiRequest).mockResolvedValue(verdict);

    await expect(getJobDeliveryHealth('job-1')).resolves.toEqual(verdict);
  });

  // The server answers 204 when there is nothing to judge, which `apiRequest` returns as an empty object.
  it('reads the empty answer as no verdict', async () => {
    vi.mocked(apiRequest).mockResolvedValue({});

    await expect(getJobDeliveryHealth('job-1')).resolves.toBeNull();
  });

  // `toString` is a property every object has, which a plain `in` check would take for a status.
  it.each(['offline', 'toString'])(
    'reads the status %s, which this client does not know, as no verdict',
    async (status) => {
      vi.mocked(apiRequest).mockResolvedValue({ status, failure_streak: 3, last_attempt_at: '2026-06-17T08:30:00Z' });

      await expect(getJobDeliveryHealth('job-1')).resolves.toBeNull();
    },
  );

  // The badge dates every verdict by this time, so a verdict that cannot be dated is not shown.
  it.each([
    ['is missing', undefined],
    ['cannot be read', 'yesterday'],
  ])('reads a verdict whose time of the newest attempt %s as no verdict', async (_case, last_attempt_at) => {
    vi.mocked(apiRequest).mockResolvedValue({ status: 'healthy', failure_streak: 0, last_attempt_at });

    await expect(getJobDeliveryHealth('job-1')).resolves.toBeNull();
  });
});
