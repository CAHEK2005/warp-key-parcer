export type ApiOptions = {
  token?: string;
  baseUrl?: string;
};

export async function apiFetch<T>(path: string, options: RequestInit & ApiOptions = {}): Promise<T> {
  const { token, baseUrl = "/api", headers, ...request } = options;
  const response = await fetch(`${baseUrl}${path}`, {
    ...request,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const payload = JSON.parse(text) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      }
    } catch {
      message = text;
    }
    throw new Error(`API ${response.status}: ${message}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}
