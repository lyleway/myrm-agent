/**
 * [INPUT]
 * - locales/{en,zh}.json (POS: 真实语言包)
 * - next-intl::createTranslator (POS: 真实的 ICU 翻译器)
 * - @/services/i18nToastService::setGlobalTranslator (POS: store / 工具函数经 translateI18nKey 取文案的全局翻译器)
 *
 * [OUTPUT]
 * - TestLocale: 测试可选的界面语言
 * - installGlobalTranslator: 以真实语言包装好全局翻译器
 *
 * [POS]
 * 前端测试共享层：断言 store / handler 经 `translateI18nKey` 交给用户的句子。翻译器走真实 ICU，语言包缺键或占位符写错会在
 * 断言处暴露；全局 mock 把 `t(key)` 当恒等函数，验证不了这一点。
 */
import { vi } from 'vitest';
import type { AbstractIntlMessages } from 'next-intl';

import { setGlobalTranslator } from '@/services/i18nToastService';
import en from '../../locales/en.json';
import zh from '../../locales/zh.json';

export type TestLocale = 'en' | 'zh';

type GlobalTranslator = Parameters<typeof setGlobalTranslator>[0];

const BUNDLES: Record<TestLocale, AbstractIntlMessages> = { en, zh };

/** Make `translateI18nKey` speak `locale`; the next call switches the language again. */
export async function installGlobalTranslator(locale: TestLocale): Promise<void> {
  // The global test setup replaces next-intl, so the real translator is loaded explicitly.
  const { createTranslator } = await vi.importActual<typeof import('next-intl')>('next-intl');
  // next-intl types the keys of a translator by its bundle, which an `AbstractIntlMessages` bundle leaves empty; the
  // global translator takes any key string and the bundle decides at runtime, as the app's own `useTranslations()` does.
  setGlobalTranslator(createTranslator({ locale, messages: BUNDLES[locale] }) as unknown as GlobalTranslator);
}
