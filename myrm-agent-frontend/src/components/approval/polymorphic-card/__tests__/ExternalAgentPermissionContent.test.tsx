import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ApprovalPayloadData } from '@/store/useApprovalStore';
import { ExternalAgentPermissionContent } from '../ExternalAgentPermissionContent';
import { makeApproval } from './approvalKit';

vi.mock('next-intl', async () => (await import('./approvalStubs')).nextIntlModule);

afterEach(cleanup);

const NAMESPACE = 'toolApproval.externalAgentPermission';

const renderRequest = (payload: ApprovalPayloadData) =>
  render(<ExternalAgentPermissionContent approval={makeApproval('external_agent_permission', payload)} />);

describe('ExternalAgentPermissionContent', () => {
  it('says who asks and what kind of action it wants', () => {
    renderRequest({ agent: 'claude-code', kind: 'delete' });

    expect(screen.getByText(`${NAMESPACE}.title:{"agent":"claude-code"}`)).toBeInTheDocument();
    expect(screen.getByText(`${NAMESPACE}.kinds.delete`)).toBeInTheDocument();
  });

  it('reads a kind it does not know as another action', () => {
    renderRequest({ agent: 'claude-code', kind: 'teleport' });

    expect(screen.getByText(`${NAMESPACE}.kinds.other`)).toBeInTheDocument();
  });

  it('shows the command the agent wants to run instead of its headline', () => {
    renderRequest({ agent: 'a', kind: 'execute', title: 'Bash', command: 'rm -rf build' });

    expect(screen.getByText('rm -rf build')).toBeInTheDocument();
    expect(screen.queryByText('Bash')).not.toBeInTheDocument();
  });

  it('shows the headline when there is no command', () => {
    renderRequest({ agent: 'a', kind: 'edit', title: 'Edit config.yaml' });

    expect(screen.getByText('Edit config.yaml')).toBeInTheDocument();
  });

  it('lists the files it names and the workspace it runs in', () => {
    renderRequest({ agent: 'a', kind: 'edit', paths: ['/work/a.py', '/work/b.py'], workspace: '/work' });

    expect(screen.getByText('/work/a.py')).toBeInTheDocument();
    expect(screen.getByText('/work/b.py')).toBeInTheDocument();
    expect(screen.getByText('/work')).toBeInTheDocument();
  });

  it('keeps what the agent passed along behind a disclosure, text and all', () => {
    renderRequest({ agent: 'a', kind: 'edit', tool_input: { file_path: '/work/a.py', new_string: 'print(1)' } });

    expect(screen.getByText(`${NAMESPACE}.details`)).toBeInTheDocument();
    expect(screen.getByText(/"new_string": "print\(1\)"/)).toBeInTheDocument();
  });

  it('adds no disclosure when the input is only the command already shown', () => {
    renderRequest({ agent: 'a', kind: 'execute', command: 'ls', tool_input: { command: 'ls' } });

    expect(screen.queryByText(`${NAMESPACE}.details`)).not.toBeInTheDocument();
  });

  it('shows nothing it was not given', () => {
    renderRequest({ agent: 'a' });

    expect(screen.queryByText(`${NAMESPACE}.paths`)).not.toBeInTheDocument();
    expect(screen.queryByText(new RegExp(`${NAMESPACE}.workspace`))).not.toBeInTheDocument();
    expect(screen.queryByText(`${NAMESPACE}.details`)).not.toBeInTheDocument();
    expect(screen.getByText(`${NAMESPACE}.refuseHint`)).toBeInTheDocument();
  });
});
