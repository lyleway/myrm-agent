/** @vitest-environment jsdom */
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConnectorHealthBadge from '../ConnectorHealthBadge';
import { getJobDeliveryHealth } from '@/services/cron';
import type { CronJob, DeliveryHealth } from '@/services/cron';

vi.mock('@/components/primitives/popover', () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const stableT = (key: string, values?: Record<string, unknown>) =>
  values ? `${key}:${Object.values(values).join(',')}` : key;

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
  useLocale: () => 'en',
}));

vi.mock('@/services/cron', () => ({
  getJobDeliveryHealth: vi.fn(),
}));

type BadgeJob = Pick<CronJob, 'id' | 'delivery' | 'last_run_at'>;

const JOB: BadgeJob = {
  id: 'job-1',
  delivery: { channel: 'webhook', target: 'https://hooks.example.com/a' },
  last_run_at: '2026-06-17T08:30:00Z',
};
const JOB_ELSEWHERE: BadgeJob = {
  ...JOB,
  delivery: { channel: 'webhook', target: 'https://hooks.example.com/b' },
};

// The clock stands still three days after the newest attempt, so the age the pill shows does not depend on the day the
// suite runs.
const NOW = new Date('2026-06-20T08:30:00Z');
const LAST_ATTEMPT_AT = '2026-06-17T08:30:00Z';

const HEALTHY: DeliveryHealth = { status: 'healthy', failure_streak: 0, last_attempt_at: LAST_ATTEMPT_AT };

function failing(overrides: Partial<DeliveryHealth> = {}): DeliveryHealth {
  return {
    status: 'degraded',
    failure_streak: 3,
    last_attempt_at: LAST_ATTEMPT_AT,
    last_error_category: 'timeout',
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('ConnectorHealthBadge', () => {
  const healthMock = vi.mocked(getJobDeliveryHealth);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows nothing until the destination has been judged', () => {
    healthMock.mockReturnValue(new Promise(() => undefined));

    const { container } = render(<ConnectorHealthBadge job={JOB} />);

    expect(container).toBeEmptyDOMElement();
  });

  it.each<[string, BadgeJob]>([
    ['a job that delivers somewhere', JOB],
    ['a job with no delivery set up', { ...JOB, delivery: undefined }],
  ])('asks about the job it belongs to and shows nothing when there is nothing to judge (%s)', async (_case, job) => {
    healthMock.mockResolvedValue(null);

    const { container } = render(<ConnectorHealthBadge job={job} />);
    await act(async () => undefined);

    expect(healthMock).toHaveBeenCalledExactlyOnceWith('job-1');
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a healthy destination as a plain pill with nothing to explain', async () => {
    healthMock.mockResolvedValue(HEALTHY);

    render(<ConnectorHealthBadge job={JOB} />);

    expect(await screen.findByTestId('connector-health-badge')).toHaveTextContent('connectorStatusHealthy');
    expect(screen.getByText(/connectorHealthStatus/)).toHaveClass('sr-only');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('connectorHealthBasis')).not.toBeInTheDocument();
  });

  it.each(['healthy', 'degraded', 'down'] as const)(
    'says how long ago the newest attempt finished on a %s destination',
    async (status) => {
      healthMock.mockResolvedValue(status === 'healthy' ? HEALTHY : failing({ status }));

      render(<ConnectorHealthBadge job={JOB} />);

      expect(await screen.findByTestId('connector-health-age')).toHaveTextContent('3 days ago');
      // The status word stays what names the verdict.
      expect(screen.getByTestId('connector-health-badge')).not.toHaveTextContent('3 days ago');
    },
  );

  it('dates a verdict that is weeks old, so an old "OK" is not taken for a fresh one', async () => {
    healthMock.mockResolvedValue({ ...HEALTHY, last_attempt_at: '2026-05-26T08:30:00Z' });

    render(<ConnectorHealthBadge job={JOB} />);

    expect(await screen.findByTestId('connector-health-age')).toHaveTextContent('25 days ago');
  });

  it.each([
    ['degraded', 'connectorStatusDegraded'],
    ['down', 'connectorStatusDown'],
  ] as const)(
    'explains a %s destination: why, what to do, the streak, when, and that only real deliveries change it',
    async (status, label) => {
      healthMock.mockResolvedValue(failing({ status }));

      render(<ConnectorHealthBadge job={JOB} />);

      // The name is the hidden prefix, the verdict and its age; spacing between them depends on layout, which jsdom
      // lacks.
      const pill = await screen.findByRole('button', {
        name: new RegExp(`^connectorHealthStatus:\\s*${label}\\s*·\\s*3 days ago$`),
      });
      expect(pill).toHaveTextContent(label);
      expect(screen.getByText('connectorCategoryTimeout')).toBeInTheDocument();
      expect(screen.getByText('connectorCategoryTimeoutHint')).toBeInTheDocument();
      expect(screen.getByText('connectorFailureStreak:3')).toBeInTheDocument();
      expect(screen.getByText(/^connectorLastFailedAt:.*2026/)).toBeInTheDocument();
      expect(screen.getByText('connectorHealthBasis')).toBeInTheDocument();
    },
  );

  it('words an address the application refused as such', async () => {
    healthMock.mockResolvedValue(
      failing({ status: 'down', failure_streak: 1, last_error_category: 'address_rejected' }),
    );

    render(<ConnectorHealthBadge job={JOB} />);

    expect(await screen.findByText('connectorCategoryAddress')).toBeInTheDocument();
    expect(screen.getByText('connectorCategoryAddressHint')).toBeInTheDocument();
  });

  it.each([
    ['a category the server is not known to record', 'brand_new_category'],
    ['no category at all', null],
  ])('words %s as unknown', async (_case, category) => {
    healthMock.mockResolvedValue(failing({ last_error_category: category }));

    render(<ConnectorHealthBadge job={JOB} />);

    expect(await screen.findByText('connectorCategoryUnknown')).toBeInTheDocument();
    expect(screen.getByText('connectorCategoryUnknownHint')).toBeInTheDocument();
  });

  it('shows nothing when the verdict cannot be loaded', async () => {
    healthMock.mockRejectedValue(new Error('offline'));

    const { container } = render(<ConnectorHealthBadge job={JOB} />);
    await act(async () => undefined);

    expect(healthMock).toHaveBeenCalledTimes(1);
    expect(container).toBeEmptyDOMElement();
  });

  it('reloads after the job has run again', async () => {
    healthMock.mockResolvedValue(HEALTHY);

    const { rerender } = render(<ConnectorHealthBadge job={JOB} />);
    await screen.findByTestId('connector-health-badge');
    rerender(<ConnectorHealthBadge job={{ ...JOB, last_run_at: '2026-06-17T09:30:00Z' }} />);

    await waitFor(() => expect(healthMock).toHaveBeenCalledTimes(2));
  });

  describe('the destination of the job changes', () => {
    it('keeps showing the current verdict while it reloads for the same destination, then replaces it', async () => {
      const reload = deferred<DeliveryHealth | null>();
      healthMock.mockResolvedValueOnce(failing()).mockReturnValueOnce(reload.promise);

      const { rerender } = render(<ConnectorHealthBadge job={JOB} />);
      await screen.findByRole('button');
      rerender(<ConnectorHealthBadge job={{ ...JOB, last_run_at: '2026-06-17T09:30:00Z' }} />);

      expect(screen.getByRole('button')).toHaveTextContent('connectorStatusDegraded');

      await act(async () => {
        reload.resolve(HEALTHY);
      });

      expect(screen.queryByRole('button')).not.toBeInTheDocument();
      expect(screen.getByTestId('connector-health-badge')).toHaveTextContent('connectorStatusHealthy');
    });

    it('keeps the verdict when a reload for the same destination fails', async () => {
      healthMock.mockResolvedValueOnce(failing()).mockRejectedValueOnce(new Error('offline'));

      const { rerender } = render(<ConnectorHealthBadge job={JOB} />);
      await screen.findByRole('button');
      rerender(<ConnectorHealthBadge job={{ ...JOB, last_run_at: '2026-06-17T09:30:00Z' }} />);
      await waitFor(() => expect(healthMock).toHaveBeenCalledTimes(2));

      expect(screen.getByRole('button')).toHaveTextContent('connectorStatusDegraded');
    });

    it('does not show the verdict of the destination the job used to deliver to', async () => {
      const elsewhere = deferred<DeliveryHealth | null>();
      healthMock.mockResolvedValueOnce(failing({ status: 'down' })).mockReturnValueOnce(elsewhere.promise);

      const { container, rerender } = render(<ConnectorHealthBadge job={JOB} />);
      await screen.findByRole('button');
      rerender(<ConnectorHealthBadge job={JOB_ELSEWHERE} />);

      expect(container).toBeEmptyDOMElement();

      await act(async () => {
        elsewhere.resolve(HEALTHY);
      });

      expect(screen.getByTestId('connector-health-badge')).toHaveTextContent('connectorStatusHealthy');
    });

    it('judges the new destination and drops a late answer for the old one', async () => {
      const earlier = deferred<DeliveryHealth | null>();
      healthMock.mockReturnValueOnce(earlier.promise).mockResolvedValueOnce(failing({ status: 'down' }));

      const { rerender } = render(<ConnectorHealthBadge job={JOB} />);
      rerender(<ConnectorHealthBadge job={JOB_ELSEWHERE} />);
      expect(await screen.findByRole('button')).toHaveTextContent('connectorStatusDown');

      await act(async () => {
        earlier.resolve(HEALTHY);
      });

      expect(screen.getByRole('button')).toHaveTextContent('connectorStatusDown');
      expect(healthMock).toHaveBeenCalledTimes(2);
    });
  });
});
