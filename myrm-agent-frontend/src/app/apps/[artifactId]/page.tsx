import { AppHostView } from '@/components/features/app-host/AppHostView';

export default async function AppPage({ params }: { params: Promise<{ artifactId: string }> }) {
  const { artifactId } = await params;
  return <AppHostView artifactId={artifactId} />;
}
