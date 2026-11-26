import { AppHostView } from '@/components/features/app-host/AppHostView';

export default async function AppPage({ params }: { params: Promise<{ artifactId: string }> }) {
  const { artifactId } = await params;
  return (
    <div className="h-[calc(100vh-4rem)] w-full">
      <AppHostView artifactId={artifactId} />
    </div>
  );
}
