import vectors from '@shared/config/mcp_server_identity.json';
import { describe, expect, it } from 'vitest';

import { findSameMcpServer, partitionByMcpIdentity, uniqueMcpName } from '@/lib/utils/mcp-config';

import { mcpServerIdentity } from '../mcpServerIdentity';

/** The segment every ASCII name had before the hash suffix existed, written out independently of the module. */
const legacyComponent = (name: string): string =>
  name
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_{2,}/g, '_')
    .replace(/^_+|_+$/g, '') || 'unnamed';

const seededRandom = (seed: number) => () => {
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed / 2 ** 32;
};

const randomName = (random: () => number, alphabet: readonly string[]): string =>
  Array.from({ length: Math.floor(random() * 25) }, () => alphabet[Math.floor(random() * alphabet.length)]).join('');

const ASCII_ALPHABET = Array.from({ length: 95 }, (_, index) => String.fromCharCode(32 + index)).concat('\t', '\n');
const UNICODE_ALPHABET = [...ASCII_ALPHABET, ...'天气我的服务日本語サーバー서버😀é\u0301ß\u200b', '\ud800', '\udfff'];

describe('mcpServerIdentity', () => {
  it.each(vectors)('共享向量：$name 的身份是 $identity', ({ name, identity }) => {
    expect(mcpServerIdentity(name)).toBe(identity);
  });

  it('向量里只有 ASCII 分隔符变体共享同一身份', () => {
    const namesByIdentity = new Map<string, Set<string>>();
    for (const { name, identity } of vectors) {
      namesByIdentity.set(identity, (namesByIdentity.get(identity) ?? new Set()).add(name.normalize('NFC')));
    }

    const shared = [...namesByIdentity]
      .filter(([, names]) => names.size > 1)
      .map(([identity, names]) => [identity, [...names].sort()]);

    expect(shared).toEqual([['my_api', ['my api', 'my-api', 'my_api']]]);
  });

  it('折叠后非空的 ASCII 名字保持它一直以来的身份', () => {
    const random = seededRandom(20261127);
    for (let round = 0; round < 3000; round += 1) {
      const name = randomName(random, ASCII_ALPHABET);
      // 折叠结果只由字母数字撑起：下划线与其它符号都会在两端被修剪掉。
      if (/[A-Za-z0-9]/.test(name)) {
        expect(mcpServerIdentity(name), JSON.stringify(name)).toBe(legacyComponent(name));
      }
    }
  });

  it('任何名字的身份都不为空，也不含工具名分隔符', () => {
    const random = seededRandom(20261128);
    for (let round = 0; round < 3000; round += 1) {
      const name = randomName(random, UNICODE_ALPHABET);
      const identity = mcpServerIdentity(name);

      expect(identity, JSON.stringify(name)).toMatch(/^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*$/);
    }
  });

  it('预组合与分解两种写法是同一个服务', () => {
    expect(mcpServerIdentity('caf\u00e9')).toBe(mcpServerIdentity('cafe\u0301'));
  });
});

describe('findSameMcpServer', () => {
  const servers = [{ name: 'github' }, { name: 'my_api' }, { name: '天气' }];

  it('返回与之同身份的已有服务器本身', () => {
    expect(findSameMcpServer(servers, 'my-api')).toBe(servers[1]);
    expect(findSameMcpServer(servers, 'github')).toBe(servers[0]);
    expect(findSameMcpServer(servers, '天气')).toBe(servers[2]);
  });

  it('大小写不同的名字、不同的中文名字都不冲突', () => {
    expect(findSameMcpServer(servers, 'GitHub')).toBeUndefined();
    expect(findSameMcpServer(servers, '我的服务')).toBeUndefined();
  });

  it('没有已有服务器时没有冲突', () => {
    expect(findSameMcpServer([], 'github')).toBeUndefined();
  });
});

describe('partitionByMcpIdentity', () => {
  it('按顺序跳过与已有服务或同批前一个同身份的服务', () => {
    const incoming = ['GitHub', 'github', 'a-b', 'a_b', '天气', '我的服务'].map((name) => ({ name }));

    const { accepted, skipped } = partitionByMcpIdentity(incoming, ['github']);

    expect(accepted.map((server) => server.name)).toEqual(['GitHub', 'a-b', '天气', '我的服务']);
    expect(skipped.map((server) => server.name)).toEqual(['github', 'a_b']);
  });

  it('原样返回传入的对象', () => {
    const server = { name: 'docs', url: 'https://docs.example.com/mcp' };

    expect(partitionByMcpIdentity([server], []).accepted[0]).toBe(server);
  });
});

describe('uniqueMcpName', () => {
  it('名字空闲时原样返回', () => {
    expect(uniqueMcpName('docs', ['search'])).toBe('docs');
  });

  it('名字被占用时依次尝试 -2、-3', () => {
    expect(uniqueMcpName('docs', ['docs'])).toBe('docs-2');
    expect(uniqueMcpName('docs', ['docs', 'docs-2'])).toBe('docs-3');
  });

  it('按身份判断是否被占用', () => {
    expect(uniqueMcpName('my-api', ['my_api', 'my_api_2'])).toBe('my-api-3');
  });

  it('两个中文名字各自空闲，同名的才要改', () => {
    expect(uniqueMcpName('我的服务', ['天气'])).toBe('我的服务');
    expect(uniqueMcpName('天气', ['天气'])).toBe('天气-2');
  });
});
