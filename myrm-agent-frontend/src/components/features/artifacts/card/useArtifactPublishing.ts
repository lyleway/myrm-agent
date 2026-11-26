/**
 * [INPUT]
 * - ../artifactUtils (POS: deploy preflight, publication patching helpers)
 * - ../PublishModal (POS: PublishedArtifactUpdate)
 * - @/lib/api (POS: getApiUrl)
 * - @/services/hosting (POS: ArtifactPublication)
 * - @/store/chat/types (POS: Artifact)
 *
 * [OUTPUT]
 * useArtifactPublishing, ArtifactPublishing.
 *
 * [POS]
 * Everything the artifact card knows about publishing: the artifact as last seen by the
 * server, its publications, whether it can be deployed, and the publish dialog state.
 */

import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { getApiUrl } from '@/lib/api';
import type { ArtifactPublication } from '@/services/hosting';
import type { Artifact } from '@/store/chat/types';
import {
  fetchArtifactDeployPreflight,
  isDeployCandidateArtifactType,
  patchArtifactPublicationsInChat,
  publicationsChanged,
  type ArtifactDeployPreflight,
} from '../artifactUtils';
import type { PublishedArtifactUpdate } from '../PublishModal';

export interface ArtifactPublishing {
  /** The artifact as given, plus what the server reported later (latest version). */
  current: Artifact;
  publications: ArtifactPublication[];
  isDeployCandidate: boolean;
  deployPreflight: ArtifactDeployPreflight | null;
  canDeploy: boolean;
  modalOpen: boolean;
  modalTargetId: string | undefined;
  openDeploy: () => void;
  openRedeploy: (targetId: string) => void;
  closeModal: () => void;
  handlePublished: (update: PublishedArtifactUpdate) => void;
}

interface HydratedArtifact {
  latest_version_id?: string | null;
  publications?: ArtifactPublication[];
}

export function useArtifactPublishing(artifact: Artifact): ArtifactPublishing {
  const isDeployCandidate = isDeployCandidateArtifactType(artifact.type);

  // A new `artifact` prop restarts the card's view of it.
  const [current, setCurrent] = useState(artifact);
  const [currentSource, setCurrentSource] = useState(artifact);
  if (currentSource !== artifact) {
    setCurrentSource(artifact);
    setCurrent(artifact);
  }

  // Publications only follow the prop when it actually carries some.
  const [publications, setPublications] = useState<ArtifactPublication[]>(artifact.publications ?? []);
  const [publicationsSource, setPublicationsSource] = useState(artifact.publications);
  if (publicationsSource !== artifact.publications) {
    setPublicationsSource(artifact.publications);
    if (artifact.publications?.length) {
      setPublications(artifact.publications);
    }
  }

  const [loadedPreflight, setLoadedPreflight] = useState<ArtifactDeployPreflight | null>(null);
  const deployPreflight = isDeployCandidate ? loadedPreflight : null;

  useEffect(() => {
    if (!isDeployCandidate) {
      return;
    }
    let cancelled = false;
    // The preflight only tunes the button; an unreachable server reads as "not deployable".
    void fetchArtifactDeployPreflight(artifact.id)
      .catch(() => null)
      .then((result) => {
        if (!cancelled) {
          setLoadedPreflight(result);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.id, isDeployCandidate]);

  useEffect(() => {
    if (!isDeployCandidate) {
      return;
    }
    let cancelled = false;

    const hydrate = async () => {
      try {
        const response = await fetch(getApiUrl(`/files/artifacts/${artifact.id}`));
        if (!response.ok || cancelled) {
          return;
        }
        const data = (await response.json()) as HydratedArtifact;
        if (cancelled) {
          return;
        }
        const fresh = data.publications;
        if (fresh) {
          setPublications((prev) => {
            if (!publicationsChanged(prev, fresh)) {
              return prev;
            }
            patchArtifactPublicationsInChat(artifact.id, fresh);
            return fresh;
          });
        }
        if (data.latest_version_id) {
          setCurrent((prev) => ({ ...prev, latest_version_id: data.latest_version_id }));
        }
      } catch {
        // Hydration is best-effort: the card still works from the props it was given.
      }
    };

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [artifact.id, isDeployCandidate]);

  const [modalOpen, setModalOpen] = useState(false);
  const [modalTargetId, setModalTargetId] = useState<string | undefined>();

  const openDeploy = () => {
    if (deployPreflight && !deployPreflight.deployable) {
      toast.error(deployPreflight.message, { description: deployPreflight.hint ?? undefined });
      return;
    }
    setModalTargetId(undefined);
    setModalOpen(true);
  };

  const openRedeploy = (targetId: string) => {
    setModalTargetId(targetId);
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setModalTargetId(undefined);
  };

  const handlePublished = (update: PublishedArtifactUpdate) => {
    setPublications(update.publications);
    patchArtifactPublicationsInChat(artifact.id, update.publications);
    if (update.latest_version_id) {
      setCurrent((prev) => ({ ...prev, latest_version_id: update.latest_version_id }));
    }
    void import('@/lib/progression/tryMarkMilestone').then(({ tryMarkMilestone }) => {
      tryMarkMilestone('first_multistep_delivery');
    });
  };

  return {
    current,
    publications,
    isDeployCandidate,
    deployPreflight,
    canDeploy: isDeployCandidate && deployPreflight?.deployable === true,
    modalOpen,
    modalTargetId,
    openDeploy,
    openRedeploy,
    closeModal,
    handlePublished,
  };
}
