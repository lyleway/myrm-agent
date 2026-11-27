'use client';

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApprovalDrawer } from '../ApprovalDrawer';
import type { ApprovalPayload } from '@/store/useApprovalStore';

/**
 * The drawer's own resolve path is the one this file exists to pin: a note typed into the
 * approval card belongs on `guidance` when the person approves, because the harness reads
 * `feedback` on a decision only as a rejection reason. Sending it as `feedback` reaches no one.
 */

const resumeDrawerApprovalStream = vi.fn(async () => undefined);

vi.mock('next-intl', () => ({
  useTranslations: (namespace: string) => (key: string) => `${namespace}.${key}`,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
}));

vi.mock('@/lib/approval/resumeDrawerApprovalStream', () => ({
  resumeDrawerApprovalStream: (...args: unknown[]) => resumeDrawerApprovalStream(...(args as [])),
}));

vi.mock('@/lib/utils/toast', () => ({ toast: { error: vi.fn(), warning: vi.fn(), success: vi.fn() } }));

const queue: ApprovalPayload[] = [];

const drawerState = {
  isOpen: true,
  queue,
  closeApproval: vi.fn(),
  closeApprovals: vi.fn(),
  hideDrawer: vi.fn(),
};

vi.mock('@/store/useApprovalStore', () => ({
  default: Object.assign(() => drawerState, { getState: () => drawerState }),
}));

const DOM_APPROVAL: ApprovalPayload = {
  approval_id: 'dom-drawer-1',
  user_id: 'user-1',
  action_type: 'high_risk_dom_action',
  status: 'PENDING',
  severity: 'critical',
  chat_id: 'chat-1',
  payload: {
    tool_input: { action: 'evaluate', expression: 'document.forms[0].submit()' },
    reason: 'mutating JS',
  },
};

const PERMISSION_APPROVAL: ApprovalPayload = {
  approval_id: 'perm-drawer-1',
  user_id: 'user-1',
  action_type: 'external_agent_permission',
  status: 'PENDING',
  severity: 'high',
  chat_id: 'chat-1',
  reason: 'claude-code: Bash',
  payload: { agent: 'claude-code', kind: 'execute', command: 'rm -rf build' },
};

function seed(approval: ApprovalPayload): void {
  queue.length = 0;
  queue.push(approval);
}

describe('ApprovalDrawer resolve payload', () => {
  beforeEach(() => {
    resumeDrawerApprovalStream.mockClear();
    global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({}) })) as unknown as typeof fetch;
  });

  it('sends the note typed on an approval as guidance, not feedback', async () => {
    seed(DOM_APPROVAL);
    render(<ApprovalDrawer />);

    const note = await screen.findByPlaceholderText('toolApproval.addCommentPlaceholder');
    await userEvent.type(note, 'only staging');
    await userEvent.click(screen.getByRole('button', { name: 'toolApproval.approve' }));

    await waitFor(() => expect(resumeDrawerApprovalStream).toHaveBeenCalledTimes(1));

    const [, action, extra] = resumeDrawerApprovalStream.mock.calls[0] as unknown as [
      ApprovalPayload,
      string,
      Record<string, unknown>,
    ];
    expect(action).toBe('approve');
    expect(extra.guidance).toBe('only staging');
    expect(extra.feedback).toBeUndefined();
  });

  describe('an external agent permission request', () => {
    const resolveBody = () => {
      const [url, init] = vi.mocked(global.fetch).mock.calls[0] as unknown as [string, { body: string }];
      return { url, body: JSON.parse(init.body) as Record<string, unknown> };
    };

    it('is titled for what it is, not by its internal type', async () => {
      seed(PERMISSION_APPROVAL);
      render(<ApprovalDrawer />);

      expect(await screen.findByText('toolApproval.externalAgentPermission.drawerTitle')).toBeInTheDocument();
      expect(screen.queryByText(/external_agent_permission/)).not.toBeInTheDocument();
    });

    it('hands the reason for a refusal to the server and resumes no stream', async () => {
      seed(PERMISSION_APPROVAL);
      render(<ApprovalDrawer />);

      await userEvent.type(await screen.findByPlaceholderText('toolApproval.addCommentPlaceholder'), 'not in prod');
      await userEvent.click(screen.getByRole('button', { name: 'toolApproval.reject' }));

      await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
      const { url, body } = resolveBody();
      expect(url).toMatch(/\/approvals\/perm-drawer-1\/resolve$/);
      expect(body).toMatchObject({ decision: 'reject', comment: 'not in prod' });
      expect(resumeDrawerApprovalStream).not.toHaveBeenCalled();
    });

    it('approves with one call and resumes no stream', async () => {
      seed(PERMISSION_APPROVAL);
      render(<ApprovalDrawer />);

      await userEvent.click(await screen.findByRole('button', { name: 'toolApproval.approve' }));

      await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(1));
      expect(resolveBody().body).toMatchObject({ decision: 'approve' });
      expect(resumeDrawerApprovalStream).not.toHaveBeenCalled();
    });
  });

  it('keeps a rejection reason on feedback, where the harness reads it', async () => {
    seed(DOM_APPROVAL);
    render(<ApprovalDrawer />);

    const note = await screen.findByPlaceholderText('toolApproval.addCommentPlaceholder');
    await userEvent.type(note, 'too risky');
    await userEvent.click(screen.getByRole('button', { name: 'toolApproval.reject' }));

    await waitFor(() => expect(resumeDrawerApprovalStream).toHaveBeenCalledTimes(1));

    const [, action, extra] = resumeDrawerApprovalStream.mock.calls[0] as unknown as [
      ApprovalPayload,
      string,
      Record<string, unknown>,
    ];
    expect(action).toBe('reject');
    expect(extra.feedback).toBe('too risky');
    expect(extra.guidance).toBeUndefined();
  });
});
