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
    throw new Error(`API ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}
