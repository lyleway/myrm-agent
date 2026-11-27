/** @vitest-environment jsdom */
import { useState } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAccount } from '@/services/external-agents';

const stableT = (key: string, values?: { name?: string }) => (values?.name ? `${key}:${values.name}` : key);

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  remove: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/lib/api', () => ({
  ApiError: class ApiError extends Error {
    code: number;
    constructor(message: string, code: number) {
      super(message);
      this.code = code;
    }
  },
}));
vi.mock('@/services/external-agents', () => ({
  createExternalAgentAccount: mocks.create,
  deleteExternalAgentAccount: mocks.remove,
}));

import { ApiError } from '@/lib/api';
import ExternalAgentAccountField from '../ExternalAgentAccountField';

const account = (backend: string, name: string): ExternalAgentAccount => ({
  backend,
  name,
  authenticated: false,
  authStatus: 'not_authenticated',
});

/** Plays the editor around the field: the chosen account lives in the parent, as it does in the form. */
const renderField = ({
  initial,
  accounts = [],
}: { initial?: string; accounts?: readonly ExternalAgentAccount[] } = {}) => {
  const onChange = vi.fn();
  const onCreated = vi.fn();
  const onDeleted = vi.fn();
  const Parent = () => {
    const [value, setValue] = useState<string | undefined>(initial);
    return (
      <ExternalAgentAccountField
        backend="claude"
        value={value}
        accounts={accounts}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
        onCreated={onCreated}
        onDeleted={onDeleted}
      />
    );
  };
  render(<Parent />);
  return { onChange, onCreated, onDeleted };
};

const picker = () => screen.getByRole('combobox', { name: 'account' });

const openPicker = async () => {
  await userEvent.click(picker());
};

const choose = async (name: string) => {
  await openPicker();
  await userEvent.click(screen.getByRole('option', { name }));
};

const startNewAccount = () => choose('accountNew');

const typeName = (name: string) => userEvent.type(screen.getByLabelText('accountNameLabel'), name);

const createButton = () => screen.getByRole('button', { name: 'accountCreate' });

describe('ExternalAgentAccountField', () => {
  beforeEach(() => {
    mocks.create.mockReset();
    mocks.remove.mockReset();
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  describe('choosing', () => {
    it('offers the login of this machine, the accounts of this agent only, and a new account', async () => {
      renderField({ accounts: [account('claude', 'work'), account('codex', 'other'), account('claude', 'home')] });

      await openPicker();

      expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
        'accountDefault',
        'work',
        'home',
        'accountNew',
      ]);
    });

    it('keeps an account that this machine has not listed selectable, so a synced setting is not lost', async () => {
      renderField({ initial: 'synced' });

      expect(picker()).toHaveTextContent('synced');
      await openPicker();

      expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual([
        'accountDefault',
        'synced',
        'accountNew',
      ]);
    });

    it('reports the chosen account, and none when the login of this machine is chosen again', async () => {
      const { onChange } = renderField({ accounts: [account('claude', 'work')] });

      await choose('work');
      expect(onChange).toHaveBeenLastCalledWith('work');

      await choose('accountDefault');
      expect(onChange).toHaveBeenLastCalledWith(undefined);
    });
  });

  describe('creating', () => {
    it('makes the account on the server first, then chooses it', async () => {
      mocks.create.mockResolvedValue(account('claude', 'work'));
      const { onChange, onCreated } = renderField();

      await startNewAccount();
      expect(onChange).not.toHaveBeenCalled();
      await typeName('work');
      await userEvent.click(createButton());

      expect(mocks.create).toHaveBeenCalledWith('claude', 'work');
      expect(onCreated).toHaveBeenCalledTimes(1);
      expect(onChange).toHaveBeenCalledWith('work');
      expect(screen.queryByLabelText('accountNameLabel')).not.toBeInTheDocument();
      expect(picker()).toHaveTextContent('work');
    });

    it('creates with the Enter key', async () => {
      mocks.create.mockResolvedValue(account('claude', 'work'));
      renderField();

      await startNewAccount();
      await typeName('work{Enter}');

      expect(mocks.create).toHaveBeenCalledWith('claude', 'work');
    });

    it('does not offer to create a name the server would refuse', async () => {
      renderField();
      await startNewAccount();

      expect(createButton()).toBeDisabled();

      await typeName('Work');
      expect(createButton()).toBeDisabled();
      expect(screen.getByText('accountNameHint')).toHaveClass('text-red-500');

      await userEvent.clear(screen.getByLabelText('accountNameLabel'));
      await typeName('work');
      expect(createButton()).toBeEnabled();
      expect(screen.getByText('accountNameHint')).not.toHaveClass('text-red-500');
    });

    it('does not ask the server for a refused name when Enter is pressed', async () => {
      renderField();
      await startNewAccount();

      await typeName('Work{Enter}');

      expect(mocks.create).not.toHaveBeenCalled();
    });

    it('says so when the name is taken, and keeps the form open with the name', async () => {
      mocks.create.mockRejectedValue(new ApiError('exists', 409));
      const { onChange, onCreated } = renderField();

      await startNewAccount();
      await typeName('work');
      await userEvent.click(createButton());

      expect(mocks.toast.error).toHaveBeenCalledWith('accountExists');
      expect(onCreated).not.toHaveBeenCalled();
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getByLabelText('accountNameLabel')).toHaveValue('work');
    });

    it('reports any other failure as a failure to create', async () => {
      mocks.create.mockRejectedValue(new Error('boom'));
      const { onChange } = renderField();

      await startNewAccount();
      await typeName('work');
      await userEvent.click(createButton());

      expect(mocks.toast.error).toHaveBeenCalledWith('accountCreateFailed');
      expect(onChange).not.toHaveBeenCalled();
    });

    it('drops a half-typed name when creating is cancelled', async () => {
      renderField();
      await startNewAccount();
      await typeName('wor');

      await userEvent.click(screen.getByRole('button', { name: 'cancel' }));
      expect(screen.queryByLabelText('accountNameLabel')).not.toBeInTheDocument();

      await startNewAccount();
      expect(screen.getByLabelText('accountNameLabel')).toHaveValue('');
    });
  });

  describe('deleting', () => {
    const deleteButton = () => screen.getByRole('button', { name: 'accountDelete' });

    it('is offered only for a chosen account', () => {
      renderField();

      expect(screen.queryByRole('button', { name: 'accountDelete' })).not.toBeInTheDocument();
    });

    it('asks first, then removes the account and sends the agent back to the login of this machine', async () => {
      mocks.remove.mockResolvedValue(undefined);
      const { onChange, onDeleted } = renderField({ initial: 'work', accounts: [account('claude', 'work')] });

      await userEvent.click(deleteButton());
      const dialog = await screen.findByRole('alertdialog');
      expect(within(dialog).getByText('accountDeleteConfirm:work')).toBeInTheDocument();
      expect(mocks.remove).not.toHaveBeenCalled();

      await userEvent.click(within(dialog).getByRole('button', { name: 'delete' }));

      expect(mocks.remove).toHaveBeenCalledWith('claude', 'work');
      expect(mocks.toast.success).toHaveBeenCalledWith('accountDeleted');
      expect(onChange).toHaveBeenLastCalledWith(undefined);
      expect(onDeleted).toHaveBeenCalledWith('claude', 'work');
    });

    it('removes nothing when the question is answered with cancel', async () => {
      const { onChange, onDeleted } = renderField({ initial: 'work', accounts: [account('claude', 'work')] });

      await userEvent.click(deleteButton());
      await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'cancel' }));

      expect(mocks.remove).not.toHaveBeenCalled();
      expect(onChange).not.toHaveBeenCalled();
      expect(onDeleted).not.toHaveBeenCalled();
    });

    it('counts an account that is already gone as removed', async () => {
      mocks.remove.mockRejectedValue(new ApiError('gone', 404));
      const { onChange, onDeleted } = renderField({ initial: 'work' });

      await userEvent.click(deleteButton());
      await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'delete' }));

      expect(mocks.toast.success).toHaveBeenCalledWith('accountDeleted');
      expect(onChange).toHaveBeenLastCalledWith(undefined);
      expect(onDeleted).toHaveBeenCalledWith('claude', 'work');
    });

    it('keeps the account and says so when the server could not remove it', async () => {
      mocks.remove.mockRejectedValue(new ApiError('boom', 500));
      const { onChange, onDeleted } = renderField({ initial: 'work' });

      await userEvent.click(deleteButton());
      await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'delete' }));

      expect(mocks.toast.error).toHaveBeenCalledWith('accountDeleteFailed');
      expect(mocks.toast.success).not.toHaveBeenCalled();
      expect(onChange).not.toHaveBeenCalled();
      expect(onDeleted).not.toHaveBeenCalled();
      expect(picker()).toHaveTextContent('work');
    });
  });
});
