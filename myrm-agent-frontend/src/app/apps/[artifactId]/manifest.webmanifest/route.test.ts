import { describe, expect, it } from 'vitest';
import { GET } from './route';

const request = (name?: string) =>
  new Request(`https://app.example.com/apps/art-1/manifest.webmanifest${name === undefined ? '' : `?name=${encodeURIComponent(name)}`}`);
const params = (artifactId: string) => ({ params: Promise.resolve({ artifactId }) });

describe('app manifest route', () => {
  it('serves the app’s manifest as a manifest', async () => {
    const response = await GET(request('Weather'), params('art-1'));

    expect(response.status).toBe(200);
    expect(response.headers.get('content-type')).toBe('application/manifest+json');
    expect(response.headers.get('x-content-type-options')).toBe('nosniff');
    expect(await response.json()).toMatchObject({ id: '/apps/art-1/', name: 'Weather', scope: '/apps/art-1/' });
  });

  it('uses the product name when the address carries none', async () => {
    const response = await GET(request(), params('art-1'));

    expect((await response.json()).name).toBe('MyrmAgent');
  });

  it('answers 404 for an id that cannot be an app, with nothing in the body', async () => {
    const response = await GET(request('Weather'), params('../etc'));

    expect(response.status).toBe(404);
    expect(await response.text()).toBe('');
  });
});
