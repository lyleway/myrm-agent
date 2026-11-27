/**
 * [INPUT]
 * - @/services/external-agents::listExternalAgentAccounts, ExternalAgentAccount (POS: the named accounts the server holds)
 *
 * [OUTPUT]
 * - useExternalAgentAccounts: the accounts and a way to read them again
 *
 * [POS]
 * Account list of the external agent settings. Which accounts exist and whether they are logged in is advisory, so a
 * failed read keeps the last list instead of blocking the settings page.
 */

import { useCallback, useState } from 'react';

import { listExternalAgentAccounts, type ExternalAgentAccount } from '@/services/external-agents';

export function useExternalAgentAccounts(): {
  accounts: ExternalAgentAccount[];
  refreshAccounts: () => Promise<void>;
} {
  const [accounts, setAccounts] = useState<ExternalAgentAccount[]>([]);

  const refreshAccounts = useCallback(async () => {
    try {
      setAccounts(await listExternalAgentAccounts());
    } catch {
      // Advisory: keep the last list.
    }
  }, []);

  return { accounts, refreshAccounts };
}
