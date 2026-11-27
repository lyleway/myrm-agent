import { buildAppManifest } from '@/lib/app-manifest';

/**
 * The web app manifest of one installed app (`/apps/<id>/manifest.webmanifest?name=...`). Served without a session
 * because browsers fetch manifests without credentials (`middleware.ts` lets exactly this path through), so it carries
 * only what the address supplies; see `lib/app-manifest.ts`.
 */
export async function GET(request: Request, { params }: { params: Promise<{ artifactId: string }> }) {
  const { artifactId } = await params;
  const manifest = buildAppManifest(artifactId, new URL(request.url).searchParams.get('name'));
  if (!manifest) {
    return new Response(null, { status: 404 });
  }
  return new Response(JSON.stringify(manifest), {
    headers: {
      'Content-Type': 'application/manifest+json',
      // The name is in the address, so a rename is a new address and the response can be cached.
      'Cache-Control': 'public, max-age=3600',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
