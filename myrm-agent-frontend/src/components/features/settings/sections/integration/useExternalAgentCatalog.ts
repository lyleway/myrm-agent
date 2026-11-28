/**
 * [INPUT]
 * - @/services/external-agents::getExternalAgentCatalog, ExternalAgentContract (POS: the contract of every agent the app can offer)
 *
 * [OUTPUT]
 * - useExternalAgentCatalog: the agents the app can offer, none until the server has answered
 *
 * [POS]
 * The known agents of the external agent settings. The catalog is advisory, so a failed read leaves the form without
 * presets and without a command it recognises instead of blocking the settings page.
 */

import { useEffect, useState } from 'react';

import { getExternalAgentCatalog, type ExternalAgentContract } from '@/services/external-agents';

export function useExternalAgentCatalog(): readonly ExternalAgentContract[] {
  const [catalog, setCatalog] = useState<readonly ExternalAgentContract[]>([]);

  useEffect(() => {
    getExternalAgentCatalog().then(setCatalog, () => {
      // Advisory: the form offers no preset and knows no command.
    });
  }, []);

  return catalog;
}
