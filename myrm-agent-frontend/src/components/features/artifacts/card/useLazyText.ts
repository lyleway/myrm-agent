/**
 * [INPUT]
 * - react (POS: state and effect primitives)
 *
 * [OUTPUT]
 * useLazyText.
 *
 * [POS]
 * Loads a piece of text once, the first time `enabled` turns true. A load that
 * returns nothing is a failure and is not retried until `enabled` goes false and true
 * again. Turning `enabled` off keeps loaded text so reopening is instant.
 */

import { useEffect, useState } from 'react';

interface LazyText {
  text: string | null;
  loading: boolean;
  setText: (text: string | null) => void;
}

export function useLazyText(enabled: boolean, load: () => Promise<string | null>): LazyText {
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  // A failure only blocks retries while the consumer stays enabled.
  if (!enabled && failed) {
    setFailed(false);
  }

  const pending = enabled && text === null && !failed;

  useEffect(() => {
    if (!pending) {
      return;
    }
    let cancelled = false;
    void load().then((loaded) => {
      if (cancelled) {
        return;
      }
      if (loaded === null) {
        setFailed(true);
      } else {
        setText(loaded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [pending, load]);

  return { text, loading: pending, setText };
}
