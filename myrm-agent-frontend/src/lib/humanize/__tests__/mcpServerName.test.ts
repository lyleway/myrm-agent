import { describe, expect, it } from 'vitest';

import { readableMcpServerName } from '../mcpServerName';

const DD4ACD_IS_TIANQI = (identity: string) => (identity === 'unnamed_dd4acd' ? '天气' : undefined);

describe('readableMcpServerName', () => {
  it('is the name the user gave the server when the settings know it', () => {
    expect(readableMcpServerName('unnamed_dd4acd', DD4ACD_IS_TIANQI)).toBe('天气');
  });

  it('prefers the name the user gave over an identity that reads', () => {
    expect(readableMcpServerName('Hello_World', () => 'Hello World')).toBe('Hello World');
  });

  it.each(['weather', 'Hello_World', 'my_very_long_server_name_for_the_weather_s_ddb65d', 'unnamed_helper'])(
    'falls back to an identity a person can read: %s',
    (identity) => {
      expect(readableMcpServerName(identity, DD4ACD_IS_TIANQI)).toBe(identity);
      expect(readableMcpServerName(identity)).toBe(identity);
    },
  );

  it.each(['unnamed', 'unnamed_dd4acd', 'unnamed_13bd86'])(
    'has no name for an identity with nothing to read: %s',
    (identity) => {
      expect(readableMcpServerName(identity, () => undefined)).toBeUndefined();
      expect(readableMcpServerName(identity)).toBeUndefined();
    },
  );

  it('has no name for an empty identity', () => {
    expect(readableMcpServerName('')).toBeUndefined();
  });
});
