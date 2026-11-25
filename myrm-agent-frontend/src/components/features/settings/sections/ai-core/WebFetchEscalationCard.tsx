/**
 * Web fetch remote escalation (L4) settings — collapsible block under Search settings.
 */

'use client';

import { memo, useState, useEffect, useCallback, useRef } from 'react';
import { useTranslations } from 'next-intl';
import { ChevronDown } from 'lucide-react';
import { Switch } from '@/components/primitives/switch';
import { Button } from '@/components/primitives/button';
import { IconGlobe, IconLoader } from '@/components/features/icons/PremiumIcons';
import { cn } from '@/lib/utils/classnameUtils';
import { getConfigSyncManager, type WebFetchEscalationConfigValue } from '@/services/config';
import { toast } from '@/hooks/shared/useToast';
import { apiRequest } from '@/lib/api';

const DEFAULT_CONFIG: WebFetchEscalationConfigValue = {
  enabled: false,
  jinaApiKey: null,
  firecrawl: {
    inheritFromSearch: true,
    api_key: null,
    apiBase: null,
    networkIsolationConfirmed: false,
  },
  sessionCap: 5,
};

type DebouncedField = 'jina' | 'firecrawlKey' | 'firecrawlBase';

const WebFetchEscalationCard = memo(() => {
  const t = useTranslations('settings.webFetchEscalation');
  const [expanded, setExpanded] = useState(false);
  const [config, setConfig] = useState<WebFetchEscalationConfigValue>(DEFAULT_CONFIG);
  const configRef = useRef<WebFetchEscalationConfigValue>(DEFAULT_CONFIG);
  const [jinaKeyText, setJinaKeyText] = useState('');
  const [firecrawlKeyText, setFirecrawlKeyText] = useState('');
  const [firecrawlBaseText, setFirecrawlBaseText] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [verifying, setVerifying] = useState<'jina' | 'firecrawl' | null>(null);
  const debounceTimersRef = useRef<Partial<Record<DebouncedField, ReturnType<typeof setTimeout>>>>({});

  useEffect(() => {
    try {
      const syncManager = getConfigSyncManager();
      const record = syncManager.get('webFetchEscalation');
      if (record) {
        const loaded: WebFetchEscalationConfigValue = {
          ...DEFAULT_CONFIG,
          ...(record as WebFetchEscalationConfigValue),
          firecrawl: {
            ...DEFAULT_CONFIG.firecrawl,
            ...(record as WebFetchEscalationConfigValue).firecrawl,
          },
        };
        configRef.current = loaded;
        setConfig(loaded);
        setJinaKeyText(loaded.jinaApiKey ?? '');
        setFirecrawlKeyText(loaded.firecrawl.api_key ?? '');
        setFirecrawlBaseText(loaded.firecrawl.apiBase ?? '');
      }
    } catch {
      // Not yet configured
    } finally {
      setIsLoading(false);
    }
    const timers = debounceTimersRef.current;
    return () => {
      for (const timer of Object.values(timers)) {
        if (timer !== undefined) {
          clearTimeout(timer);
        }
      }
    };
  }, []);

  const persist = useCallback(
    (patch: Partial<WebFetchEscalationConfigValue>) => {
      const current = configRef.current;
      const newConfig: WebFetchEscalationConfigValue = {
        ...current,
        ...patch,
        firecrawl: patch.firecrawl ? { ...current.firecrawl, ...patch.firecrawl } : current.firecrawl,
      };
      configRef.current = newConfig;
      setConfig(newConfig);
      try {
        const syncManager = getConfigSyncManager();
        syncManager.set('webFetchEscalation', {
          enabled: newConfig.enabled,
          jinaApiKey: newConfig.jinaApiKey,
          firecrawl: {
            inheritFromSearch: newConfig.firecrawl.inheritFromSearch,
            api_key: newConfig.firecrawl.api_key,
            apiBase: newConfig.firecrawl.apiBase,
            networkIsolationConfirmed: Boolean(newConfig.firecrawl.networkIsolationConfirmed),
          },
          sessionCap: newConfig.sessionCap,
        });
      } catch {
        toast({ title: t('saveFailed'), variant: 'destructive' });
      }
    },
    [t],
  );

  const scheduleFieldSave = useCallback((field: DebouncedField, save: () => void) => {
    const existing = debounceTimersRef.current[field];
    if (existing !== undefined) {
      clearTimeout(existing);
    }
    debounceTimersRef.current[field] = setTimeout(() => {
      delete debounceTimersRef.current[field];
      save();
    }, 500);
  }, []);

  const hasCustomFirecrawlBase = firecrawlBaseText.trim().length > 0;
  const effectiveFirecrawlInherit = !hasCustomFirecrawlBase && config.firecrawl.inheritFromSearch;

  const handleVerify = async (provider: 'jina' | 'firecrawl') => {
    setVerifying(provider);
    try {
      const apiKey = provider === 'jina' ? jinaKeyText.trim() || null : firecrawlKeyText.trim() || null;
      await apiRequest('/integrations/web-fetch/verify', {
        method: 'POST',
        body: JSON.stringify({
          provider,
          api_key: apiKey,
          inherit_from_search: provider === 'firecrawl' && effectiveFirecrawlInherit,
          api_base: provider === 'firecrawl' ? firecrawlBaseText.trim() || null : null,
          network_isolation_confirmed: provider === 'firecrawl' && Boolean(config.firecrawl.networkIsolationConfirmed),
        }),
      });
      toast({ title: t('verifySuccess', { provider: provider.toUpperCase() }) });
    } catch (err) {
      toast({
        title: t('verifyFailed'),
        description: err instanceof Error ? err.message : undefined,
        variant: 'destructive',
      });
    } finally {
      setVerifying(null);
    }
  };

  if (isLoading) {
    return null;
  }

  return (
    <section className="rounded-2xl border border-border/60 bg-card/40 overflow-hidden">
      <div className="flex w-full items-center justify-between gap-3 px-5 py-4 hover:bg-muted/30 transition-colors">
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="flex flex-1 items-center gap-3 min-w-0 text-left"
        >
          <IconGlobe className="h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-foreground">{t('title')}</h3>
            <p className="text-xs text-muted-foreground truncate">{t('subtitle')}</p>
          </div>
        </button>
        <div className="flex items-center gap-3 shrink-0">
          <Switch checked={config.enabled} onCheckedChange={(v) => persist({ enabled: v })} />
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={t('title')}
            onClick={() => setExpanded((v) => !v)}
            className="p-0.5 text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronDown className={cn('h-4 w-4 transition-transform', expanded && 'rotate-180')} />
          </button>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-border/50 px-5 py-4 space-y-4">
          <p className="text-xs leading-relaxed text-muted-foreground">{t('description')}</p>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t('jinaApiKey')}</label>
            <input
              type="password"
              placeholder={t('jinaApiKeyPlaceholder')}
              value={jinaKeyText}
              onChange={(e) => {
                const nextValue = e.target.value;
                setJinaKeyText(nextValue);
                scheduleFieldSave('jina', () => {
                  persist({ jinaApiKey: nextValue.trim() || null });
                });
              }}
              className="w-full bg-background font-mono text-xs rounded-lg border border-border px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring/50"
            />
            <p className="text-[10px] text-muted-foreground">{t('jinaApiKeyHint')}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={verifying === 'jina'}
              onClick={() => void handleVerify('jina')}
              className="mt-1"
            >
              {verifying === 'jina' ? <IconLoader className="h-3 w-3 animate-spin" /> : null}
              {t('verifyJina')}
            </Button>
          </div>

          <div className="space-y-2 rounded-xl border border-border/40 p-3">
            <p className="text-[10px] text-muted-foreground">{t('firecrawlKeylessHint')}</p>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-medium text-muted-foreground">{t('firecrawlInherit')}</span>
              <Switch
                checked={effectiveFirecrawlInherit}
                disabled={hasCustomFirecrawlBase}
                onCheckedChange={(v) =>
                  persist({ firecrawl: { ...configRef.current.firecrawl, inheritFromSearch: v } })
                }
              />
            </div>
            {hasCustomFirecrawlBase && (
              <p className="text-[10px] text-muted-foreground">{t('firecrawlCustomBaseKeyHint')}</p>
            )}
            {(!config.firecrawl.inheritFromSearch || hasCustomFirecrawlBase) && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t('firecrawlApiKey')}</label>
                <input
                  type="password"
                  placeholder={t('firecrawlApiKeyPlaceholder')}
                  value={firecrawlKeyText}
                  onChange={(e) => {
                    const nextValue = e.target.value;
                    setFirecrawlKeyText(nextValue);
                    scheduleFieldSave('firecrawlKey', () => {
                      persist({
                        firecrawl: { ...configRef.current.firecrawl, api_key: nextValue.trim() || null },
                      });
                    });
                  }}
                  className="w-full bg-background font-mono text-xs rounded-lg border border-border px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring/50"
                />
              </div>
            )}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t('firecrawlApiBase')}</label>
              <input
                type="text"
                placeholder="https://api.firecrawl.dev"
                value={firecrawlBaseText}
                onChange={(e) => {
                  const nextBase = e.target.value;
                  setFirecrawlBaseText(nextBase);
                  scheduleFieldSave('firecrawlBase', () => {
                    const trimmed = nextBase.trim() || null;
                    persist({
                      firecrawl: {
                        ...configRef.current.firecrawl,
                        apiBase: trimmed,
                        inheritFromSearch: trimmed ? false : configRef.current.firecrawl.inheritFromSearch,
                        networkIsolationConfirmed: trimmed
                          ? Boolean(configRef.current.firecrawl.networkIsolationConfirmed)
                          : false,
                      },
                    });
                  });
                }}
                className="w-full bg-background font-mono text-xs rounded-lg border border-border px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring/50"
              />
              <p className="text-[10px] text-muted-foreground">{t('firecrawlApiBaseHint')}</p>
              {hasCustomFirecrawlBase && (
                <label className="flex items-start gap-2 pt-1 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={Boolean(config.firecrawl.networkIsolationConfirmed)}
                    onChange={(e) =>
                      persist({
                        firecrawl: {
                          ...configRef.current.firecrawl,
                          networkIsolationConfirmed: e.target.checked,
                        },
                      })
                    }
                    className="mt-0.5 h-3.5 w-3.5 rounded border-border accent-primary"
                  />
                  <span className="space-y-0.5">
                    <span className="block text-xs font-medium text-foreground">{t('firecrawlNetworkIsolation')}</span>
                    <span className="block text-[10px] text-muted-foreground">
                      {t('firecrawlNetworkIsolationHint')}
                    </span>
                  </span>
                </label>
              )}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={verifying === 'firecrawl'}
              onClick={() => void handleVerify('firecrawl')}
            >
              {verifying === 'firecrawl' ? <IconLoader className="h-3 w-3 animate-spin" /> : null}
              {t('verifyFirecrawl')}
            </Button>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">{t('sessionCap')}</label>
            <input
              type="number"
              min={1}
              max={50}
              value={config.sessionCap}
              onChange={(e) => {
                const parsed = Number.parseInt(e.target.value, 10);
                if (!Number.isNaN(parsed)) {
                  persist({ sessionCap: Math.min(50, Math.max(1, parsed)) });
                }
              }}
              className="w-24 bg-background text-xs rounded-lg border border-border px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring/50"
            />
            <p className="text-[10px] text-muted-foreground">{t('sessionCapHint')}</p>
          </div>
        </div>
      )}
    </section>
  );
});

WebFetchEscalationCard.displayName = 'WebFetchEscalationCard';

export default WebFetchEscalationCard;
