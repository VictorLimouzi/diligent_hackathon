import { useCallback, useEffect, useState } from "react";
import { errorText, request } from "./client";
export function useRequest<T>(path: string | null): {
  data?: T;
  error?: string;
  loading: boolean;
  reload: () => void;
} {
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{
    path: string | null;
    data?: T;
    error?: string;
    loading: boolean;
  }>({ path: null, loading: true });
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setState({ path, loading: !!path });
    if (path)
      request<T>(path, { signal: controller.signal })
        .then((data) => {
          if (!controller.signal.aborted)
            setState({ path, data, loading: false });
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setState({ path, error: errorText(error), loading: false });
        });
    return () => controller.abort();
  }, [path, version]);
  return { ...(state.path === path ? state : { loading: true }), reload };
}
